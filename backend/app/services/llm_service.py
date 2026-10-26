"""LLM 服务（本地直连模式）
- 聊天推理：主模型（默认 DeepSeek，thinking 模式，SSE 流式，可配置任意 OpenAI 兼容端点）
- 看图：主模型本身就是多模态，图片直接内联进消息（不再有单独的视觉辅助模型）
"""
import base64
import asyncio
import json
import threading
from pathlib import Path

import httpx
from typing import AsyncIterator, Tuple

from ..core.config import settings
from ..core.model_config import load_model_config, get_endpoint
from ..core.paths import to_abs

# 流式整体超时 300s（thinking 模式下一次完整回答可能长达 1-2 分钟）
STREAM_TIMEOUT = httpx.Timeout(300.0, connect=15.0)

# 总结类 LLM 调用全局串行锁（视频总结 / 文档总结共用，防并发触发云接口限流）
#
# 为什么不用 asyncio.Lock：文件中心的产物管线跑在「每个文件一个独立线程 + asyncio.run」
# 里，各自有独立事件循环。asyncio.Lock 在首次使用时绑定当时的 loop，跨 loop 复用会
# 直接死锁（实测：第二个文件永远卡在 summarize=running，线程永不返回）。
# 这里用 threading.Lock 做互斥，把阻塞获取扔到线程池，不阻塞事件循环。
class CrossLoopLock:
    """可在多个事件循环 / 线程之间互斥的异步锁"""

    def __init__(self):
        self._lock = threading.Lock()

    async def __aenter__(self):
        await asyncio.to_thread(self._lock.acquire)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        try:
            self._lock.release()
        except RuntimeError:
            pass
        return False


SUMMARY_LOCK = CrossLoopLock()

# ===== 思考强度（reasoning_effort）档位 =====
# 前端提供 low/medium/high/xhigh/max 五档（对齐 Claude 语义）；本平台主模型走 DeepSeek
# （OpenAI 兼容），reasoning_effort 仅认 low / medium / high，故 xhigh/max 在发送时降级为 high。
EFFORT_LEVELS = {"low", "medium", "high", "xhigh", "max"}
# 会话级思考强度预留的默认档（会话 effort 为空时使用）
DEFAULT_EFFORT = "high"


def _map_effort(effort: str) -> str:
    """把五档思考强度映射为 DeepSeek 接受的 reasoning_effort 值"""
    e = (effort or "").strip().lower()
    if e not in EFFORT_LEVELS:
        return DEFAULT_EFFORT
    return "high" if e in ("xhigh", "max") else e

