---
name: wenan-text-proofread
description: 视频转文字流程的「第 5 步：人工校对」独立入口。把某一批的原始 ASR 三件套（1-纯文字/2-时间轴/3-字幕）按映射表改成「（校对）」三件套，并复查半角标点、残留可疑词、段落长度。当用户输入 /wenan-text-proofread、说「校对这批/这个视频」「把 ASR 稿改干净」「继续第 5 步」时使用。
whenToUse: 用户要对已下载/已识别的批次做校对，或输入 /wenan-text-proofread；也可以由 /wenan-text 在第 5 步转进来。
---

# 视频转文字 · 校对（/wenan-text-proofread）

只做 `/wenan-text` 的第 5 步（校对），不做下载/识别（第 1-4 步）、不生成 md（第 6 步）、不验收（第 7 步）。
**核心原则：ASR 结果不能直接交付；原始版禁止覆盖。**

## 触发方式

- 斜杠命令：`/wenan-text-proofread`（默认最新批）、`/wenan-text-proofread batch_0002`、`/wenan-text-proofread batch_0002 游资`
- 自然语言：`校对一下最新这批`、`把这批 ASR 稿校对成三件套`、`继续第 5 步`
- 一次只处理一个视频目录；批内有多个视频时必须指明

## 工具（scripts/proofread.py，只用标准库）

```bat
:: 看有哪些批次 / 每个视频校对没校对
.venv\Scripts\python.exe .dsh\skills\wenan-text-proofread\scripts\proofread.py --list

:: 看某批原始稿概况（字数、段数、半角、可疑词、校对版是否存在）
.venv\Scripts\python.exe .dsh\skills\wenan-text-proofread\scripts\proofread.py --report --batch batch_0002

:: 应用映射表，写出（校对）三件套（校对版已存在会拒绝，保护旧成果）
.venv\Scripts\python.exe .dsh\skills\wenan-text-proofread\scripts\proofread.py ^
  --batch batch_0002 --fixes "<批目录>\_proofread\fixes.json" --reflow

:: 确认要重跑、覆盖现有校对版
.venv\Scripts\python.exe .dsh\skills\wenan-text-proofread\scripts\proofread.py --batch batch_0002 --fixes ... --force
```

`fixes.json` 两种写法（脚本会按「错」的长度倒序应用，避免短串先破坏长串）：

```json
[["秦硕", "秦朔"], ["红国", "红果"]]
```
```json
{ "fixes": [["秦硕", "秦朔"]], "reflow": true }
```

- `--reflow`：只在纯文字稿里，把超过 235 字的段落在逗号处切开（排版用，不改内容）
- `--json`：机器可读输出
- 退出码：`0` 成功 / `1` 参数或读取错误 / `3` 校对版已存在且未加 `--force`

## 三道校对（必须都做）

1. **同音字批量替换** —— 读一遍 `1-纯文字.txt`，建 `fixes.json`：专名、术语、平台名、数字（`9.15/9.20/9.25/9.30` 不能错）
2. **逐段通读** —— 读回 `1-纯文字（校对）.txt`，改断句、语境、重复词；不确定处保留原文并标注，不硬改
3. **残留扫描** —— 看脚本复查输出：半角标点、可疑词、段落长度；需要再改就修 `fixes.json` 重跑（`--force`）

> 「哪里错了」由你逐段判断，脚本只做机械替换和复查。`wenan-text/scripts/proofread_example.py` 是映射表写法样例，
> 不要直接对成品跑（它会覆盖并降级已人工校对的版本）。

## 输出与交接

- 写出：`1-纯文字（校对）.txt` / `2-时间轴（校对）.txt` / `3-字幕（校对）.srt`，与原文件同目录
- 原始版**一个字节都不改**
- 汇报：修改类数/处数、典型修正 3~5 条、残留可疑词、校对版路径
- 完成后回 `/wenan-text` 继续：
  - 第 6 步 `build_md.py`（`--text` 必给，用校对稿）
  - 第 7 步 `check.py`

## 限制

- 不判断语义对错；专名/术语的最终取舍由人或模型结合上下文决定
- 只处理批目录里已经存在的原始三件套；缺文件会直接报错
- 默认不覆盖已有校对版（`--force` 才覆盖）
