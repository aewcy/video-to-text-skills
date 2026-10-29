#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wenan-text-proofread 校对工具：把某一批的原始 ASR 三件套按映射表改成「（校对）」三件套。

只做第 5 步（校对）的机械部分：应用同音字/错字映射、可选重排段落、复查半角标点/可疑词/段落长度。
「哪里错了」仍由模型逐段通读决定，脚本不猜语义。

用法：
    python proofread.py --list                          # 列批次/视频和校对状态
    python proofread.py --report                        # 看最新批（或 --batch）原始稿概况
    python proofread.py --fixes fixes.json              # 应用映射，写校对三件套
    python proofread.py --batch batch_0002 --video 游资 --fixes fixes.json --reflow
    python proofread.py --fixes fixes.json --force      # 覆盖已存在的校对版
    python proofread.py --report --json                 # 机器可读

fixes.json：
    [["秦硕", "秦朔"], ["红国", "红果"]]                 # 简写：只给映射
    或 {"fixes": [ ... ], "reflow": true}               # 需要重排段落时

退出码：0 成功 / 1 参数或读取错误 / 3 校对版已存在且未加 --force
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

RAW_FILES = ("1-纯文字.txt", "2-时间轴.txt", "3-字幕.srt")
TEXT_FILE = "1-纯文字.txt"
HALF_PUNCT = re.compile(r"[,?!:;](?=[\u4e00-\u9fff])")
# 与 wenan-text/scripts/check.py 的 SUSPECTS 保持同步；找不到 check.py 时用这份兜底
FALLBACK_SUSPECTS = [
    "纸银", "掌停", "掌幅", "龙湖榜", "低息", "静价", "净价", "缩哈", "卫骨",
    "培率", "分水领", "采实变", "误道", "树语", "副盘", "武当", "芬奇",
    "暴力", "控子", "众主", "新电图", "分食", "剩才能", "身套", "倾仓",
]


def out_name(name: str) -> str:
    if name.endswith(".txt"):
        return name[:-4] + "（校对）.txt"
    if name.endswith(".srt"):
        return name[:-4] + "（校对）.srt"
    return name + "（校对）"


def find_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    here = Path(__file__).resolve()
    for q in [here, *here.parents]:
        if (q / "app.py").exists() or (q / "models").is_dir():
            return q
    return Path.cwd().resolve()


def list_batches(root: Path) -> list[Path]:
    bdir = root / "outputs" / "batches"
    if not bdir.is_dir():
        return []
    return sorted([p for p in bdir.iterdir() if p.is_dir() and p.name.startswith("batch_")])


def videos_of(batch: Path) -> list[Path]:
    return sorted([p for p in batch.iterdir() if p.is_dir() and p.name != "_tmp"])


def pick_batch(root: Path, name: str | None) -> Path:
    batches = list_batches(root)
    if not batches:
        raise SystemExit(f"没有找到批次目录：{root / 'outputs' / 'batches'}")
    if name:
        want = name if name.startswith("batch_") else f"batch_{int(name):04d}"
        for b in batches:
            if b.name == want:
                return b
        raise SystemExit(f"批次不存在：{name}（现有：{', '.join(b.name for b in batches)}）")
    return batches[-1]


def pick_video(batch: Path, keyword: str | None) -> Path:
    vids = videos_of(batch)
    if not vids:
        raise SystemExit(f"{batch.name} 下没有视频目录")
    if keyword:
        hits = [v for v in vids if keyword.lower() in v.name.lower()]
        if len(hits) == 1:
            return hits[0]
        if not hits:
            raise SystemExit(f"{batch.name} 里没有名字包含「{keyword}」的视频：\n- " + "\n- ".join(v.name for v in vids))
        raise SystemExit(f"「{keyword}」匹配到多个视频，请写具体一点：\n- " + "\n- ".join(v.name for v in hits))
    if len(vids) == 1:
        return vids[0]
    raise SystemExit(f"{batch.name} 有 {len(vids)} 个视频，用 --video 指定：\n- " + "\n- ".join(v.name for v in vids))