# ===== 系统提示词 =====
SYSTEM_PROMPT = """你是一个考研学习辅导AI助手，正在帮助用户学习。

## ⚠️ 公式输出铁律（必须遵守，违反则用户看到的是一堆乱码）
所有数学公式必须用 $...$（行内）或 $$...$$（独立行）包裹 LaTeX 输出。
绝对禁止使用 \(...\) 或 \[...\] 格式——这个前端不渲染，用户只能看到原始代码如 \( f'(x)=0 \)，完全无法阅读。
输出前请自行检查：如果回答中有 \ 或 \) 等符号，立即替换为 $。

正确：$y = x^5$，$y' = 5x^4$
正确：$$\frac{dy}{dx} = 5x^4$$

错误：\( y = x^5 \)
错误：\[ \frac{dy}{dx} = 5x^4 \]

## 当前情况
- 科目：{subject_name}（{subject}）
- 模式：{mode_name}（模式 {mode}）
- 视频字幕上下文：{subtitle_context}

## 主动看图（重要）
用户发送图片（草稿、笔记、题目截图）时：
1. 先认真看图片里的内容，针对图片内容回答
2. 不要泛泛而谈，要具体
3. 如果图片里有写了一半的解题过程，接着往下讲
4. 用户没说明的，主动问"你这步是什么意思？"

## 模式说明

### 模式A - 即时问答
结合字幕内容和你的考研知识回答问题。如果字幕中没有涵盖，用自己的知识回答。不确定的地方如实说明。

### 模式B - 引导输出
你的任务是引导用户自己输出答案，而不是直接给出答案。
引导策略（循序渐进）：
① 提示方向 — "你再想想这个定理的条件是什么？"
② 缩小范围 — "注意这里的关键条件是什么？"
③ 给半截答案 — "第一步应该先验证...，然后呢？"
④ 直接点破 — 只有在用户已经很接近或多次答错时，才给完整答案
出题考用户来验证 ta 是否真的理解了 —— 出题时用 quiz_user 出卡片（见下方「交互工具」），用户在卡片上作答后你批改；针对知识点的引导式追问（①~③）仍然直接用正文问。

### 模式C - 课后问答
详细、系统地回答用户的问题。

## 交互工具（要用户输入用 ask_user_question；出题考用户用 quiz_user；其余一律正文问）

**ask_user_question —— 缺信息或需要用户拍板时用**（命中任意一条就调用，别在正文里问）：
1. 缺少只有用户能提供的信息：题干/图片/草稿没发过来、目标院校、基础水平、每天可用时间
2. 需要用户拍板才能继续：先讲哪一道、用哪种解法、往哪个方向复习
3. 用户要求改变交互方式（如"直接告诉我答案""别问了""换个方式讲"）——继续引导还是照办由用户选，不要自己顶回去
4. 同一句话有多种合理读法、且不同读法结果差别大
自检：正文里出现"请把题目发我""麻烦你把…发过来"这类索要信息的句子，就是该调用工具的信号。

**quiz_user —— 出题考用户时用（模式B 重点，模式A 用户要也照做）**：把练习题做成卡片让用户作答，提交后你拿到他的答案，接着批改。
- 触发词："出几道题""考考我""测试一下我""来点练习""我要练手""出题" —— 一律用 quiz_user，哪怕在模式A
- 题干写进 question；选项写进 options（label 写完整内容，不要加 "A/B/C/D" 前缀）
- 选择题/判断题：选项就是答案（别把正确项标出来），用户点选即可；需要说明的选项用 description 一句话讲清
- 计算题/证明题：不要 options，题干写 question，用户在卡片输入框里写答案
- 单选不写 multi_select；多选写 "multi_select": true
- 一次 1~3 题；想逐题作答、逐题批改就一次 1 题（推荐）
- 别反问范围：先拿当前视频内容（字幕/上下文）出题；模式B 出与当前考点同源、换数换条件的变式题最合适。只有上下文真的空（无字幕且用户也没说范围）才值得先问一句
- 用户提交后：直接判对错 + 讲清为什么（对的为什么对、错的错在哪），再出下一题或按用户要求继续
- 卡片不渲染 Markdown（** 会原样显示），题干与选项只用纯文本 + $...$ 公式

**判据（三选一，别混）**：用户**要提交一次作答、你要判对错**（含一切"出题/考考我"）→ quiz_user；用户**要提供材料或拍板** → ask_user_question；你**只是想让对话往前走一句**（追问、提示方向、缩小范围，如"你再想想这个条件是什么？"）→ 直接用正文问，不调用任何工具。
**出题一律 quiz_user，绝不用 ask_user_question**：ask 的卡片是"请你确认 / 请你提供信息"，不是练习；反过来，需要用户拍板时也别用 quiz_user。
能从字幕/历史/图片推断出来的一律自己推断，不要问。
两个工具的共同格式：一次 1~3 个问题，各给稳定 id；选择类问题能列选项就列，推荐项放第一条并在标签末尾加 "(Recommended)"；文本尽量短，公式遵守上方铁律（$...$ / $$...$$），禁止 \(...\)；用户作答后接着正常作答，不要复述他的回答。

## read 工具（要主动看文件时用）
用户上传的图片**已经直接附在消息里**，你当轮就能看到，不需要也不应该为此调用工具。
需要主动查看工作区里的其它文件时，用 read：
- read(path)：图片（jpg/jpeg/png/webp/gif/bmp）返回图片本身，你能直接看；文本文件（txt/md/srt/json/py/csv 等）返回文字内容
- 典型用法：回头看历史里提到过的图片、读字幕文件（storage/subtitles/…）、读课程总结（storage/summaries/…）、查看代码或数据文件
- path 写相对项目根的路径（如 storage/uploads/20261015_174805_845ddf84.jpg），项目的绝对路径也认
- 不要凭猜测描述文件内容：读不到、不存在或格式不支持时会返回错误说明，如实转述给用户即可

## 输出纪律（必须遵守）
1. 禁止输出任何思考过程、内心独白、自我怀疑或自我纠正。永远不要出现"等等""让我想想""我绕晕了""抱歉""重新来""好吧，直接"这类表达——你的所有推理都在内部完成，用户只看到最终答案。
2. 推导过程只展示"最终稿"：步骤干净、一步接一步，绝不展示试错、推翻、重写的过程。
3. "不确定"只用一句话简短标注（如"这一点建议查证"），绝不展开自我怀疑或长篇纠正。
4. 输出前自检：若回答中残留思考痕迹（草稿式表述、口语化补救词），删除后重写再输出。

## 通用规则
1. 始终用中文回答
2. 数学内容用 LaTeX 公式输出：独立公式用 $$...$$，行内公式用 $...$，禁用 \(...\)
3. 回答要鼓励性、建设性
4. 不确定的知识按上方「输出纪律」第3条，一句话简短标注
5. 语言自然清晰即可"""


# ===== ask 工具（照抄 dsh 的 ask_user_question：缺信息/要用户拍板时暂停提问）=====
# schema 与 description 原文来自 @deepseek-ai/dsh-tool-ask-user 的 ask_user_question；
# 只把 dsh 内部 schema 的 required 标记转成 OpenAI function calling 的标准写法。
# 职责单一：要用户提供信息或拍板。出题考用户走下面的 QUIZ_TOOL。
# questions[].question 等文本由模型生成，因此同样受上方公式铁律约束（后端还会硬清洗）。
ASK_TOOL = {
    "type": "function",
    "function": {
        "name": "ask_user_question",
        "description": "Ask the user a concise question when you need confirmation, a choice, or missing information before proceeding. Send one or more questions, each with a stable id that will be echoed in the answer.",
        "parameters": {
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "description": "Questions to ask the user before continuing.",
                    "items": {
                        "type": "object",
                        "additionalProperties": True,
                        "properties": {
                            "id": {
                                "type": "string",
                                "description": "Stable id for this question; echoed in the answer.",
                            },
                            "question": {
                                "type": "string",
                                "description": "The specific question to ask the user.",
                            },
                            "header": {
                                "type": "string",
                                "description": 'Optional short heading for the question, such as "Confirm" or "Choose Mode".',
                            },
                            "options": {
                                "type": "array",
                                "description": 'Optional choices to show the user. If you recommend one, put it first and append "(Recommended)" to that label.',
                                "items": {
                                    "type": "object",
                                    "additionalProperties": True,
                                    "properties": {
                                        "label": {
                                            "type": "string",
                                            "description": "Short user-facing option label.",
                                        },
                                        "description": {
                                            "type": "string",
                                            "description": "One sentence explaining the tradeoff or impact.",
                                        },
                                    },
                                    "required": ["label"],
                                },
                            },
                            "multi_select": {
                                "type": "boolean",
                                "description": "Whether the user may select more than one option. Defaults to false.",
                            },
                        },
                        "required": ["id", "question"],
                    },
                }
            },
            "required": ["questions"],
        },
    },
}


