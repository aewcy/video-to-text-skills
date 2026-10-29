# -*- coding: utf-8 -*-
"""
把校对后的字幕转成两个 Markdown：

    正文.md      —— 不带时间轴，按段落/小标题排好，专门用来读和复制
    时间轴.md    —— 每行一条 [时:分:秒] 文字

用法：
    python build_md.py --srt <校对版.srt> --outdir <文案/日期_标题> \
        --title "标题" [--url ...] [--author ...] [--model small] \
        [--proofread "3 轮 189 处"] [--sections sections.txt]

sections.txt：一行一个分节标记（会按顺序在正文里切成 ## 小标题）。
标记文本本身会变成标题，所以写视频里真实出现的那句，比如「三、炒作逻辑」。
"""
import argparse
import re
import shutil
import sys
from pathlib import Path

def _project_root(p: Path) -> Path:
    for q in [p, *p.parents]:
        if (q / "app.py").exists() or (q / "models").exists():
            return q
    return p.parents[3]


BASE = _project_root(Path(__file__).resolve())  # 项目根目录

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CJK = True
SRT_BLOCK = re.compile(
    r"\d+\s*\n(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})[^\n]*\n(.*?)(?=\n\s*\n|\Z)",
    re.S,
)
END_PUNCT = set("。！？，、；：…）】》”’")


def parse_srt(text: str) -> list[dict]:
    text = text.replace("\r\n", "\n").strip() + "\n\n"
    segs = []
    for m in SRT_BLOCK.finditer(text):
        body = re.sub(r"<[^>]+>", "", m.group(3)).strip()
        if body:
            segs.append({"start": _ts(m.group(1)), "end": _ts(m.group(2)), "text": body})
    return segs


def _ts(s: str) -> float:
    s = s.replace(",", ".")
    h, m, sec = s.split(":")
    return int(h) * 3600 + int(m) * 60 + float(sec)


def hhmmss(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def smart_join(buf: list[str]) -> str:
    out = ""
    for t in buf:
        if not out:
            out = t
            continue
        if out[-1] not in END_PUNCT:
            out += "，"
        out += t
    return out


def make_paragraphs(segs: list[dict], max_chars: int = 120) -> list[str]:
    paras, buf = [], []
    for s in segs:
        t = s["text"].strip()
        if not t:
            continue
        buf.append(t)
        joined = smart_join(buf)
        if len(joined) >= max_chars and (t[-1:] in "。！？!?…" or len(joined) >= max_chars * 1.6):
            paras.append(joined)
            buf = []
    if buf:
        paras.append(smart_join(buf))
    return [p for p in paras if p]


def split_sections(text: str, markers: list[str]):
    """按标记把整篇切成 [(标题 or None, 正文)]"""
    cuts, searched = [], 0
    for m in markers:
        i = text.find(m, searched)
        if i >= 0:
            cuts.append((i, m))
            searched = i + len(m)
    out, last = [], 0
    for k, (i, m) in enumerate(cuts):
        if i > last:
            out.append((None, text[last:i].strip()))
        end = cuts[k + 1][0] if k + 1 < len(cuts) else len(text)
        out.append((m, text[i + len(m):end].strip().lstrip("，,、 ")))
        last = end
    if last < len(text):
        out.append((None, text[last:].strip()))
    return [(h, b) for h, b in out if (b or h)]


def build(srt: Path, outdir: Path, title: str, url: str, author: str,
          model: str, proofread: str, sections: list[str], when: str,
          text_path: Path | None = None) -> None:
    segs = parse_srt(srt.read_text(encoding="utf-8", errors="ignore"))
    if text_path and text_path.exists():
        # 正文用校对后的段落文本（跨字幕条的修正才生效）
        paras = [p.strip() for p in text_path.read_text(encoding="utf-8").split("\n\n") if p.strip()]
    else:
        paras = make_paragraphs(segs)
    full = "\n\n".join(paras)
    chars = len(re.sub(r"\s", "", full))
    dur = segs[-1]["end"] if segs else 0

    outdir.mkdir(parents=True, exist_ok=True)

    head = [f"# {title}", ""]
    meta = []
    if url:
        meta.append(f"**来源**：{url}")
    if author:
        meta.append(f"**作者**：{author}")
    meta.append(f"**时长**：{dur/60:.1f} 分钟")
    meta.append(f"**字数**：{chars}")
    meta.append(f"**识别**：faster-whisper {model}（GPU）")
    if proofread:
        meta.append(f"**校对**：{proofread}")
    meta.append(f"**处理时间**：{when}")
    head.append("> " + "　·　".join(meta))
    head += ["", "---", ""]

    body = []
    for h, b in split_sections(full, sections):
        if h:
            body += [f"## {h}", ""]
        if b:
            body += [p + "\n" for p in b.split("\n\n") if p.strip()]
    if not sections:
        body = [p + "\n" for p in paras]

    (outdir / "正文.md").write_text("\n".join(head + body).rstrip() + "\n", encoding="utf-8")

    tl = [f"# {title} · 时间轴", "", f"> {dur/60:.1f} 分钟　·　{len(segs)} 条", "", "---", ""]
    for s in segs:
        tl.append(f"- **[{hhmmss(s['start'])}]** {s['text']}")
    (outdir / "时间轴.md").write_text("\n".join(tl) + "\n", encoding="utf-8")

    shutil.copy(srt, outdir / "字幕.srt")

    heading_count = len([h for h, _ in split_sections(full, sections) if h])
    print(f"正文.md    {chars} 字 / {heading_count} 个标题 / {dur/60:.1f} 分钟")
    print(f"时间轴.md  {len(segs)} 条")
    print(f"字幕.srt   已复制")
    print(f"输出目录   {outdir}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--srt", required=True)
    ap.add_argument("--text", default="", help="校对后的段落文本（推荐提供，正文以它为准）")
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--url", default="")
    ap.add_argument("--author", default="")
    ap.add_argument("--model", default="small")
    ap.add_argument("--proofread", default="")
    ap.add_argument("--sections", default="")
    ap.add_argument("--when", default="")
    a = ap.parse_args()

    sections = []
    if a.sections:
        sections = [x.strip() for x in Path(a.sections).read_text(encoding="utf-8").splitlines() if x.strip()]

    import datetime
    when = a.when or datetime.date.today().strftime("%Y-%m-%d")
    build(Path(a.srt), Path(a.outdir), a.title, a.url, a.author, a.model, a.proofread, sections, when,
          Path(a.text) if a.text else None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
