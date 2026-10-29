# -*- coding: utf-8 -*-
"""
按《流程规范.md》自动检查一批产出。

用法：
    python check.py            # 检查最新一批
    python check.py batch_0001 # 检查指定批
"""
import json
import re
import sys
from pathlib import Path

def _project_root(p: Path) -> Path:
    for q in [p, *p.parents]:
        if (q / "app.py").exists() or (q / "models").exists():
            return q
    return p.parents[3]


BASE = _project_root(Path(__file__).resolve())  # 项目根目录
BATCH_ROOT = BASE / "outputs" / "batches"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 常见 ASR 同音错（出现在校对版里就算不合格）
SUSPECTS = [
    "纸银", "掌停", "掌幅", "龙湖榜", "低息", "静价", "净价", "缩哈", "卫骨",
    "培率", "分水领", "采实变", "误道", "树语", "副盘", "武当", "芬奇",
    "暴 力", "控子", "众主", "新电图", "分食", "剩才能", "身套", "倾仓",
]
HALF_PUNCT = re.compile(r"[,?!:;](?=[\u4e00-\u9fff])")


def last_cue_end(srt: str) -> float:
    ts = re.findall(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->", srt)
    if not ts:
        return 0.0
    h, m, s, ms = ts[-1]
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def check_video(vdir: Path) -> dict:
    r = {"name": vdir.name, "warn": [], "ok": []}
    meta = {}
    if (vdir / "meta.json").exists():
        meta = json.loads((vdir / "meta.json").read_text(encoding="utf-8"))

    srt_f = vdir / "3-字幕（校对）.srt"
    if not srt_f.exists():
        srt_f = vdir / "3-字幕.srt"
    srt = srt_f.read_text(encoding="utf-8") if srt_f.exists() else ""
    cues = len(re.findall(r"-->\s", srt))
    end = last_cue_end(srt)

    txt_f = vdir / "1-纯文字（校对）.txt"
    reviewed = txt_f.exists()
    if not reviewed:
        txt_f = vdir / "1-纯文字.txt"
    text = txt_f.read_text(encoding="utf-8") if txt_f.exists() else ""
    chars = len(re.sub(r"\s", "", text))
    paras = [p for p in text.split("\n\n") if p.strip()]

    # 1) 校对版是否存在
    r["ok" if reviewed else "warn"].append("校对版：有" if reviewed else "校对版：缺失（仍未校对）")

    # 2) 语速合理性（中文口播一般 150~400 字/分钟）
    dur = float(meta.get("duration") or 0) or end
    if dur > 10:
        cpm = chars / (dur / 60)
        ok = 120 <= cpm <= 450
        r["ok" if ok else "warn"].append(f"语速：{cpm:.0f} 字/分钟（{chars} 字 / {dur/60:.1f} 分钟）")
    else:
        r["warn"].append(f"时长未知，无法算语速（{chars} 字）")

    # 3) 半角标点
    half = HALF_PUNCT.findall(text)
    r["ok" if not half else "warn"].append(f"半角标点：{len(half)} 处")

    # 4) 残留同音错
    hit = {w: text.count(w) for w in SUSPECTS if w in text}
    r["ok" if not hit else "warn"].append("残留可疑词：" + (str(hit) if hit else "无"))

    # 5) 段落长度
    longest = max((len(p) for p in paras), default=0)
    r["ok" if longest <= 250 else "warn"].append(f"段落 {len(paras)} 个，最长 {longest} 字")

    # 6) 字幕完整性
    mono = True
    starts = [float(h) * 3600 + float(m) * 60 + float(s) for h, m, s in
              re.findall(r"(\d{2}):(\d{2}):(\d{2})[,.]\d{3}\s*-->", srt)]
    for a, b in zip(starts, starts[1:]):
        if b < a:
            mono = False
            break
    r["ok" if cues and mono else "warn"].append(f"字幕 {cues} 条，时间{'单调递增' if mono else '有乱序'}")

    return r


def main() -> int:
    if len(sys.argv) > 1:
        batch_dir = BATCH_ROOT / sys.argv[1]
    else:
        dirs = sorted([d for d in BATCH_ROOT.iterdir() if d.is_dir()]) if BATCH_ROOT.exists() else []
        if not dirs:
            print("还没有任何批次")
            return 1
        batch_dir = dirs[-1]

    print(f"== 检查 {batch_dir.name} ==")
    bad = 0
    for vdir in sorted([d for d in batch_dir.iterdir() if d.is_dir() and d.name != "_tmp"]):
        r = check_video(vdir)
        status = "PASS" if not r["warn"] else "WARN"
        if r["warn"]:
            bad += 1
        print(f"\n[{status}] {r['name'][:50]}")
        for line in r["ok"]:
            print(f"   ✓ {line}")
        for line in r["warn"]:
            print(f"   ! {line}")
    print("\n" + ("全部通过 ✓" if bad == 0 else f"{bad} 个视频有告警，需处理"))
    return 0 if bad == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