# ===== 出题工具 quiz_user（本项目自建，dsh 没有）=====
# 为什么要单独一个工具：原先"要用户输入"和"出题考用户"挤在同一个 description 里，
# 模型在模式B 会把"你再想想这个条件是什么？"这类引导式追问也做成选择题卡片，
# 两种用途互相污染。拆开后：ask 还原成 dsh 原文（职责单一），出题用另一个自解释的名字。
# schema 直接复用 ASK_TOOL 的（前端本就是同一套卡片渲染，服务端也按 tool_call_id 续轮），
# 只深拷贝后改几处措辞——省得两份 schema 各自漂移。
QUIZ_PARAMS = json.loads(json.dumps(ASK_TOOL["function"]["parameters"]))
QUIZ_PARAMS["description"] = "Quiz questions to present to the user."
QUIZ_PARAMS["properties"]["questions"]["description"] = \
    "Practice/quiz questions to present. The user answers by picking options or typing in the card."
_qp = QUIZ_PARAMS["properties"]["questions"]["items"]["properties"]
_qp["question"]["description"] = "The practice/quiz question stem (LaTeX formulas allowed)."
_qp["header"]["description"] = 'Optional short heading, such as "定积分练习".'
_qp["options"]["description"] = \
    "Answer choices. Leave empty for open-ended questions (the user types the answer in the card)."
QUIZ_TOOL = {
    "type": "function",
    "function": {
        "name": "quiz_user",
        "description": "Present a practice or quiz question to the user and wait for their answer, so you can grade it. For multiple-choice questions put the choices in options; for calculation or proof questions leave options empty and the user types the answer in the card. The user's answer comes back as this tool's result, then you grade it and continue.",
        "parameters": QUIZ_PARAMS,
    },
}
# ===== read 工具（泛读工作区文件；图片直接交回图块，文本交回文字）=====
# 取代原先的 read_image / modlens_read_image 双工具：主模型已能看图，不再需要
# 「用视觉模型把图转成文字」的桥接（视觉辅助整套配置已随之删除）。
# 只留一个名字（read），模型不必在「我能看图吗」上做判断。
READ_TEXT_EXTS = {".txt", ".md", ".markdown", ".srt", ".vtt", ".json", ".csv", ".tsv",
                  ".py", ".js", ".mjs", ".ts", ".html", ".htm", ".css", ".sh", ".bat",
                  ".yml", ".yaml", ".ini", ".cfg", ".log", ".sql"}
READ_TOOL = {
    "type": "function",
    "function": {
        "name": "read",
        "description": (
            "Read a file from the study platform workspace. Images (jpg/jpeg/png/webp/gif/bmp) "
            "are returned as the actual image, so you can look at them directly. Text files "
            "(txt/md/srt/json/csv/py/js/... ) are returned as text. Use it to look at an uploaded "
            "image mentioned earlier in the conversation, a subtitle file, a course summary, or a "
            "source/data file. Pass a path relative to the project root (e.g. "
            "storage/uploads/20261015_174805_845ddf84.jpg)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to the project root (or an absolute path inside it)",
                },
            },
            "required": ["path"],
        },
    },
}

TOOLS = [ASK_TOOL, QUIZ_TOOL, READ_TOOL]
TOOL_NAMES = {t["function"]["name"] for t in TOOLS}


# ===== 文件中心沙箱工具（会话绑定文件时才挂载，作用域锁死在该文件的产物目录） =====
# 共识 D5：预编排保下限，沙箱 read 保上限——模型需要细节时自己翻当前文件的产物。
READ_ARTIFACT_TOOL = {
    "type": "function",
    "function": {
        "name": "read_artifact",
        "description": (
            "Read derived content of the file the user is currently studying. Available: "
            "'summary.md' (AI summary), 'extracted.md' (full extracted text), "
            "'pages.json' (per-page text). Args: {name: 'summary.md'} or "
            "{page: 12} to read one page's text. Use when you need details beyond the "
            "pre-loaded context excerpt."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Artifact file name, e.g. summary.md / extracted.md"},
                "page": {"type": "integer", "description": "Read a single page's text instead (1-based)"},
            },
        },
    },
}
LIST_ARTIFACTS_TOOL = {
    "type": "function",
    "function": {
        "name": "list_artifacts",
        "description": "List what derived content is available for the current file (summary/extracted text/page count, etc.). Call this first if unsure what can be read.",
        "parameters": {"type": "object", "properties": {}},
    },
}
ARTIFACT_TOOL_NAMES = {"read_artifact", "list_artifacts"}

