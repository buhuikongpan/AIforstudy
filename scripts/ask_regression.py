# -*- coding: utf-8 -*-
"""交互工具回归验收：把真实场景重放给主模型，看它到底调没调、调的是哪一个。

两个工具（2026-09-15 拆分）
------------------------
  ask_user_question —— 缺只有用户能提供的信息 / 需要用户拍板
  quiz_user         —— 出题考用户（卡片点选或输入作答，提交后 AI 批改）
拆之前两者共用一个 description，结果模型把"你再想想这个条件是什么？"这类引导式追问
也做成了选择题卡片；下面每条场景都写清楚期望哪个工具，"走正文"也是合法期望。

为什么要有这个脚本
------------------
提示词里写"该问就问"不等于模型会问。2026-09-15 那次物理辅导（mode B）里，用户说
"后两问直接套公式就得了"，模型在思考里看懂了两种读法，却仍然选择"顶回去"继续引导，
一次 ask 都没调；全库 112 条 assistant 消息里 ask_user_question 只出现过 2 次。
所以这类"行为积极性"不能靠读提示词验收，只能真跑一遍模型。

复用后端同一套实现（SYSTEM_PROMPT / TOOLS / _build_system_prompt / 模型配置），
保证"脚本测的"就是"线上跑的"：同一份提示词、同一组工具 schema、同一个端点。

用法
----
    python scripts/ask_regression.py                # 全部场景，逐条判定
    python scripts/ask_regression.py --case B-契约岔路
    python scripts/ask_regression.py --dry-run      # 只打印场景与系统提示词，不调 API
    python scripts/ask_regression.py -v             # 额外打印模型实际问了什么

退出码：0 = 全部符合期望；1 = 有场景不符（可当门禁用）；2 = 配置/依赖缺失
"""
import argparse
import io
import json
import os
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower().startswith("gb"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace",
                                  line_buffering=True)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
try:
    from app.core.model_config import get_endpoint, load_model_config
    from app.services.llm_service import TOOLS, _build_system_prompt, _completion_payload
except Exception as e:  # 依赖缺失/代码半坏时给明确提示，而不是抛栈
    print("[X] 无法导入后端模块（backend/app/...）：%s" % e)
    print("    请确认已 pip install -r backend/requirements.txt，且在项目根目录运行。")
    sys.exit(2)

import httpx

# 场景表。expect 是"该调哪个工具"的期望："ask" / "quiz" / ""（后者=应该走正文）。
# why = 判定依据（回归失败时先看这里）。★ 是 2026-09-15 物理辅导那次现场，改提示词的直接动机。
CASES = [
    dict(
        name="B-契约岔路★",
        mode="B", subject="math",
        user="后两问直接套公式就得了",
        expect="ask",
        why="★现场：两种读法（要我给答案 / 陈述思路等我批改），且与模式B角色冲突，属用户所有权选择",
    ),
    dict(
        name="B-催着要答案",
        mode="B", subject="math",
        user="太难了，直接告诉我答案吧",
        expect="ask",
        why="用户要求改变交互契约（别引导了）。继续引导还是给答案应由用户拍板，不能模型自己决定",
    ),
    dict(
        name="B-单点知识追问",
        mode="B", subject="math",
        # 必须给上下文：提问里的"这一步"靠字幕才能定位。无上下文时模型缺题面，
        # 弹卡片索要题面反而是对的（那属于 ask 的规则 1），这条逆向场景就不成立了。
        subtitle="【例题】计算 $\\int_0^1 \\frac{\\ln(1+x)}{1+x^2}\\,dx$。解析：令 $x=\\tan t$，$dx=\\frac{dt}{\\cos^2 t}$，原式化为 $\\int_0^{\\pi/4}\\ln(1+\\tan t)\\,dt$。",
        user="这一步为什么要换元？",
        expect="",
        why="题面已在字幕里，单点小追问是教学内容，正文讲即可；两个工具都不该调（拆工具前它会被做成选择题卡片）",
    ),
    dict(
        name="A-纯知识",
        mode="A", subject="math",
        user="拉格朗日中值定理的条件是什么？",
        expect="",
        why="纯知识问题，没有用户所有权选择，问了就是骚扰",
    ),
    dict(
        name="A-要用户拍板",
        mode="A", subject="math",
        user="这两道题先讲哪一道？",
        expect="ask",
        why="必须由用户选择才能继续，典型的该问场景",
    ),
    dict(
        name="A-缺用户信息",
        mode="A", subject="math",
        user="帮我制定一个数学复习计划",
        expect="ask",
        why="目标院校/基础/每天可用时间只有用户能给，字幕和历史都推断不出来",
    ),
    dict(
        name="B-出题考我",
        mode="B", subject="math",
        user="出两道题考考我",
        expect="quiz",
        why="出题考核要走 quiz_user 卡片（点选作答→批改），而不是正文出题，也不是 ask。"
            "2026-09-15 实测发现：模型会先反问「想让我考哪一块」再出题——模式B 已有字幕（本题给了），"
            "反问范围是多余绕路，已在提示词里禁止（quiz_user 的「别反问范围」条）",
    ),
    dict(
        name="A-出题练手",
        mode="A", subject="math",
        user="给我出一道定积分的练习题",
        expect="quiz",
        why="同样是出题：卡片能让用户在输入框写答案再提交，比正文出题多一轮交互好处",
    ),
]