def load_suspects(root: Path) -> list[str]:
    """从 wenan-text/scripts/check.py 里读 SUSPECTS（保持同一口径），读不到就用兜底表。"""
    candidates = [
        root / ".dsh" / "skills" / "wenan-text" / "scripts" / "check.py",
        Path(__file__).resolve().parents[2] / "wenan-text" / "scripts" / "check.py",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            for node in tree.body:
                if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "SUSPECTS" for t in node.targets
                ):
                    val = ast.literal_eval(node.value)
                    if isinstance(val, list) and all(isinstance(x, str) for x in val):
                        return val
        except Exception:
            pass
    return FALLBACK_SUSPECTS


def load_fixes(path: Path) -> tuple[list[tuple[str, str]], bool]:
    data = json.loads(path.read_text(encoding="utf-8"))
    reflow = False
    if isinstance(data, dict):
        reflow = bool(data.get("reflow", False))
        data = data.get("fixes")
    if not isinstance(data, list):
        raise SystemExit(f"{path} 格式不对：应为 [[错, 对], ...] 或 {{\"fixes\": [...], \"reflow\": bool}}")
    fixes: list[tuple[str, str]] = []
    for i, item in enumerate(data, 1):
        if not (isinstance(item, (list, tuple)) and len(item) == 2 and all(isinstance(x, str) for x in item)):
            raise SystemExit(f"{path} 第 {i} 条不是 [错, 对] 字符串对：{item!r}")
        bad, good = item
        if not bad:
            raise SystemExit(f"{path} 第 {i} 条的「错」为空")
        fixes.append((bad, good))
    fixes.sort(key=lambda x: -len(x[0]))
    return fixes, reflow


def apply_fixes(text: str, fixes: list[tuple[str, str]]) -> tuple[str, dict[str, int]]:
    hits: dict[str, int] = {}
    for bad, good in fixes:
        n = text.count(bad)
        if n:
            hits[bad] = n
            text = text.replace(bad, good)
    return text, hits


def reflow_text(text: str, max_chars: int = 235) -> str:
    """按空行分段；把超过 max_chars 的段落在逗号处切开（校对稿排版用，不改文字内容）。"""
    paras = [line.strip() for line in text.splitlines() if line.strip()]
    out: list[str] = []
    for p in paras:
        while len(p) > max_chars:
            cut = p.rfind("，", 0, max_chars)
            if cut < 80:
                cut = max_chars
            out.append(p[:cut].rstrip("，") + "，")
            p = p[cut:].lstrip("，")
        if p:
            out.append(p)
    return "\n\n".join(out) + "\n"


def report_text(vdir: Path, suspects: list[str], reviewed: bool) -> dict:
    raw = vdir / TEXT_FILE
    text = raw.read_text(encoding="utf-8", errors="replace") if raw.exists() else ""
    paras = [p for p in text.split("\n\n") if p.strip()]
    chars = len(re.sub(r"\s", "", text))
    half = HALF_PUNCT.findall(text)
    hit = {w: text.count(w) for w in suspects if w in text}
    return {
        "video": vdir.name,
        "raw_chars": chars,
        "raw_paragraphs": len(paras),
        "raw_longest": max((len(p) for p in paras), default=0),
        "half_width": len(half),
        "suspects": hit,
        "reviewed": reviewed,
        "files": {n: (vdir / out_name(n)).exists() for n in RAW_FILES},
    }


def cmd_list(root: Path) -> int:
    batches = list_batches(root)
    if not batches:
        print(f"没有批次：{root / 'outputs' / 'batches'}")
        return 1
    for b in batches:
        vids = videos_of(b)
        print(f"{b.name}（{len(vids)} 个视频）")
        for v in vids:
            done = all((v / out_name(n)).exists() for n in RAW_FILES)
            print(f"  {'✓' if done else '·'} {v.name}")
    return 0