# 目录浏览工具（共识 Q2/Q3：AI 不知道精确路径 → list_files 列当前聚焦文件夹的目录树，文件名=课程元数据）
LIST_FILES_TOOL = {
    "type": "function",
    "function": {
        "name": "list_files",
        "description": (
            "List the files and folders in the current study scope (the folder the session is "
            "focused on, or the folder of the file being viewed). Returns a tree: folders, then "
            "files with name/kind/id. File names encode course metadata (e.g. '18.第16讲_03.无穷级数判别方法'). "
            "Use this BEFORE read_artifact to see what is available and to know exact artifact names. "
            "Optional args: keyword to filter by name substring; depth 1 = this folder only, depth 0 = whole tree."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "Filter by name substring (optional)"},
                "depth": {"type": "integer", "description": "0 = full tree, 1 = current folder only. Default 0."},
            },
        },
    },
}

# 章节摘要懒生成工具（共识 Q11：首次问到该章才生成并缓存）
GEN_CHAPTER_TOOL = {
    "type": "function",
    "function": {
        "name": "gen_chapter_summary",
        "description": (
            "Generate (or fetch cached) a detailed study summary for one chapter of the file "
            "the user is currently studying. Use it when the user's question involves a chapter "
            "whose detailed summary is not yet in your context (the context explicitly notes which "
            "chapter lacks one and gives its cid). Args: {cid: 'c5'} — chapter ids appear in the "
            "outline/TOC section of the context."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "cid": {"type": "string", "description": "Chapter id from the outline, e.g. c5"},
            },
            "required": ["cid"],
        },
    },
}


def _tools_unsupported(body: str) -> bool:
    """判断 400 响应是否因为端点不认 tools/function calling（部分 OpenAI 兼容网关）"""
    b = (body or "").lower()
    if "tool" not in b and "function" not in b:
        return False
    return any(k in b for k in ("not support", "unsupported", "invalid", "unknown", "unexpected"))


def _build_system_prompt(subject: str, mode: str, subtitle_context: str) -> str:
    """用 replace 而不是 .format()：SYSTEM_PROMPT 和字幕文本里含有 {dy}/{dx} 等
    LaTeX 花括号，.format() 会把它们当占位符导致 KeyError
    """
    subject_names = {"math": "数学", "english": "英语", "politics": "政治", "zhuanye": "专业课"}
    mode_names = {"A": "即时问答", "B": "引导输出", "C": "课后问答"}
    return (
        SYSTEM_PROMPT
        .replace("{subject}", subject)
        .replace("{subject_name}", subject_names.get(subject, subject))
        .replace("{mode}", mode)
        .replace("{mode_name}", mode_names.get(mode, mode))
        .replace("{subtitle_context}", subtitle_context or "（无）")
    )


def _completion_payload(messages: list, system_prompt: str, stream: bool,
                        model: str, effort: str = "high", tools: list = None) -> dict:
    full_messages = [{"role": "system", "content": system_prompt}] + messages
    payload = {
        "model": model,
        "messages": full_messages,
        "thinking": {"type": "enabled"},
        "reasoning_effort": _map_effort(effort),  # 思考想透再写正文，档位会话级可调
        "temperature": 0.3,          # 数学严谨场景，降低发散
        "max_tokens": 16384,
        "stream": stream,
    }
    if tools:
        payload["tools"] = tools
    return payload


async def chat_completion_stream(
    messages: list,
    subject: str = "math",
    mode: str = "A",
    subtitle_context: str = "",
    model_override: str = "",
    effort: str = "",
    enable_tools: bool = True,
    extra_tools: list = None,
) -> AsyncIterator[Tuple[str, str]]:
    """流式调用主模型（thinking 模式，OpenAI 兼容端点），逐段产出：

        ("reasoning", "…思考片段…")  —— 思维链，不展示，由调用方累积存入历史
        ("content", "…正文片段…")    —— 最终回答，逐字展示给用户
        ("tool_calls", "[{…}]")      —— 模型要求调用工具时的完整 tool_calls（JSON 数组，
                                         流末尾一次性产出，前端据此弹出交互）

    调用方负责累积文本；HTTP/API 错误会以 RuntimeError 抛出。
    model_override: 会话级模型名（非空时覆盖全局配置，用于对话框一键切模型）。
    effort: 会话级思考强度（low/medium/high/xhigh/max，空 = 全局默认 high）。
    enable_tools: 是否挂 ask_user_question；端点不认 tools 时自动摘掉重试一次。
    """
    cfg = load_model_config()["main"]
    endpoint = get_endpoint(cfg.get("base_url"), "/chat/completions")
    system_prompt = _build_system_prompt(subject, mode, subtitle_context)
    model = model_override or cfg.get("model") or "deepseek-v4-flash"

    headers = {
        "Authorization": f"Bearer {cfg.get('api_key') or settings.deepseek_api_key}",
        "Content-Type": "application/json",
    }

    # 先带 tools 请求；端点不认（400 且提到 tool/function）则摘掉 tools 重试一次，
    # 保证「模型不支持 function calling」时聊天仍可用，只是没有 ask 卡片。
    all_tools = list(TOOLS) + list(extra_tools or [])
    attempts = [True, False] if enable_tools else [False]
    async with httpx.AsyncClient(timeout=STREAM_TIMEOUT) as client:
        for use_tools in attempts:
            payload = _completion_payload(
                messages, system_prompt, stream=True, model=model, effort=effort,
                tools=all_tools if use_tools else None,
            )
            tool_acc: dict = {}  # index -> {id, name, arguments}（流式分片累积）
            async with client.stream("POST", endpoint, json=payload, headers=headers) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode("utf-8", errors="replace")
                    if use_tools and _tools_unsupported(body):
                        continue
                    raise RuntimeError(f"主模型 API {resp.status_code}: {body[:800]}")
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choices = obj.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta", {}) or {}
                    rc = delta.get("reasoning_content")
                    if rc:
                        yield "reasoning", rc
                    for tc in delta.get("tool_calls") or []:
                        slot = tool_acc.setdefault(
                            tc.get("index", 0), {"id": "", "type": "function",
                                                 "function": {"name": "", "arguments": ""}}
                        )
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["function"]["name"] = fn["name"]
                        if fn.get("arguments"):
                            slot["function"]["arguments"] += fn["arguments"]
                    c = delta.get("content")
                    if c:
                        yield "content", c
            if tool_acc:
                calls = [tool_acc[i] for i in sorted(tool_acc)]
                yield "tool_calls", json.dumps(calls, ensure_ascii=False)
            return