def _interactive_calls(message: dict) -> list:
    """取出这条回复里的交互工具调用，返回 [(工具名, 参数dict), ...]（没调就返回空列表）

    两个工具都算：ask_user_question（要用户拍板/缺信息）、quiz_user（出题考用户）。
    """
    out = []
    for call in (message.get("tool_calls") or []):
        fn = call.get("function") or {}
        name = fn.get("name")
        if name not in ("ask_user_question", "quiz_user"):
            continue
        try:
            out.append((name, json.loads(fn.get("arguments") or "{}")))
        except json.JSONDecodeError:
            out.append((name, {"raw": fn.get("arguments")}))
    return out


def run_case(case: dict, cfg: dict, verbose: bool):
    """跑一个场景，返回 (实际调用名 "ask"/"quiz"/""=走正文 | None 表示请求失败, 说明文本)"""
    prompt = _build_system_prompt(case.get("subject", "math"), case.get("mode", "A"),
                                  case.get("subtitle", "（无）"))
    # 走后端同一条组装路径：同一份 system prompt、同一组 tools、同一套采样参数
    payload = _completion_payload([{"role": "user", "content": case["user"]}], prompt, False,
                                  cfg["model"], case.get("effort", "high"), TOOLS)
    endpoint = get_endpoint(cfg["base_url"], "/chat/completions")
    try:
        resp = httpx.post(endpoint, json=payload,
                          headers={"Authorization": "Bearer " + cfg["api_key"]}, timeout=180)
    except Exception as e:
        return None, "请求异常：%s" % e
    if resp.status_code != 200:
        return None, "HTTP %d：%s" % (resp.status_code, resp.text[:300])

    message = ((resp.json().get("choices") or [{}])[0]).get("message") or {}
    calls = _interactive_calls(message)
    # 归一化成短名，方便和 expect 对账（"" = 没调交互工具，直接正文作答）
    got = ("quiz" if calls[0][0] == "quiz_user" else "ask") if calls else ""
    detail = ""
    if verbose:
        detail = ("%s: %s" % (calls[0][0], json.dumps(calls[0][1], ensure_ascii=False)[:400])) \
            if calls else ("正文：" + (message.get("content") or "")[:200].replace("\n", " "))
    return got, detail


def main() -> int:
    ap = argparse.ArgumentParser(description="交互工具（ask / quiz）积极性回归验收")
    ap.add_argument("--case", help="只跑名字包含该子串的场景")
    ap.add_argument("--dry-run", action="store_true", help="只打印场景与系统提示词，不调 API")
    ap.add_argument("-v", "--verbose", action="store_true", help="打印模型实际问了什么/答了什么")
    args = ap.parse_args()

    cases = [c for c in CASES if not args.case or args.case in c["name"]]
    if not cases:
        print("[X] 没有匹配的场景：%s" % args.case)
        return 2

    if args.dry_run:
        for c in cases:
            print("=" * 76)
            print("场景: %-16s (mode %s, 期望 %s)" % (c["name"], c.get("mode", "A"), c["expect"] or "正文"))
            print("用户: %s" % c["user"])
            print("依据: %s" % c["why"])
        print("=" * 76)
        print("送进模型的系统提示词（占位符已按 scene 1 替换）：\n")
        print(_build_system_prompt("math", cases[0].get("mode", "A"), "（无）"))
        return 0

    cfg = load_model_config().get("main") or {}
    if not cfg.get("api_key") or not cfg.get("model") or not cfg.get("base_url"):
        print("[X] 主模型未配置（base_url/model/api_key）——请先在 ⚙️ 设置 → 主模型 里填好。")
        return 2

    print("端点 %s\n模型 %s\n" % (get_endpoint(cfg["base_url"], "/chat/completions"), cfg["model"]))
    failed = 0
    for c in cases:
        got, detail = run_case(c, cfg, args.verbose)
        if got is None:
            print("  %-14s ✗ %s" % (c["name"], detail))
            failed += 1
        elif got == c["expect"]:
            print("  %-14s ✓ %s" % (c["name"], got or "正文"))
        else:
            print("  %-14s ✗ 期望 %-6s 实际 %-6s ← %s"
                  % (c["name"], c["expect"] or "正文", got or "正文", c["why"]))
            failed += 1
        if detail:
            print("        ↳ %s" % detail)
    print("\n%d/%d 符合期望" % (len(cases) - failed, len(cases)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
