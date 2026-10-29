# video-to-text-skills

把**抖音 / B站等视频链接**变成可复制文字稿的 [DSH](https://github.com/deepseek-ai/deepseek-harness) skill：
解析 → 下载 → 字幕优先 / faster-whisper 识别 → **人工校对** → 生成 Markdown（正文 + 时间轴）+ srt 字幕 → 自动验收 → 按 30 批滚动归档。

## 仓库结构

```
video-to-text-skills/
├── wenan-text/                 ← 主 skill：链接 → 文字稿
│   ├── SKILL.md                ← 流程正文：7 步、验收标准、异常处理、命令速查
│   └── scripts/
│       ├── pull.py             ← 链接 → 下载 → 字幕/ASR → 三件套 → 归档（30 批滚动）
│       ├── build_md.py         ← 校对稿 → 正文.md + 时间轴.md + 字幕.srt
│       ├── check.py            ← 验收（PASS / WARN）
│       └── proofread_example.py← 校对映射表写法样例（不是通用工具，见下）
└── wenan-pre-position/         ← 前置 skill：环境校验 / 补齐
    ├── SKILL.md
    └── scripts/preflight.py    ← 检查 Python 包/ffmpeg/app.py/模型/GPU/磁盘/网络
```

## 安装到 DSH
复制连接，找ai装。

（把这个仓库链接发给 AI，让它把 `wenan-text/`、`wenan-pre-position/` 复制到 `<项目根>/.dsh/skills/`
或 `%USERPROFILE%\.dsh\skills\`；DSH 按最近的 `.git` 判定项目根，没有 `.git` 时用用户级目录。）

## 前置校验（推荐先跑）

装好后先跑 `/wenan-pre-position`（或直接用脚本）：

```bat
:: 只检查，出报告（退出码 0=齐全 / 2=有缺失）
.venv\Scripts\python.exe .dsh\skills\wenan-pre-position\scripts\preflight.py

:: 缺 Python 包 / 模型时自动补齐；下载慢加 --hf-mirror
.venv\Scripts\python.exe .dsh\skills\wenan-pre-position\scripts\preflight.py --fix --hf-mirror

:: 只看要做什么，不执行
.venv\Scripts\python.exe .dsh\skills\wenan-pre-position\scripts\preflight.py --fix --dry-run
```

检查项：Python 版本 / 是否在 `.venv`、`faster-whisper` / `yt-dlp` / `numpy` / `opencc`、`ffmpeg`、
`app.py` 及所需函数、`models/<model>` 完整性、GPU、磁盘空间、目录可写、模型下载源是否可达。
`--fix` 只会自动处理**Python 包 / 模型 / 目录**；`ffmpeg`、`.venv`、`app.py` 缺失时只给安装命令，不会乱动系统。

## 依赖

本仓库**只含 skill 与脚本**，真正的运行环境需要自备：

| 依赖 | 说明 |
|---|---|
| Windows + Python 3.12 | 脚本按 Windows 路径/venv 写；换系统要改路径 |
| `faster-whisper` | 本地 ASR，GPU 用 float16，无 GPU 自动退 CPU |
| `yt-dlp` + `ffmpeg` | 下载与抽音轨 |
| `models/small`（约 460 MB）或 `models/medium`（约 1.5 GB） | faster-whisper 模型目录 |
| **本地 `app.py`** | `pull.py` 复用它的 `load_model` / `load_wav_16k` / `extract_audio` / `probe_media` / `normalize_cjk` / `parse_srt`。**本仓库不包含 app.py**，需要你自己的项目根里有（或把这些调用换成自己的实现） |

另外，抖音解析走第三方在线接口 `video.zacao.top`（会把你的链接发给对方），失败才回落 yt-dlp；
介意的话可以改 `pull.py` 里的 `PARSE_API`，或只用 B站 / 其它 yt-dlp 支持的站点。

## 用法

```bat
:: 一条（默认 small）
.venv\Scripts\python.exe .dsh\skills\wenan-text\scripts\pull.py "<链接>"

:: 要更准（≤10 分钟才会真的用 medium，超时会自动降 small）
.venv\Scripts\python.exe .dsh\skills\wenan-text\scripts\pull.py "<链接>" --model medium

:: 一批（同一批号）
.venv\Scripts\python.exe .dsh\skills\wenan-text\scripts\pull.py "<链接1>" "<链接2>"
```

跑完得到：

```
outputs/batches/batch_0002/<视频标题>/
    1-纯文字.txt      ← 未校对 ASR（原始版，禁止覆盖）
    2-时间轴.txt
    3-字幕.srt
    meta.json
```

然后按 `SKILL.md` 第 5 步做**三道校对**，产出 `1-纯文字（校对）.txt` / `2-时间轴（校对）.txt` /
`3-字幕（校对）.srt`，再生成成品：

```bat
.venv\Scripts\python.exe .dsh\skills\wenan-text\scripts\build_md.py ^
  --srt "<批目录>\3-字幕（校对）.srt" ^
  --text "<批目录>\1-纯文字（校对）.txt" ^
  --outdir "文案\<日期>_<短标题>" ^
  --title "<完整标题>" --url "<链接>" --author "<作者>" ^
  --model small --proofread "3 轮 N 处" ^
  --when "<YYYY-MM-DD>"

.venv\Scripts\python.exe .dsh\skills\wenan-text\scripts\check.py batch_0002
```

成品结构：

```
文案\<日期>_<短标题>/
    正文.md      ← 不带时间轴，按段落/小标题排好，用来读和复制
    时间轴.md    ← 每行一条 [时:分:秒] 文字
    字幕.srt     ← 播放器 / 剪映用
```

`check.py` 检查校对版是否存在、语速（120~450 字/分钟）、半角标点、残留同音词、段落长度、
字幕时间轴单调性；结果只有 **PASS（exit 0）** / **WARN（exit 2）**，没有 FAIL。

## 关于校对

`scripts/proofread_example.py` 是**映射表写法样例**，不是通用工具：

- 它的 `FIXES` 只覆盖某一期视频的同音字/错字，直接对别的批次跑没意义；
- 它会**直接覆盖**批次的校对三件套，对已经人工校对过的内容重跑会静默降级。

正确用法是复制一份、按当前视频内容重建 `FIXES`，再跑。校对三道：

1. 同音字批量替换（专名、术语、平台名）
2. 逐段通读改断句、数字、语境（`9.15 / 9.20 / 9.25 / 9.30` 这类数字不能错）
3. 残留扫描：半角标点、可疑同音词、重复词

## 限制

- 不支持需登录/付费内容；纯图集、无人声/纯音乐不出空文件，会如实报告
- 只抽 `.srt` 侧挂字幕，内嵌 `mov_text` / `ass` / `webvtt` 字幕轨不处理
- 单条建议 ≤ 60 分钟；识别速度约 small 6~8 倍实时、medium 2.5~3 倍实时（RTX 4070 Laptop 实测）
- 原始 ASR 归档滚动保留最近 30 批