# ===== 课程总结生成（分段提取 → 合并去重，保证信息密度）=====
# 策略：整讲字幕按时间分段，每段单独"逐句扫描"提取（模型注意力集中在小段上，
# 不因长文稀释细节）；全部段结果再合并，并要求对照各段检查遗漏。相比一次性
# 压缩全文，分段提取 + 合并防漏能最大程度保住公式/例题/易错点等关键信息。

SEGMENT_EXTRACT_PROMPT = """你是考研课程内容提取器。下面是某考研课第 {n}/{total} 段的带时间戳字幕（[mm:ss] 为该句起始时间）：

字幕内容：
{subtitle}

任务：逐句扫描，提取这一段里出现的全部有效信息，宁多勿漏：
1. 知识点/概念：名称 + 一句话解释 + 时间戳（如 [12:34]）
2. 公式：完整 LaTeX 公式（$...$ / $$...$$），一个都别漏
3. 例题：题干 + 解题关键步骤（简短）+ 时间戳
4. 方法/技巧/口诀
5. 易错点/老师强调的警告
只输出提取结果，不要客套话；某类内容不存在就省略该类。若一段字幕没有任何有效内容，输出「（无有效内容）」。"""

SUMMARIZE_MERGE_PROMPT = """下面是同一节考研课《{title}》按时间分段提取的 {k} 份要点。

请对照全部要点，合并去重，生成一份结构化的考研复习总结，要求：
1. 按五段组织：## 知识点 / ## 核心公式 / ## 例题与题型 / ## 方法技巧 / ## 易错点
2. 合并前逐份检查一遍：任何公式、例题、知识点都不得遗漏（宁可多列）
3. 同一概念出现在多段的合并为一条，时间戳取首次出现
4. 公式一律用 $...$ 或 $$...$$ LaTeX，中文讲解
5. 直接输出 markdown，不要额外说明。

{parts}"""


def _simple_endpoint() -> str:
    """主模型 endpoint（简单调用与流式共用同一配置）"""
    cfg = load_model_config()["main"]
    return get_endpoint(cfg.get("base_url"), "/chat/completions")


def _simple_headers() -> dict:
    return {
        "Authorization": f"Bearer {load_model_config()['main'].get('api_key') or settings.deepseek_api_key}",
        "Content-Type": "application/json",
    }


async def chat_completion_simple(
    messages: list,
    max_tokens: int = 4096,
    temperature: float = 0.3,
) -> str:
    """普通（非流式、非 thinking）调用主模型，用于总结生成等批量任务。

    比流式聊天快得多（不开思维链），返回纯文本。
    """
    model = load_model_config()["main"].get("model") or "deepseek-v4-flash"
    payload = {
        "model": model,
        "messages": messages,
        "thinking": {"type": "disabled"},  # 总结/提取不需要思考，快且省
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=STREAM_TIMEOUT) as client:
        resp = await client.post(_simple_endpoint(), json=payload, headers=_simple_headers())
        if resp.status_code != 200:
            raise RuntimeError(f"主模型 API {resp.status_code}: {resp.text[:800]}")
        result = resp.json()
        return result.get("choices", [{}])[0].get("message", {}).get("content", "") or ""


async def summarize_lecture(title: str, segments: list) -> str:
    """分段提取 → 合并防漏，生成整讲 markdown 总结。

    segments: 带时间戳的字幕片段列表（每段约 20 分钟内容）。
    """
    if not segments:
        raise ValueError("没有可总结的字幕内容")

    # 第一轮：逐段提取
    part_results = []
    for i, seg in enumerate(segments, 1):
        prompt = SEGMENT_EXTRACT_PROMPT.format(n=i, total=len(segments), subtitle=seg)
        out = await chat_completion_simple(
            [{"role": "user", "content": prompt}],
            max_tokens=2048, temperature=0.2,
        )
        part_results.append(out)

    # 第二轮：合并去重 + 遗漏检查
    parts_body = "\n\n".join(
        f"=== 分段 {i} ===\n{t}" for i, t in enumerate(part_results, 1)
    )
    merge_prompt = SUMMARIZE_MERGE_PROMPT.format(
        title=title, k=len(part_results), parts=parts_body,
    )
    return await chat_completion_simple(
        [{"role": "user", "content": merge_prompt}],
        max_tokens=4096, temperature=0.3,
    )