def cmd_report(root: Path, batch_name: str | None, video_kw: str | None, as_json: bool) -> int:
    batch = pick_batch(root, batch_name)
    vdir = pick_video(batch, video_kw)
    info = report_text(vdir, load_suspects(root), all((vdir / out_name(n)).exists() for n in RAW_FILES))
    if as_json:
        info["batch"] = batch.name
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0
    print(f"批次：{batch.name}　视频：{vdir.name}")
    print(f"原始稿：{info['raw_chars']} 字 / {info['raw_paragraphs']} 段 / 最长 {info['raw_longest']} 字")
    print(f"半角标点（紧跟汉字）：{info['half_width']} 处")
    print("可疑词：" + (str(info["suspects"]) if info["suspects"] else "无"))
    print(f"校对版：{'已存在' if info['reviewed'] else '未生成'}")
    return 0


def cmd_fix(root: Path, batch_name: str | None, video_kw: str | None,
            fixes_path: Path, force: bool, reflow: bool, as_json: bool) -> int:
    batch = pick_batch(root, batch_name)
    vdir = pick_video(batch, video_kw)
    fixes, json_reflow = load_fixes(fixes_path)
    reflow = reflow or json_reflow

    missing = [n for n in RAW_FILES if not (vdir / n).exists()]
    if missing:
        print(f"原始稿缺失：{', '.join(missing)}（{vdir}）", file=sys.stderr)
        return 1

    targets = [vdir / out_name(n) for n in RAW_FILES]
    exists = [t.name for t in targets if t.exists()]
    if exists and not force:
        print("校对版已存在，未覆盖（确认要重跑请加 --force，会丢掉现有校对稿）：", file=sys.stderr)
        for name in exists:
            print(f"  - {vdir / name}", file=sys.stderr)
        return 3

    results = []
    total_hits: dict[str, int] = {}
    for name in RAW_FILES:
        src = (vdir / name).read_text(encoding="utf-8", errors="replace")
        fixed, hits = apply_fixes(src, fixes)
        if reflow and name == TEXT_FILE:
            fixed = reflow_text(fixed)
        for k, v in hits.items():
            total_hits[k] = total_hits.get(k, 0) + v
        (vdir / out_name(name)).write_text(fixed, encoding="utf-8")
        results.append({"file": name, "out": out_name(name), "hits": sum(hits.values())})

    info = report_text(vdir, load_suspects(root), True)
    payload = {
        "batch": batch.name,
        "video": vdir.name,
        "fixes": len(fixes),
        "total_hits": sum(total_hits.values()),
        "files": results,
        "reviewed": info,
    }
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"批次：{batch.name}　视频：{vdir.name}")
    for r in results:
        print(f"  {r['file']}  →  {r['out']}   修改 {r['hits']} 处")
    print(f"共 {len(fixes)} 类、{sum(total_hits.values())} 处修改（含三件重复计数）")
    print("复查：")
    print(f"  半角标点：{info['half_width']} 处")
    print(f"  可疑词：{str(info['suspects']) if info['suspects'] else '无'}")
    print(f"  段落：{info['raw_paragraphs']} 个，最长 {info['raw_longest']} 字")
    print("原始版未改动。下一步：回到 wenan-text 第 6 步跑 build_md.py。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="wenan-text 校对：原始三件套 → （校对）三件套")
    ap.add_argument("--root", default=None, help="项目根（默认从脚本位置向上找 app.py/models）")
    ap.add_argument("--batch", default=None, help="批次名或序号，如 batch_0002 / 2（默认最新批）")
    ap.add_argument("--video", default=None, help="视频名关键词（批内多个视频时必填）")
    ap.add_argument("--list", action="store_true", help="列出批次/视频和校对状态")
    ap.add_argument("--report", action="store_true", help="只看原始稿概况，不写文件")
    ap.add_argument("--fixes", default=None, help="映射表 JSON 路径")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的校对版")
    ap.add_argument("--reflow", action="store_true", help="把超长段落在逗号处切开（只影响纯文字）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    a = ap.parse_args()

    root = find_root(a.root)
    if a.list:
        return cmd_list(root)
    if not a.fixes:
        return cmd_report(root, a.batch, a.video, a.json)
    return cmd_fix(root, a.batch, a.video, Path(a.fixes).expanduser().resolve(), a.force, a.reflow, a.json)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已取消", file=sys.stderr)
        sys.exit(130)
