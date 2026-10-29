#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
发链接 -> 下载（抖音 / B站等 yt-dlp 支持的站点）-> 转录 -> 存三种格式

用法：
    python pull.py <链接1> [链接2 ...] [--lang zh] [--model small]

产物（每批一个文件夹，滚动保留最近 30 批）：
    outputs/batches/batch_0001/<视频标题>/
        1-纯文字.txt      # 分段纯文本，直接复制
        2-时间轴.txt      # 每行 [时:分:秒] 文字
        3-字幕.srt        # 标准字幕文件
        meta.json         # 元信息
    outputs/batches/index.json   # 批次索引（含第 31 批自动清理旧批）
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

def _project_root(p: Path) -> Path:
    for q in [p, *p.parents]:
        if (q / "app.py").exists() or (q / "models").exists():
            return q
    return p.parents[3]


BASE = _project_root(Path(__file__).resolve())  # 项目根目录
sys.path.insert(0, str(BASE))

import app  # 复用转录/导出逻辑

BATCH_ROOT = BASE / "outputs" / "batches"
INDEX_PATH = BATCH_ROOT / "index.json"
KEEP_BATCHES = 30
DEFAULT_MODEL = "small"


def pick_model(pref: str) -> str:
    """指定的模型还没下载就先用 small。"""
    models = BASE / "models"
    if (models / pref / "model.bin").exists():
        return pref
    for fallback in ("medium", "small", "tiny"):
        if (models / fallback / "model.bin").exists():
            return fallback
    return pref

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def log(*a):
    print(*a, flush=True)


# --------------------------------------------------------------------------
# yt-dlp
# --------------------------------------------------------------------------
def run_ytdlp(args: list[str], timeout: int = 3600) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-m", "yt_dlp"] + args
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)


def fetch_meta(url: str) -> dict:
    p = run_ytdlp(["--no-warnings", "--no-playlist", "-J", url], timeout=600)
    if p.returncode != 0:
        tail = (p.stderr or "").strip().splitlines()[-3:]
        raise RuntimeError("解析失败：" + " | ".join(tail))
    return json.loads(p.stdout)


def download_media(url: str, work: Path) -> Path:
    """下音频，顺手把能拿到的字幕也抓下来。返回音频文件路径。"""
    work.mkdir(parents=True, exist_ok=True)
    args = [
        "--no-warnings", "--no-playlist",
        "-f", "bestaudio/best",
        "-o", str(work / "media.%(ext)s"),
        "--write-subs", "--write-auto-subs",
        "--sub-langs", "zh-Hans,zh-CN,zh,zh-Hant,zh-TW,en,ai-zh",
        "--convert-subs", "srt",
        "--retries", "5", "--fragment-retries", "5",
        "--no-write-info-json",
        url,
    ]
    p = run_ytdlp(args, timeout=7200)
    if p.returncode != 0:
        tail = (p.stderr or "").strip().splitlines()[-4:]
        raise RuntimeError("下载失败：" + " | ".join(tail))

    media = [f for f in work.iterdir() if f.is_file() and f.suffix.lower() not in {".srt", ".vtt", ".json", ".part"}]
    if not media:
        raise RuntimeError("下载完成但找不到音视频文件")
    media.sort(key=lambda f: f.stat().st_size, reverse=True)
    return media[0]


def pick_subtitle(work: Path) -> Path | None:
    subs = sorted(work.glob("*.srt"))
    # 优先中文
    for s in subs:
        n = s.name.lower()
        if "zh" in n or "ai-zh" in n:
            return s
    return subs[0] if subs else None


# --------------------------------------------------------------------------
# 转录
# --------------------------------------------------------------------------
def transcribe(wav: Path, model_name: str, lang: str):
    model, device = app.load_model(model_name)
    audio = app.load_wav_16k(wav)
    prompt = "以下是普通话的句子，使用简体中文，并加上标点符号。" if (lang or "").startswith("zh") else None
    seg_iter, info = model.transcribe(
        audio,
        language=None if lang == "auto" else lang,
        beam_size=5,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        condition_on_previous_text=False,
        temperature=[0.0],          # 不做温度回退，避免长视频反复重试卡住
        no_speech_threshold=0.6,
        compression_ratio_threshold=2.4,
        initial_prompt=prompt,
        word_timestamps=False,
    )
    total = float(getattr(info, "duration", 0) or 0)
    detected = getattr(info, "language", "") or lang or "zh"
    segs = []
    last_log = 0.0
    for s in seg_iter:
        t = app.normalize_cjk((s.text or "").strip(), detected)
        if t:
            segs.append({"start": round(float(s.start), 2), "end": round(float(s.end), 2), "text": t})
        now = time.time()
        if now - last_log > 20:
            pct = (float(s.end) / total * 100) if total else 0
            log(f"    识别中… {pct:.0f}%  已 {len(segs)} 句")
            last_log = now
    return segs, detected, device


