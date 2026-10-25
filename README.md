# 📚 AIforstudy — AI 驱动的学习资料平台

> **AI for Study**：上传任意学习资料（视频 / PDF / PPT / Word / 图片 / 文本），
> AI 帮你**定位、理解、答疑**——它知道你正在看哪一页、哪一讲，回答都聚焦于你的学习进度。

## ✨ 核心能力

- **文件中心**：左侧是资源管理器级的托管库——上传复制、新建/重命名/移动/删除（回收站）、
  拖拽上传、动态科目（每个科目资料与会话独立隔离，AI 模型配置全局共享）
- **全类型查看器**：PDF/Office（入库自动转 PDF）、图片、文本/Markdown、音频；
  视频播放器带黑边自适应、字幕调节、快捷键、进度记忆
- **AI 上下文编排**：右侧 agent 自动注入「科目知识地图 → 文档总览 → 完整目录 →
  当前章节路径 → 当前章节摘要（懒生成）→ 当前页原文 → 前后邻页 → 关键词检索片段」，
  让你在第 N 页提问，它就用第 N 页的上下文回答
- **三个问答模式**：💬 即时问答 / 🎯 引导输出（AI 出题引导）/ 📝 课后问答
- **省钱设计**：章节摘要按需懒生成并缓存；关键词检索零成本；单文档 LLM 调用有闸门（20 次）
- **模型**：DeepSeek 聊天（SSE 流式）/ 智谱看图 / 千问 ASR 字幕——本地直连，Key 放自己手里

## 🖼️ 界面

```
┌─────────────┬──────────────────────────────┬──────────────┐
│ 📚 资源管理器 │  全类型查看器（PDF/视频/Office）│ 💬 AI 助教    │
│ 数学/PLC 科目 │  章节大纲 ≡ / 📷 截图 / 快捷键   │ 上下文自动跟随 │
└─────────────┴──────────────────────────────┴──────────────┘
```

---

## 快速启动

### 环境要求
- Python 3.11+
- ffmpeg（`winget install ffmpeg`）

### 步骤

```bash
# 1. 安装依赖
cd backend
pip install -r requirements.txt

# 2. 配置 .env（复制模板填入 Key）
copy ..\.env.example ..\.env
#    DEEPSEEK_API_KEY = 聊天（必填）
#    ZHIPU_API_KEY    = 看图
#    QWEN_API_KEY     = 字幕识别
#    PLATFORM_TOKEN   = （可选）访问令牌，开启后接口需鉴权

# 3. 启动
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# 4. 打开 http://localhost:8000
```

### 或双击脚本
`scripts\考研学习平台.bat`（自动检查依赖并启动；服务已在运行时会询问是否重启）

---

## 使用

1. **上传视频**：选择科目 → 上传 MP4 → 自动提取字幕（识别引擎在 ⚙️ 设置页选，默认本地 Whisper，几分钟）
   也可以直接把视频放进 `storage/videos/<科目>/`，再点侧栏 `🔎` 重新扫描即可出现在列表
2. **学习**：选视频播放（自动**从上次播放位置续播**）→ 切换 Agent：
   - **A 即时问答**：边看边问（自动带**整讲字幕全文 + 当前播放位置**，agent 完整知道你学到哪），可发图片（草稿/笔记截图）
   - **B 引导输出**：让 AI 出题/追问，答错给提示（勾选视频后，优先用**课程总结**，更结构化）
   - **C 课后问答**：自由提问
3. **🎤 语音输入**：输入框左侧麦克风按钮 → 说话 → 再点停止，识别文字填入输入框，改完回车发送。引擎在设置页**独立配置**（与视频字幕解耦）：
   - `本地 Whisper`：**免费离线**，支持**实时草稿**（说话时边说边出字，每 2.5s 刷新）——推荐日常用
   - `自动/千问云`：qwen3-asr-flash（**约 ¥0.013/分钟**，按音频时长计费，非免费），电脑零负担，无实时草稿（防费用放大）
