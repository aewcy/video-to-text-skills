---
name: wenan-pre-position
description: 视频转文字（/wenan-text）的前置校验与补齐。检查 Python 环境、faster-whisper / yt-dlp / ffmpeg / app.py / 模型 / GPU / 磁盘 / 网络，缺 Python 包和模型时可自动下载（支持 hf-mirror 镜像）。当用户输入 /wenan-pre-position、问「前置/依赖/环境是否齐」「为什么跑不了视频转文字」「先检查一下」时使用。
whenToUse: 用户要跑 /wenan-text 之前做环境检查，或报缺依赖/缺模型/下载慢，或输入 /wenan-pre-position。
---

# 视频转文字 · 前置校验（/wenan-pre-position）

跑 `/wenan-text` 之前先确认环境齐不齐。**默认只检查不改环境**；用户明确要「补齐/下载/装一下」时才加 `--fix`。

## 检查什么

| 项 | 说明 |
|---|---|
| Python | 版本 ≥3.10；是否在 `.venv` 里运行 |
| Python 包 | `faster-whisper`、`yt-dlp`、`numpy`、`opencc-python-reimplemented` |
| ffmpeg / ffprobe | 是否在 PATH（抽音轨、探时长要用） |
| `app.py` | `pull.py` 依赖它，检查所需函数是否齐全（AST 静态检查，不 import） |
| 模型 | `models/<model>/model.bin` + `config.json` + `.sizes.json` 大小校验 |
| GPU | `nvidia-smi`；没有就用 CPU（只是慢） |
| 磁盘 / 可写 | 剩余空间 ≥3 GB；项目根可写 |
| 目录 | `文案/`、`outputs/`、`uploads/` |
| 网络 | 模型下载源是否可达（默认 huggingface.co，可切 hf-mirror） |

## 执行方式（脚本在 skill 的 scripts 里）

```bat
:: 1) 默认：只检查，出报告（退出码 0=全齐 / 2=有缺失 / 1=脚本异常）
.venv\Scripts\python.exe .dsh\skills\wenan-pre-position\scripts\preflight.py

:: 2) 用户要「补齐」时：装缺失包 + 下载缺失模型 + 建目录
.venv\Scripts\python.exe .dsh\skills\wenan-pre-position\scripts\preflight.py --fix

:: 3) 下载慢/被墙：切 hf-mirror（模型 ~75MB tiny / ~460MB small / ~1.5GB medium）
.venv\Scripts\python.exe .dsh\skills\wenan-pre-position\scripts\preflight.py --fix --hf-mirror

:: 4) 只要机器可读结果（给自动化/汇报用）
.venv\Scripts\python.exe .dsh\skills\wenan-pre-position\scripts\preflight.py --json

:: 5) 只看要做什么，不执行
.venv\Scripts\python.exe .dsh\skills\wenan-pre-position\scripts\preflight.py --fix --dry-run
```

用户级安装时把 `.dsh\skills` 换成 `%USERPROFILE%\.dsh\skills`。**没有 `.venv` 时**用系统 `python` 跑同一个脚本即可（脚本只用标准库）。

## 怎么解读报告

- `[✓]` 正常；`[!]` 警告（不影响跑通，例如没 GPU 会退 CPU）；`[✗]` 缺失（会跑不起来）
- 每行 `→` 后是修复建议。`--fix` 能自动处理的只有三类：**Python 包 / 模型 / 目录**
- `ffmpeg`、`.venv`、`app.py` **不会自动装**，只给命令；把这些原样转告用户，不要自作主张执行大下载或改系统

## 补齐规矩

1. 默认**不**加 `--fix`；先出报告，告诉用户「缺什么、多大、要不要现在下载」
2. 用户说「补齐/下载/装一下」→ 才跑 `--fix`；下载模型前先报体积和预计时间
3. 下载失败 → 提示加 `--hf-mirror` 重试，或手动把文件放进 `models/<name>/`
4. `--fix` 完**必须再跑一次**默认检查，确认 PASS，再让用户去 `/wenan-text`

## 与 /wenan-text 的关系

这是 `/wenan-text` 的「第 0 步」。检查 PASS 后按 `wenan-text` skill（同仓库另一个目录）继续：`pull.py` → 校对 → `build_md.py` → `check.py` → 交付。

## 限制

- 只校验**视频转文字**这条链路，不检查网页版 `app.py` 的服务、端口和前端
- 不负责安装 ffmpeg / 重建 venv / 修复 app.py（只给指引）
- 模型下载源是 HuggingFace 或其镜像；网络不通时只报告，不重试
- `--fix` 不会覆盖已存在的完整模型；大小不符的文件会删掉重下
