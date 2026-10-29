---
name: wenan-text
description: 把抖音 / B站 等视频链接转成可复制的文字稿。当用户输入「/wenan-text <链接>」或直接发来 v.douyin.com、bilibili BV 链接并要求转文字 / 提取文案 / 要字幕时使用。产出 Markdown 正文 + 时间轴 + srt 字幕，按 30 批滚动归档。
whenToUse: 用户发了抖音或B站视频链接，想要视频里的文字稿；或输入 /wenan-text 开头的指令。
---

# 视频转文字（/wenan-text）

把一条视频链接变成能读、能复制的文字稿。**用户只发链接，其余全自动**；识别完必须人工校对一遍再交付。

## 触发方式

- 斜杠命令：`/wenan-text <链接>`（DSH 中用户可调用的 skill 会自动成为斜杠命令；手输 `/wenan-text <链接>` 时正文会被内联进提示）
- 自然语言：`把这条视频转成文字 <链接>`、`文案 <链接>`、`提取这个视频的字幕 <链接>`
- 一次多条链接 = 同一批

## 环境

| 项 | 值 |
|---|---|
| 项目根 | 你的本地项目根（含 `app.py`、`models/` 的目录；脚本会从自身位置向上查找） |
| Python | `.venv\Scripts\python.exe`（faster-whisper + yt-dlp） |
| 模型 | `models\small`（快，默认）、`models\medium`（准，慢 2~3 倍） |
| GPU | 有 NVIDIA GPU 时自动用 float16，否则退 CPU |
| 脚本 | `<项目根>\.dsh\skills\wenan-text\scripts\`（项目级安装）或 `%USERPROFILE%\.dsh\skills\wenan-text\scripts\`（用户级安装） |

依赖（本仓库不含）：Python 3.12 + venv、`faster-whisper`、`yt-dlp`、`ffmpeg`、`models/small` 或 `models/medium`，以及本地的 `app.py`——`pull.py` 会复用它的 `load_model` / `load_wav_16k` / `extract_audio` / `probe_media` / `normalize_cjk` / `parse_srt`。

## 流程（7 步，不可跳）

### 1 解析
- B站 / 其它站点：`yt-dlp` 直连
- 抖音：`pull.py` **先**走在线解析接口（第三方 `video.zacao.top`，会把链接发给对方）拿直链；解析失败才回落到 `yt-dlp`
  - ⚠️ 接口返回里**优先取 `video_url`**；`audio_url` 常是背景音乐轨（~60 秒），只在没有 `video_url` 时兜底，用错会只出一句话

### 2 下载
- yt-dlp 路径：`--retries 5 --fragment-retries 5`，默认续传 `.part`
- 抖音解析直链：单次下载、无重试，失败就整条重跑；需带 `Referer: https://www.douyin.com/`

### 3 字幕优先
- 只认 yt-dlp 抓到的 **`.srt` 侧挂字幕**（`--write-subs --write-auto-subs --convert-subs srt`，优先 zh/ai-zh），有就直接用，**不跑 ASR**
- 内嵌 `mov_text` / `ass` / `webvtt` 轨不提取（那是配套本地网页版 `app.py` 的能力）；没有侧挂 srt 就正常跑 ASR

### 4 识别
`faster-whisper`，GPU float16，VAD 开，`temperature=[0.0]`（不做回退重试，否则长视频卡死）。
默认 `small`（快）；显式 `--model medium` 且视频 ≤ 10 分钟才用 `medium`，超过 10 分钟脚本自动降回 `small`。

### 5 校对（必做，交付前）

> ASR 结果**不能直接交付**。small 模型同音字错误很多。

三道：
1. **同音字批量替换** —— 参考 `scripts/proofread_example.py`，按本条内容建映射表
2. **逐段通读** —— 改专名、术语、数字（9.15/9.20/9.25/9.30 不能错）、断句
3. **残留扫描** —— 半角标点、可疑同音词、重复词