4. **字幕文件**：自动存 `storage/subtitles/`，可点 📄 下载
5. **🎬 识别引擎**（设置页「识别引擎（视频字幕用）」）：
   - `本地 Whisper`：**只用本地**，免费离线，逐句**真实**时间戳（实测约 23 倍实时，4 分钟视频约 20 秒）；
     本地失败会**明确报错**（日志 `[asr] 引擎 local 识别失败`），**不会**偷偷改用云引擎
   - `自动降级链`：智谱 → 千问 → 腾讯 → 硅基流动 → 本地（按已配 Key 的引擎依次尝试）
   - 指定单个云引擎：只用该引擎
   > ⚠️ 云引擎**不返回句子级时间戳**，字幕时间是按「字数 × 220ms + 句尾吸附静音」估算的，
   > 会有秒级偏差且随播放累积滞后。要精确时间轴请用「本地 Whisper」。
   > 每次识别完成都会打印实际生效的引擎：`[asr] 引擎 local 识别完成：N 段`。
6. **🧠 思考强度**：输入区上方「🧠 思考强度」下拉（会话级），与「🤖 模型」下拉并列，切换后仅当前会话生效。
   档位 `低(min)/中(m)/高(h)/非常高(xh)/极限(max)`；实为 DeepSeek 的 `reasoning_effort`（仅认低/中/高），
   `非常高/极限` 在发送时自动降级为 `高`。日常问答用 `低` 更快更省，难题用 `高/非常高` 更稳。

## 课程总结（自动生成 + 一键补生成）

字幕提取完成后，后端自动为每节课生成一份**结构化课程总结**（分段提取 → 合并防漏，保信息密度）：

- 内容五段式：**知识点（带时间戳）/ 核心公式 / 例题与题型 / 方法技巧 / 易错点**
- 视频树中 `📄` 可查看/下载 markdown；无总结的字幕已就绪视频显示 `📄生成`（单节手动生成），失败显示 `📄重试`
- **存量视频一键补齐**：侧栏 `📑` 按钮把「字幕已完成但无总结」的视频全部排队串行生成（约 10~60 秒/个，后台执行不阻塞页面）
- 模式 B 引导输出优先使用勾选视频的总结（高密度、跨多节不撑爆上下文）；
  尚无总结的视频自动回退字幕全文
- 一份总结仅 1~2 千 token，是全文的 1/10，适合作为长期学习画像

> 模型上下文为 1M token：单节全文（约 1.5 万 token）与多节总结都毫无压力，
> 截断阈值仅作极端防御。
> ⚡ 设置页勾选「模式A 精简上下文」后，即时问答只发「课程总结 + 播放位置前后 ±10 分钟字幕」，
> 回答更快更省（默认不勾选，保持全文上下文）。

---

## 模型 API 设置（⚙️ 设置）

页面右上角 ⚙️ 可配置全部模型（**OpenAI 兼容**，保存即生效，无需重启）：

- **🧠 主模型**：API 地址 + Key + 模型名（聊天/归纳总结用）
- **👁️ 视觉辅助**：启停开关 + 地址 + Key + 模型名（默认智谱 GLM-4V）
- **🎬 语音识别 ASR**：地址 + Key + 模型名（默认千问 qwen3-asr-flash）
- **🔐 访问令牌**：PLATFORM_TOKEN 的本地保存

**图片处理逻辑（工具化看图）**：
- 上传的图片先存到 `storage/uploads/`，消息里只把本地路径告诉模型
- 模型调用 `read_image`（自己就是多模态、直接看图）或 `modlens_read_image`（走视觉辅助模型转成逐字转录 + 版面 + 语义的结构化文字）取图后再回答

配置存服务器数据库（`platform_config` 表），`.env` 中的同名 Key 作为首次启动兜底；
Key 回显只显示掩码，不泄露明文。

---

## 路径约定（搬家 / 改名 / 换盘都不会丢数据）

数据库里 **只存相对项目根的路径**，例如：