# --------------------------------------------------------------------------
# 输出
# --------------------------------------------------------------------------
def safe_name(name: str, limit: int = 24) -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name or "").strip(" ._")
    name = re.sub(r"\s+", " ", name)
    return (name[:limit] or "未命名").strip()


_END_PUNCT = set("。！？，、；：…）】》”’")


def _smart_join(buf: list[str], lang: str) -> str:
    """中文拼句：上一句没标点就补一个逗号，避免几句话说成一长串。"""
    if lang not in app.CJK_LANGS:
        return " ".join(buf)
    out = ""
    for t in buf:
        if not out:
            out = t
            continue
        if out[-1] not in _END_PUNCT:
            out += "，"
        out += t
    return out


def make_paragraphs(segs: list[dict], lang: str, max_chars: int = 120) -> list[str]:
    """按句子攒段落：到 120 字左右、且刚好句末就断，读起来才像文章。"""
    cjk = lang in app.CJK_LANGS
    enders = "。！？!?…" if cjk else ".!?"
    paras: list[str] = []
    buf: list[str] = []
    for s in segs:
        t = (s.get("text") or "").strip()
        if not t:
            continue
        buf.append(t)
        joined = _smart_join(buf, lang)
        if len(joined) >= max_chars and (t[-1:] in enders or len(joined) >= max_chars * 1.6):
            paras.append(joined)
            buf = []
    if buf:
        paras.append(_smart_join(buf, lang))
    return [p for p in paras if p]