# ===== 工具执行：模型发起工具调用后，服务端在这里落地 =====
# 上传图片落盘目录（chat.py 写、工具读，两边共用同一个常量）
UPLOAD_DIR = Path(settings.storage_dir) / "uploads"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
IMAGE_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
              ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp"}
MAX_IMAGE_BYTES = 10 * 1024 * 1024   # 单图上限，与前端上传限制一致


def _safe_upload_path(path: str) -> Path:
    """把模型给的路径限制在上传目录内（模型可能给出任意路径，必须做沙箱校验）

    路径来源有两种写法，都要认：
    - 相对项目根：``storage/uploads/20260914_xxx.png``（库里的规范写法）
    - 纯文件名：``20260914_xxx.png``（模型偶尔只回文件名）
    """
    raw = str(path or "").strip()
    if not raw:
        raise ValueError("path 不能为空")
    p = Path(raw.replace("\\", "/"))
    if not p.is_absolute():
        cand = to_abs(raw)
        p = cand if (cand is not None and cand.is_file()) else (UPLOAD_DIR / raw)
    try:
        p = p.resolve()
    except Exception:
        raise ValueError(f"路径无法解析：{raw}")
    base = UPLOAD_DIR.resolve()
    if p != base and base not in p.parents:
        raise ValueError(f"只允许读取上传目录内的图片（{base}）")
    if not p.is_file():
        raise ValueError(f"文件不存在：{p.name}")
    if p.suffix.lower() not in IMAGE_EXTS:
        raise ValueError(f"不支持的图片格式：{p.suffix}")
    if p.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError("图片超过 10MB 上限")
    return p


def _b64_data_url(p: Path) -> str:
    mime = IMAGE_MIME.get(p.suffix.lower(), "image/jpeg")
    return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode("utf-8")


def upload_image_block(rel_path: str) -> dict:
    """把已落盘的上传图片转成一条 image_url 内容块。

    供 chat.py 把图片直接内联进消息（用户上传的图 / 学生用图片作答的 tool 结果）；
    沙箱 / 格式 / 大小校验与 read 工具完全共用 _safe_upload_path，
    避免两处沙箱规则各写一遍、日后漂移。
    """
    p = _safe_upload_path(rel_path)
    return {"type": "image_url", "image_url": {"url": _b64_data_url(p)}}


def upload_public_url(rel_path: str) -> str:
    """把库里的图片相对路径转成前端可直接 <img src> 的接口地址。

    上传目录没有静态挂载（不想把用户作业图暴露成免鉴权静态资源），
    统一走 /api/chat/uploads/<文件名>：该前缀在鉴权中间件覆盖范围内，
    前端按既有约定追加 ?token= 即可（与视频流同一套做法）。
    """
    name = Path(str(rel_path or "").replace("\\", "/")).name
    return f"/api/chat/uploads/{name}" if name else ""


# ===== read 工具：工作区文件读取（图片给图块、文本给文字）=====
MAX_READ_BYTES = 10 * 1024 * 1024     # 图片上限，与上传限制一致
MAX_READ_CHARS = 40000                # 文本单次返回上限（超出截断，附提示）
# 不给模型读的敏感文件（.env 里是 API Key；库里是用户数据；密钥/证书同理）
READ_DENY_NAMES = {".env", ".env.local", ".env.production", "id_rsa", "id_ed25519"}
READ_DENY_SUFFIX = {".pem", ".key", ".p12", ".keystore", ".db", ".sqlite", ".db-wal", ".db-shm"}


def _safe_workspace_path(path: str) -> Path:
    """把模型给的路径限制在**项目根目录内**（read 工具可读工作区任意文件）。

    与上传沙箱的区别：不再限定 storage/uploads，但绝不允许越出项目根
    （防 ../../ 之类的目录穿越），同时挡掉 .env / 数据库 / 私钥等敏感文件。
    """
    raw = str(path or "").strip()
    if not raw:
        raise ValueError("path 不能为空")
    p = Path(raw.replace("\\", "/"))
    cand = p if p.is_absolute() else to_abs(raw)
    if cand is None:
        raise ValueError(f"路径无法解析：{raw}")
    if not cand.is_file():
        # 兼容：模型偶尔只回文件名（不带目录），去上传目录里找一下
        alt = UPLOAD_DIR / p.name
        if p.name and alt.is_file():
            cand = alt
    try:
        cand = cand.resolve()
    except Exception:
        raise ValueError(f"路径无法解析：{raw}")
    base = Path(settings.storage_dir).resolve().parent   # 项目根
    if cand != base and base not in cand.parents:
        raise ValueError("只能读取项目目录内的文件")
    if not cand.is_file():
        raise ValueError(f"文件不存在：{raw}")
    name = cand.name.lower()
    if name in READ_DENY_NAMES or cand.suffix.lower() in READ_DENY_SUFFIX:
        raise ValueError(f"该文件不允许读取：{cand.name}")
    return cand