```
storage/videos/math/20260803_180400_xxx.mp4
storage/subtitles/12.txt
storage/summaries/12.md
```

读文件时由 `backend/app/core/paths.py` 拼上当前项目根（`BASE_DIR`）。好处：

- **项目改名 / 换目录 / Windows↔Linux 都不用改库**——相对路径天然跟着项目走
- 老数据里的绝对路径**读取时仍然兼容**（升级版本不会导致视频播不了）
- 后端**每次启动自动巡检**（`core/path_migration.py`）：绝对路径统一转相对；
  万一记录失效（例如从别处拷回旧库），只要文件名还在 `storage/` 下，就按文件名**自动找回**
- 彻底找不到的记录**保留原值**、只在日志里告警，不会擅自清空

想单独体检 / 手动修复（一般用不到，启动时已自动做过）：

```bash
python scripts/migrate_paths_after_rename.py           # 预演，只看不改
python scripts/migrate_paths_after_rename.py --apply   # 备份数据库后写库
```

> 历史坑（已修）：videos 表早期存绝对路径，项目从 `Desktop\DIFY考研学习平台\本地文件`
> 挪到 `Desktop\项目\考研学习平台\本地文件` 后，63 条记录全指向旧目录 → 视频一律
> "加载失败"，而文件其实一个都没丢。

---

## 安全（访问令牌）

平台默认**不鉴权**（`PLATFORM_TOKEN` 留空），局域网内任何设备都能访问。
多人共用或多设备使用时，建议：

1. 在 `.env` 设置 `PLATFORM_TOKEN=你的随机令牌`
2. 重启服务
3. 页面右上角 ⚙️ 设置 → 填写令牌 → 保存

之后所有 `/api` 接口都需要令牌（`Authorization: Bearer`；视频流因 `<video>` 标签无法带请求头，自动走 `?token=` 查询参数）。模型 API Key 存于服务器数据库，接口受令牌保护。

---

## 项目结构

```
AIforstudy/
├── backend/                   # FastAPI 后端（裸跑）
│   ├── requirements.txt
│   ├── app/
│   │   ├── main.py            # 入口（含鉴权中间件）
│   │   ├── models.py          # ORM 模型（SQLAlchemy）
│   │   ├── core/
│   │   │   ├── config.py      # 配置（settings + 项目根计算）
│   │   │   ├── database.py    # 引擎 / 会话 / 建表迁移
│   │   │   ├── paths.py       # 路径统一层（库里存相对路径，读时拼项目根）
│   │   │   └── path_migration.py  # 启动巡检：绝对路径转相对 + 失效文件重定位
│   │   ├── api/
│   │   │   ├── deps.py        # 公共依赖（get_db）
│   │   │   └── routes/
│   │   │       ├── videos.py  # 视频/字幕(ASR)
│   │   │       ├── chat.py    # 聊天（SSE 流式，直连 DeepSeek/智谱）
│   │   │       ├── folders.py # 文件夹
│   │   │       └── config.py  # 配置状态（只读）
│   │   ├── schemas/           # Pydantic 请求模型
│   │   └── services/
│   │       ├── llm_service.py # DeepSeek 流式聊天 + 智谱看图
│   │       └── asr_service.py # 千问 ASR
│   └── tests/                 # 单元测试（unittest，无 pytest 依赖）
├── frontend/                  # 前端页面（原生 JS + KaTeX）
│   ├── index.html
│   ├── css/  js/  vendor/
├── scripts/                   # 独立工具脚本（一键启动.bat / 字幕 / 转码 / 云端同步）
│   └── _legacy/               # 一次性修复·迁移·排查脚本（留档，不再维护）
├── storage/                   # 运行时数据：videos/（视频）subtitles/（字幕）summaries/（课程总结）
├── data/                      # SQLite + 日志（运行时数据）
├── _archive/                  # 历史备份 / 排错残留（已 gitignore）
├── .env / .env.example        # API Key + PLATFORM_TOKEN 配置
├── .gitignore
└── README.md
```

### 运行单元测试