def write_outputs(video_dir: Path, segs: list[dict], lang: str, meta: dict) -> dict:
    video_dir.mkdir(parents=True, exist_ok=True)
    text = app.join_segments(segs, lang)
    paras = make_paragraphs(segs, lang) or ([text] if text else [])

    (video_dir / "1-纯文字.txt").write_text("\n\n".join(paras) + "\n", encoding="utf-8")

    lines = []
    for s in segs:
        ts = app.fmt_ts(s["start"], False)[0:8]  # HH:MM:SS
        lines.append(f"[{ts}] {s['text']}")
    (video_dir / "2-时间轴.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    (video_dir / "3-字幕.srt").write_text(app.to_srt(segs), encoding="utf-8")

    info = dict(meta)
    info.update({
        "chars": len(re.sub(r"\s", "", text)),
        "segments": len(segs),
        "language": lang,
        "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    (video_dir / "meta.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    return info


# --------------------------------------------------------------------------
# 批次索引（滚动保留最近 30 批）
# --------------------------------------------------------------------------
def load_index() -> list[dict]:
    if INDEX_PATH.exists():
        try:
            return json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def save_index(idx: list[dict]) -> None:
    BATCH_ROOT.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")


def roll_old_batches(idx: list[dict]) -> list[dict]:
    """超过 30 批就删掉最老的批次文件夹。"""
    idx.sort(key=lambda b: b["seq"])
    while len(idx) > KEEP_BATCHES:
        old = idx.pop(0)
        d = BATCH_ROOT / old["dir"]
        if d.exists():
            try:
                shutil.rmtree(d)
                log(f"  [清理] 删除旧批次 {old['dir']}（超过 {KEEP_BATCHES} 批）")
            except Exception as e:
                log(f"  [清理失败] {old['dir']}: {e}")
    return idx


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# 抖音兜底：yt-dlp 拿不到时走在线解析接口拿直链
# --------------------------------------------------------------------------
PARSE_API = "https://video.zacao.top/api/parse"


def is_douyin(url: str) -> bool:
    return "douyin.com" in url or "iesdouyin.com" in url


def parse_douyin(url: str) -> dict:
    body = json.dumps({"text": url}).encode("utf-8")
    req = urllib.request.Request(PARSE_API, data=body, headers={
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.loads(r.read().decode("utf-8", "ignore"))
    if d.get("code") != 200 or not d.get("data"):
        raise RuntimeError(str(d.get("message") or d)[:120])
    return d["data"]


def download_url(src: str, dst: Path) -> int:
    req = urllib.request.Request(src, headers={
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://www.douyin.com/",
        "Accept": "*/*",
    })
    with urllib.request.urlopen(req, timeout=300) as r, open(dst, "wb") as f:
        shutil.copyfileobj(r, f)
    return dst.stat().st_size


def fetch_source(url: str, work: Path):
    """返回 (title, duration, uploader, media_path, subtitle_path)"""
    work.mkdir(parents=True, exist_ok=True)
    if is_douyin(url):
        try:
            data = parse_douyin(url)
            title = (data.get("title") or "抖音视频").strip()
            uploader = ((data.get("author") or {}).get("nickname") or "").strip()
            src = data.get("video_url") or data.get("audio_url")
            if not src:
                raise RuntimeError("解析接口没返回可用地址")
            # audio_url 常常是背景音乐轨，优先拿视频再抽人声
            dst = work / ("media.mp4" if data.get("video_url") else "media.m4a")
            size = download_url(src, dst)
            log(f"  抖音解析成功，已下载 {size/1048576:.1f} MB")
            return title, 0.0, uploader, dst, None
        except Exception as e:
            log(f"  抖音解析接口失败（{e}），改试 yt-dlp…")

    meta = fetch_meta(url)
    title = meta.get("title") or "未命名"
    duration = meta.get("duration") or 0
    media = download_media(url, work)
    return title, float(duration), meta.get("uploader") or "", media, pick_subtitle(work)


def process_url(url: str, batch_dir: Path, model_name: str, lang: str) -> dict:
    log(f"\n=== 处理：{url}")
    t0 = time.time()
    work = batch_dir / "_tmp"
    if work.exists():
        shutil.rmtree(work)

    title, duration, uploader, media, sub = fetch_source(url, work)
    if not duration:  # 抖音解析接口不给时长，自己探一下
        try:
            duration = float(app.probe_media(media)["duration"] or 0)
        except Exception:
            duration = 0.0
    if duration > 600 and model_name == "medium":
        model_name = "small"
        log(f"  时长 {duration/60:.1f} 分钟，medium 太慢，本次改用 small（后面会人工校对）")
    log(f"  标题：{title}")
    if duration:
        log(f"  时长：{duration/60:.1f} 分钟　上传者：{uploader or '-'}")
    else:
        log(f"  上传者：{uploader or '-'}")
    log(f"  已下载：{media.name}（{media.stat().st_size/1048576:.1f} MB）")

    segs: list[dict] = []
    detected = lang
    if sub:
        log(f"  发现字幕轨：{sub.name}（直接提取，不跑识别）")
        segs = app.parse_srt(sub.read_text(encoding="utf-8", errors="ignore"))
        segs = [dict(s, text=app.normalize_cjk(s["text"], "zh")) for s in segs]
        detected = "zh"
        source = "subtitle"
    else:
        log("  开始语音识别…")
        wav = work / "audio16k.wav"
        app.extract_audio(media, wav, duration)
        segs, detected, device = transcribe(wav, model_name, lang)
        source = f"asr-{model_name}-{device}"
        log(f"  识别完成：{len(segs)} 句（{device}）")

    if not segs:
        shutil.rmtree(work, ignore_errors=True)
        raise RuntimeError("没有拿到任何文字（可能是纯音乐/无人声，或下载失败）")

    video_dir = batch_dir / safe_name(title)
    info = write_outputs(video_dir, segs, detected, {
        "url": url, "title": title, "duration": round(float(duration), 1),
        "uploader": uploader, "source": source,
        "elapsed": round(time.time() - t0, 1),
    })
    shutil.rmtree(work, ignore_errors=True)
    info["dir"] = str(video_dir.relative_to(BATCH_ROOT.parent.parent)).replace("\\", "/")
    log(f"  ✓ 完成，用时 {info['elapsed']}s，{info['chars']} 字 -> {video_dir}")
    return info


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("urls", nargs="+")
    ap.add_argument("--lang", default="zh")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    args = ap.parse_args()

    idx = load_index()
    seq = (idx[-1]["seq"] + 1) if idx else 1
    batch_dir = BATCH_ROOT / f"batch_{seq:04d}"
    batch_dir.mkdir(parents=True, exist_ok=True)

    model_name = pick_model(args.model)
    if model_name != args.model:
        log(f"[提示] {args.model} 模型还没下载，本次先用 {model_name}")

    items = []
    failed = []
    for url in args.urls:
        try:
            items.append(process_url(url, batch_dir, model_name, args.lang))
        except Exception as e:
            log(f"  ✗ 失败：{e}")
            failed.append({"url": url, "error": str(e)})

    if items:
        idx.append({
            "seq": seq,
            "dir": batch_dir.name,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "items": items,
        })
        idx = roll_old_batches(idx)
        save_index(idx)
    elif not failed:
        shutil.rmtree(batch_dir, ignore_errors=True)

    log("\n" + "=" * 56)
    log(f"批次 {seq}：成功 {len(items)} 条，失败 {len(failed)} 条")
    for it in items:
        log(f"  ✓ {it['title'][:40]}  {it['chars']} 字  {it['dir']}")
    for f in failed:
        log(f"  ✗ {f['url']}  {f['error'][:100]}")
    log(f"当前共保存 {len(idx)}/{KEEP_BATCHES} 批")
    return 0 if items else 1


if __name__ == "__main__":
    sys.exit(main())