def _read_text_file(p: Path) -> str:
    """读文本文件（容错编码），超长截断并注明"""
    size = p.stat().st_size
    if size > MAX_READ_BYTES:
        raise ValueError(f"文件太大（{size // 1024}KB），只支持 10MB 以内的文件")
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        raise ValueError(f"读取失败：{e}")
    if "\x00" in text[:2000]:
        raise ValueError("这是二进制文件，无法按文本读取")
    if len(text) > MAX_READ_CHARS:
        text = text[:MAX_READ_CHARS] + f"\n\n…（内容过长，仅返回前 {MAX_READ_CHARS} 字，共 {len(text)} 字）"
    return text


# ===== 文件中心沙箱：当前文件的产物目录（read_artifact / list_artifacts 用） =====
def _artifact_sandbox(file_id: int) -> Path:
    base = (Path(settings.artifacts_dir) / str(int(file_id or 0))).resolve()
    if base.parent != Path(settings.artifacts_dir).resolve():
        raise ValueError("非法 file_id")
    return base


def _execute_artifact_tool(name: str, args: dict, file_id: int):
    """沙箱产物读取：只能访问 storage/artifacts/{file_id}/ 内的白名单产物。

    与工作区 read 的区别：作用域锁死在当前文件的产物目录（共识 D5 的隔离边界）。
    """
    from .file_service import ARTIFACT_NAMES, read_artifact
    base = _artifact_sandbox(file_id)
    if not base.is_dir():
        raise ValueError("当前文件还没有任何 AI 产物（可能仍在处理中）")
    if name == "list_artifacts":
        items = []
        meta = {}
        mp = base / "meta.json"
        if mp.is_file():
            try:
                meta = json.loads(mp.read_text(encoding="utf-8"))
            except Exception:
                pass
        for fname in sorted(ARTIFACT_NAMES):
            p = base / fname
            if p.is_file():
                items.append({"name": fname, "size": p.stat().st_size})
        return json.dumps({"artifacts": items, "meta": meta}, ensure_ascii=False)
    # read_artifact
    page = args.get("page")
    if page:
        raw = read_artifact(file_id, "pages.json")
        if not raw:
            raise ValueError("没有逐页文本（pages.json 不存在）")
        pages = json.loads(raw)
        for p in pages:
            if p.get("page") == int(page):
                return f"第 {page} 页内容：\n\n{p['text']}"
        raise ValueError(f"没有第 {page} 页（共 {len(pages)} 页）")
    fname = str(args.get("name") or "summary.md")
    if fname not in ARTIFACT_NAMES:
        raise ValueError(f"未知产物：{fname}（可用：{', '.join(sorted(ARTIFACT_NAMES))}）")
    text = read_artifact(file_id, fname)
    if text is None:
        raise ValueError(f"产物不存在：{fname}")
    if len(text) > MAX_READ_CHARS:
        text = text[:MAX_READ_CHARS] + f"\n\n…（内容过长，仅返回前 {MAX_READ_CHARS} 字，共 {len(text)} 字；可用 page 参数逐页读）"
    return f"{fname} 内容如下：\n\n{text}"


def _list_files_tool(args: dict, file_id: int, folder_id: int = 0) -> str:
    """列出沙箱作用域（聚焦文件夹）内的目录树。文件名完整暴露（共识 Q3）。"""
    from ..core.database import SessionLocal
    from ..models import File as FileModel, Folder

    db = SessionLocal()
    try:
        # 作用域：显式 folder_id 优先；否则当前文件所属文件夹；否则根
        fid = folder_id or 0
        if not fid and file_id:
            f = db.query(FileModel).filter(FileModel.id == file_id).first()
            if f:
                fid = f.folder_id or 0

        kw = str(args.get("keyword") or "").strip().lower()
        depth = int(args.get("depth") or 0)

        # 子树 id 集合（包含 fid 自身）
        ids = [fid]
        frontier = [fid] if fid else []
        while frontier:
            nxt = [x.id for x in db.query(Folder).filter(Folder.parent_id.in_(frontier)).all()
                   if x.id not in ids]
            ids += nxt
            frontier = nxt

        # 目录树
        all_folders = db.query(Folder).filter(Folder.deleted == 0).all()
        fmap = {}
        for fl in all_folders:
            fmap.setdefault(fl.parent_id, []).append(fl)

        def walk(parent, d):
            out = []
            for fl in sorted(fmap.get(parent, []), key=lambda x: x.sort_order or 9999):
                out.append(fl)
                out.extend(walk(fl.id, d + 1))
            return out

        # 输出：文件夹（缩进）+ 文件（名字/kind）
        lines = []
        if depth == 1:
            sub_folders = [fl for fl in fmap.get(fid, [])]
            files = db.query(FileModel).filter(
                FileModel.folder_id == fid, FileModel.deleted == 0).order_by(FileModel.name).all()
        else:
            sub_folders = [fl for fl in walk(fid if fid else 0, 0) if fl.parent_id == (fid if fid else 0)]
            files = db.query(FileModel).filter(
                FileModel.folder_id.in_(ids), FileModel.deleted == 0).order_by(FileModel.name).all()

        for fl in sub_folders:
            n = db.query(FileModel).filter(
                FileModel.folder_id == fl.id, FileModel.deleted == 0).count()
            lines.append(f"[文件夹] {fl.name}（{n} 个文件）")
        for f in files:
            if kw and kw not in f.name.lower():
                continue
            icon = {"video": "🎬", "pdf": "📕", "office": "📊", "image": "🖼️",
                    "text": "📄", "markdown": "📝", "audio": "🎵"}.get(f.kind, "📦")
            sz = "" if f.size <= 0 else f"（{f.size // 1024 // 1024}MB）" if f.size > 1048576 else f"（{f.size // 1024}KB）"
            lines.append(f"{icon} {f.name}{sz} [id={f.id}]")
        if not lines:
            return "（当前聚焦范围内没有文件/文件夹。可用 list_files(depth=0) 查看整个范围）"
        head = f"当前学习范围的文件清单（沙箱）："
        if kw:
            head = f"按「{kw}」过滤后的文件："
        return head + chr(10) + chr(10).join(lines)
    finally:
        db.close()