```bash
cd backend
python -m unittest discover -s tests -v
```

- `test_smoke`：基础路由与参数校验
- `test_stream_mock`：MockTransport 内存模拟 DeepSeek 的 SSE 响应，验证流式事件序列与落库
- `test_summary`：MockTransport 模拟「分段提取 → 合并」两轮调用，验证课程总结生成与状态流转
- `test_paths`：路径层（相对/绝对互转、搬家后按文件名重定位找回）

全部测试**不访问外网、不消耗 API 配额**；各 mock 模块通过 setUpModule/tearDownModule
隔离全局替换，可同进程并行。

---

## 常见问题

- **平台打不开**：双击 `scripts\考研学习平台.bat`（弹窗开着即运行，关窗即停）
- **服务更新后想重启**：再双击一次脚本，按提示输入 Y 即结束旧进程并重启
- **看图报错 429**：智谱免费模型限流，等 1-2 分钟重试
- **字幕提取失败**：若设置页识别引擎是「本地 Whisper」，需先 `pip install faster-whisper`；
  失败原因看日志里的 `[asr] 引擎 local 识别失败：…`（本地引擎不再静默回退云引擎）。用云引擎则检查对应 Key 与网络
- **字幕时间戳对不上 / 整体滞后**：说明该字幕是云引擎生成的（云 ASR 无句子级时间戳，只能按字数估时）；
  把识别引擎切到「本地 Whisper」重新识别该视频即可，完成后日志会打印 `[asr] 引擎 local 识别完成：N 段`
- **接口提示"未授权"**：服务器 `.env` 已配 `PLATFORM_TOKEN`，请在 ⚙️ 设置中填写
- **搬家 / 改名后视频全"加载失败"**：不用手动改库。重启服务即可——启动巡检会把路径转成
  相对路径、失效的按文件名找回（详见上文「路径约定」）。想先看体检报告：
  `python scripts/migrate_paths_after_rename.py`
- **starlette 报错**：确认 `pip install starlette==1.6.0`（fastapi 0.141.1 配套版本）
---

## 📚 文件中心（全资料 AI 辅助，2026-02 改造）

平台已从"视频中心"升级为"**文件中心**"：几乎所有学习资料（PPT/PDF/Word/图片/文本/音视频）统一上传、统一查看、统一 AI 辅助。

### 三区架构

- **左侧 · 托管资源管理器**：上传复制制（文件拷贝进 `storage/library/`，不碰原始磁盘），
  支持新建/重命名/移动/删除（回收站）/搜索/拖拽上传/批量删除，操作体验对齐资源管理器。
- **中间 · 全类型查看器**：PDF 用 pdf.js（本地 vendor）渲染，Office（pptx/docx/xlsx）入库时自动
  转成 PDF 后同一路径查看，图片/文本/Markdown/音频原生支持，视频沿用原播放器（字幕/倍速/总结全保留）。
  PDF 支持文本层划词（划词后点"🤖 问 AI"直接提问）与阅读位置记忆。
- **右侧 · 上下文编排 agent**：上下文跟随当前查看的文件（会话绑定文件）。预编排注入
  总结/全文摘录/当前查看位置；模型还可用沙箱工具 `read_artifact`/`list_artifacts` 自己翻当前文件的产物
  （作用域锁死在 `storage/artifacts/{file_id}/`）。聊天面板 🧩 按钮可实时预览实际注入内容，
  ⚙️ 设置里可按文件类型配置编排模板——编排器不是黑盒。

### 数据与产物

- 统一文件表 `files`（旧 videos 数据已迁移，**id 不变**，字幕/总结/会话引用全部有效）；
  视频特有字段在 `video_meta`；后台任务在 `file_tasks`。
- AI 产物集中在 `storage/artifacts/{file_id}/`：`derived.pdf`（Office 转换产物）、
  `extracted.md`、`summary.md`、`chunks.jsonl`、`pages.json`、`thumb.png`、`meta.json`。
- 回收站：`storage/library_trash/`，可恢复或彻底删除。