产出 `1-纯文字（校对）.txt` / `2-时间轴（校对）.txt` / `3-字幕（校对）.srt`，**原始版禁止覆盖**。

### 6 生成 Markdown

```bat
:: 下面按项目级安装路径写；用户级安装把 .dsh\skills 换成 %USERPROFILE%\.dsh\skills
.venv\Scripts\python.exe .dsh\skills\wenan-text\scripts\build_md.py ^
  --srt "<批目录>\3-字幕（校对）.srt" ^
  --text "<批目录>\1-纯文字（校对）.txt" ^
  --outdir "文案\<日期>_<短标题>" ^
  --title "<完整标题>" --url "<链接>" --author "<作者>" ^
  --model small --proofread "3 轮 N 处" ^
  --sections "<批目录>\sections.txt" --when "<YYYY-MM-DD>"
```

- `--text` **必给**：跨字幕条的修正只在段落文本里生效；缺省时脚本会静默从 srt 重建段落（不报错，但修正会丢）
- `--sections`（可选）：一行一个分节标记，按顺序切成 `## 小标题`，写视频里真实出现的那句
- 分节标记要和正文标点完全一致（`四、个股选择` ≠ `四，个股选择`），否则切不出来——用了 sections 就必须核对标题数量（脚本打印的标题数应与 `sections.txt` 行数一致）

### 7 验收 + 交付

```bat
.venv\Scripts\python.exe .dsh\skills\wenan-text\scripts\check.py
```

检查：校对版是否存在、语速（120~450 字/分钟）、半角标点、残留同音词、段落长度、字幕时间轴单调性。结果只有 **PASS（exit 0）** 和 **WARN（exit 2）**，没有 FAIL。

全部 PASS 后：
1. 更新 `文案\index.md`（加一行：日期 / 标题 / 时长 / 字数 / 链接）
2. 对话里用**文件卡片**给出 `正文.md`、`时间轴.md`、`字幕.srt`

## 产出结构

```
文案\
  <日期>_<短标题>\
    正文.md      ← 不带时间轴，分节分段，用来读和复制
    时间轴.md    ← 每行一条 [时:分:秒] 文字
    字幕.srt     ← 播放器 / 剪映用
  index.md       ← 总索引

outputs\batches\batch_NNNN\<标题>\   ← 原始归档：未校对 ASR + 校对版 + meta，滚动 30 批
```

`正文.md` 头部固定元信息：

```markdown
# 标题

> **来源**：url　·　**作者**：xxx　·　**时长**：16.7 分钟　·　**字数**：5627　·　**识别**：faster-whisper small（GPU）　·　**校对**：3 轮 189 处　·　**处理时间**：2026-10-29

---
```

## 异常处理

| 现象 | 处理 |
|---|---|
| 抖音 `Fresh cookies` | 正常，走在线解析接口 |
| 抖音只出 1 句 / 6 个字 | 用错 `audio_url` 了，换 `video_url` |
| 识别长时间不动 | 查 GPU 占用（nvidia-smi），降 `--model small`，必要时重启 |
| 无人声 / 纯音乐 | 如实告知，不出空文件 |
| 下载断流 | 断点续传已内置，仍失败就重试 |
| 网络很慢 | 告知预计时间，不硬等 |

## 命令速查

```bat
:: 跑一条（默认 small）
.venv\Scripts\python.exe .dsh\skills\wenan-text\scripts\pull.py "<链接>"

:: 要更准（且 ≤10 分钟才真的会用 medium）
.venv\Scripts\python.exe .dsh\skills\wenan-text\scripts\pull.py "<链接>" --model medium

:: 一批
.venv\Scripts\python.exe .dsh\skills\wenan-text\scripts\pull.py "<链接1>" "<链接2>"

:: 验收
.venv\Scripts\python.exe .dsh\skills\wenan-text\scripts\check.py batch_0001
```