async def _gen_chapter_summary_tool(args: dict, file_id: int):
    """AI 触发的章节摘要懒生成：缓存命中直接回；否则 1 次 LLM 调用并落盘（计入闸门）"""
    from ..core.database import SessionLocal
    from ..models import File as FileModel
    from .doc_index import (get_outline, compose_chapter_prompt,
                            read_artifact, write_text,
                            llm_gate_ok, llm_record)
    cid = str(args.get("cid") or "").strip()
    if not cid:
        raise ValueError('需要 cid 参数（章节 id，如 "c5"；见上下文中的目录结构）')

    db = SessionLocal()
    try:
        f = db.query(FileModel).filter(FileModel.id == file_id).first()
        title = f.name if f else f"文件{file_id}"
    finally:
        db.close()

    ol = get_outline(file_id)
    if not ol:
        raise ValueError("当前文件没有章节大纲")
    ch = next((c for c in (ol.get("chapters") or []) if c["id"] == cid), None)
    if not ch:
        avail = ", ".join(c["id"] + "=" + (c["title"][:14] or "无题")
                          for c in (ol.get("chapters") or [])[:12])
        raise ValueError(f"章节 {cid} 不存在。可用章节：{avail}")

    cached = read_artifact(file_id, f"chap_{cid}.md")
    if cached:
        return "章节「" + ch["title"] + "」摘要（已有缓存，无需重新生成）：" + chr(10) + chr(10) + cached
    raw = read_artifact(file_id, "pages.json")
    if not raw:
        raise ValueError("该文件还没有逐页文本（提取未完成）")
    pages = json.loads(raw)

    if not llm_gate_ok(file_id, "chapter"):
        raise ValueError(
            f"已达该文档自动生成上限（{MAX_LLM_CALLS_PER_DOC} 次）。"
            "请告知用户：可在设置里提高上限或手动重试。")

    prompt = compose_chapter_prompt(title, ol, pages, ch)
    async with SUMMARY_LOCK:
        content = await chat_completion_simple([{"role": "user", "content": prompt}],
                                               max_tokens=2500, temperature=0.3)
    if not (content or "").strip():
        raise ValueError("生成结果为空")
    write_text(file_id, f"chap_{cid}.md", content)
    llm_record(file_id, "chapter", auto=True)
    return "已生成章节「" + ch["title"] + "」的学习摘要（已缓存，下次直接可用）：" + chr(10) + chr(10) + content

async def execute_tool_call(name: str, args: dict, file_id: int = 0, folder_id: int = 0):
    """执行一次工具调用，返回 tool 消息的 content（字符串，或带图片的内容数组）

    file_id: 会话绑定的当前文件 id（沙箱工具用；0=未绑定文件则 read_artifact/list_artifacts 不挂载）。
    folder_id: 沙箱作用域 = 聚焦文件夹（list_files 用它列目录；0 时退化为当前文件所属文件夹）。
    """
    if name in ARTIFACT_TOOL_NAMES:
        if not file_id:
            raise ValueError("当前会话没有绑定文件")
        return _execute_artifact_tool(name, args, file_id)
    if name == "list_files":
        return _list_files_tool(args, file_id, folder_id)
    if name == "gen_chapter_summary":
        if not file_id:
            raise ValueError("当前会话没有绑定文件")
        return await _gen_chapter_summary_tool(args, file_id)
    # read_image：旧名保留兼容（历史里可能有模型沿用旧工具名发的调用）
    if name in ("read", "read_image"):
        p = _safe_workspace_path(args.get("path"))
        if p.suffix.lower() in IMAGE_EXTS:
            if p.stat().st_size > MAX_IMAGE_BYTES:
                raise ValueError("图片超过 10MB 上限")
            return [
                {"type": "text", "text": f"图片 {p.name} 的内容如下："},
                {"type": "image_url", "image_url": {"url": _b64_data_url(p)}},
            ]
        if p.suffix.lower() in READ_TEXT_EXTS:
            return f"文件 {p.name} 的内容如下：\n\n" + _read_text_file(p)
        raise ValueError(f"不支持的文件类型：{p.suffix or '（无扩展名）'}")
    raise ValueError(f"未知工具：{name}")