### Office → PDF 转换引擎（外部依赖）

Office 文件入库时需要转换引擎把 pptx/docx/ppt 转成 PDF（保真度高、提取管线统一）：

1. **LibreOffice（当前使用，推荐）**：装完即可用，程序按顺序自动查找
   —— PATH 里的 `soffice` → `C:/Program Files/LibreOffice/program/soffice.exe`
   → `Program Files (x86)` → 扫描 `LibreOffice*` 目录。**本机已安装 26.8.1 并验证通过**
   （55 页 PPT 转 PDF，中文与图片完整还原）。
   转换时使用独立 profile（`data/_lo_profile`），避免残留进程锁住默认 profile 导致静默失败。
2. **x2t（可选，保真度更高）**：OnlyOffice 的转换引擎。在 ⚙️ 设置 →「上下文编排 & 文件转换」里填路径
   （Windows 可用 `FileConverter/bin/x2t.exe`，依赖库与字体索引需在同目录）。配置后优先于 LibreOffice。
3. 两者都没有：Office 文件可上传、可下载，状态标记为处理失败并提示配置；原文件始终保留。

> 已知失真：字体未安装会被替换；PPT 动画/嵌入音视频不会进 PDF；复杂 SmartArt 偶有差异。
> 原文件永远保留，查看器留「下载原文件」入口。

### 播放器功能与快捷键

- **黑边自适应**：容器按视频宽高比贴合，不再上下大片黑边
- **字幕**：字号 `[` `]` 调节、底板浓淡 `◐` `◑` 调节、`S` 或 📄 显示/隐藏（设置持久化到浏览器）
- **快捷键**（视频窗格可见且未聚焦输入框时生效）：

| 键 | 功能 | 键 | 功能 |
|---|---|---|---|
| `空格` / `K` | 播放/暂停 | `←` / `→` | 后退/前进 5 秒 |
| `J` / `L` | 后退/前进 10 秒 | `↑` / `↓` | 音量 ±10% |
| `M` | 静音切换 | `F` | 全屏 |
| `0`–`9` | 跳到 0%–90% | `[` `]` | 字幕字号 ± |
| `S` | 字幕显示/隐藏 | | |

- **进度记忆**：每 3 秒 + 暂停/拖动/关闭页面时落盘，重新打开自动续播
- **倍速工具栏**：鼠标移入视频区域时显示（0.5x–5x、全屏、画中画、截图开关）

### 开发自查工具

改完前端想确认效果（不依赖手工点页面）：

```
scripts\截图UI.bat                     # 截当前界面到 data\ui_shot.png
node scripts/screenshot_ui.mjs <token> out.png "<页面里执行的 async 表达式>"
```

第二种用法可带一段在页面里执行的表达式，返回值会打印（`RESULT:`），用来断言 DOM 状态。

> **注意：ES module 子模块的缓存坑**
> `main.js` 带 `?v=N`，但它内部 `import './tree.js'` 不带版本号 —— 只 bump 入口版本号时，
> 子模块仍会被浏览器缓存，导致「改了看不到效果」。后端已对 `/js/` `/css/` 下所有文件
> 返回 `Cache-Control: no-cache, no-store`，改完**普通刷新即生效**。

### 常用接口（/api/fs/*、/api/files/*）

`GET /api/fs/tree`、`POST /api/fs/upload`、`POST /api/fs/mkdir`、`PATCH /api/fs/file/{id}/rename`、
`POST /api/fs/file/{id}/move`、`DELETE /api/fs/file/{id}`（进回收站）、
`GET /api/files/{id}/view`（查看流，Office 自动给转换后的 PDF）、
`GET /api/files/{id}/status`、`GET /api/files/{id}/text?page=N`、
`GET/POST /api/fs/settings`（编排模板 + x2t 配置）。

### 迁移与维护

- 迁移脚本：`python scripts/migrate_files_center.py`（幂等，可重复执行；`--dry-run` 预览）
- 开发计划全文：工作区根目录 `开发计划-全资料AI辅助.md`
