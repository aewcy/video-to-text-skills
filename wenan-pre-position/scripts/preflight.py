#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wenan-pre-position 前置校验器：检查视频转文字流程需要的运行环境，并可按需补齐。

默认只检查、不改环境；加 --fix 才安装缺失的 Python 包 / 下载缺失的 faster-whisper 模型。

用法：
    python preflight.py [--root <项目根>] [--model small] [--fix] [--dry-run --json]
                        [--hf-mirror | --hf-endpoint URL] [--no-network]

退出码：
    0  全部 OK
    2  有缺失/警告（--fix 后仍存在）
    1  脚本自身异常（如项目根找不到）
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

MODEL_REPOS = {
    "tiny": "Systran/faster-whisper-tiny",
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
    "large-v3": "Systran/faster-whisper-large-v3",
}
MODEL_FILES = {
    "config.json", "model.bin", "tokenizer.json",
    "vocabulary.txt", "vocabulary.json", "preprocessor_config.json",
}
PKGS = [
    ("faster-whisper", "faster_whisper"),
    ("yt-dlp", "yt_dlp"),
    ("numpy", "numpy"),
    ("opencc-python-reimplemented", "opencc"),
]
APP_FUNCS = {
    "load_model", "load_wav_16k", "extract_audio",
    "probe_media", "normalize_cjk", "parse_srt",
}
CHECK_REQUIRE = ["python", "venv", "app", "ffmpeg", "ffprobe", "writable", "model"]
# 这些检查项缺失时 --fix 能自动处理
AUTO_FIX = {"pkg", "model", "dir"}


class Report:
    def __init__(self):
        self.items = []

    def add(self, item_id: str, status: str, detail: str, hint: str = "", fixable: bool = False):
        self.items.append({
            "id": item_id, "status": status, "detail": detail,
            "hint": hint, "fixable": fixable,
        })

    def has(self, status: str) -> bool:
        return any(i["status"] == status for i in self.items)

    def render(self):
        marks = {"ok": "✓", "warn": "!", "fail": "✗", "info": "·"}
        for i in self.items:
            line = f"[{marks.get(i['status'], '?')}] {i['detail']}"
            if i["status"] != "ok" and i["hint"]:
                line += f"    → {i['hint']}"
            print(line)


def find_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    here = Path(__file__).resolve()
    for q in [here, *here.parents]:
        if (q / "app.py").exists() or (q / "models").is_dir():
            return q
    return Path.cwd().resolve()


def pkg_version(dist: str) -> str | None:
    try:
        from importlib.metadata import version
        return version(dist)
    except Exception:
        return None


def module_present(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except Exception:
        return False


def app_functions(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return set()
    return {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}


def model_ready(target: Path) -> tuple[bool, str]:
    bin_f = target / "model.bin"
    cfg_f = target / "config.json"
    if not bin_f.exists():
        return False, "缺 model.bin"
    if not cfg_f.exists():
        return False, "缺 config.json"
    sizes = target / ".sizes.json"
    if sizes.exists():
        try:
            expect = json.loads(sizes.read_text(encoding="utf-8"))
            for fn, sz in expect.items():
                p = target / fn
                if not p.exists() or (sz and p.stat().st_size != sz):
                    return False, f"{fn} 大小不符"
        except Exception:
            pass
    return True, f"{bin_f.stat().st_size / 1048576:.0f} MB"


def probe_network(endpoint: str, timeout: float = 8.0) -> tuple[bool, str]:
    url = endpoint.rstrip("/") + "/api/models/Systran/faster-whisper-small"
    req = urllib.request.Request(url, headers={"User-Agent": "wenan-pre-position/1.0"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read(64)
        return True, f"{endpoint} 可达（{time.time() - t0:.1f}s）"
    except Exception as e:
        return False, f"{endpoint} 不可达：{type(e).__name__}: {e}"


def download_file(url: str, dst: Path, label: str) -> None:
    part = dst.with_name(dst.name + ".part")
    part.unlink(missing_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "wenan-pre-position/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r, open(part, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        last = -1
        while True:
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total:
                pct = int(done * 100 / total)
                if pct >= last + 5:
                    print(f"    {label}  {pct}%  ({done / 1048576:.0f}/{total / 1048576:.0f} MB)", flush=True)
                    last = pct
    part.replace(dst)


def ensure_model(name: str, root: Path, endpoint: str) -> Path:
    """把 HF 上的 faster-whisper 模型下到 models/<name>/（带大小校验）。"""
    repo = MODEL_REPOS[name]
    target = root / "models" / name
    target.mkdir(parents=True, exist_ok=True)
    api = f"{endpoint.rstrip('/')}/api/models/{repo}?blobs=true"
    files: list[tuple[str, int]] = []
    try:
        with urllib.request.urlopen(urllib.request.Request(api, headers={"User-Agent": "wenan-pre-position/1.0"}), timeout=60) as r:
            data = json.loads(r.read().decode("utf-8", "ignore") or "{}")
        for s in data.get("siblings") or []:
            fn = s.get("rfilename")
            if fn in MODEL_FILES:
                files.append((fn, int(s.get("size") or 0)))
    except Exception as e:
        print(f"    ! 读取模型文件列表失败（{e}），按默认文件名尝试")
    if not files:
        files = [(n, 0) for n in ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt")]
    files.sort(key=lambda x: -x[1])
    expect = dict(files)
    total_mb = sum(sz for _, sz in files) / 1048576 if any(sz for _, sz in files) else 0
    print(f"    模型 {name}（{repo}）约 {total_mb:.0f} MB，下载到 {target}")
    for fn, sz in files:
        dst = target / fn
        if dst.exists() and (sz == 0 or dst.stat().st_size == sz):
            continue
        if dst.exists():
            dst.unlink(missing_ok=True)
        download_file(f"{endpoint.rstrip('/')}/{repo}/resolve/main/{fn}", dst, fn)
        if sz and dst.stat().st_size != sz:
            raise RuntimeError(f"{fn} 大小不对（{dst.stat().st_size} != {sz}）")
    (target / ".sizes.json").write_text(json.dumps(expect), encoding="utf-8")
    return target


def pip_install(specs: list[str], python: Path, index_url: str | None) -> int:
    cmd = [str(python), "-m", "pip", "install", *specs]
    if index_url:
        cmd += ["-i", index_url]
    print("    $ " + " ".join(cmd), flush=True)
    p = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    tail = "\n".join((p.stdout or "").strip().splitlines()[-6:])
    if p.returncode != 0:
        tail += "\n" + "\n".join((p.stderr or "").strip().splitlines()[-6:])
    print(tail)
    return p.returncode


def check_all(root: Path, required_model: str, endpoint: str, network: bool) -> Report:
    r = Report()

    # 1. Python
    ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info < (3, 10):
        r.add("python", "fail", f"Python {ver}（需要 ≥3.10）", "升级 Python 后重建 venv")
    else:
        r.add("python", "ok", f"Python {ver}（{sys.executable}）")

    # 2. venv
    venv_py = root / ".venv" / "Scripts" / "python.exe"
    if sys.prefix != sys.base_prefix:
        r.add("venv", "ok", f".venv 已启用（{sys.prefix}）")
    elif venv_py.exists():
        r.add("venv", "warn", f".venv 存在但当前不在 venv 里（{sys.executable}）",
              f"改用 {venv_py} 运行，或先激活 .venv")
    else:
        r.add("venv", "warn", "没有 .venv", "py -3.12 -m venv .venv && .venv\\Scripts\\python -m pip install -r requirements.txt")

    # 3. Python 包
    missing = []
    for dist, mod in PKGS:
        v = pkg_version(dist)
        if v or module_present(mod):
            r.add("pkg", "ok", f"{dist}  {v or '已安装'}")
        else:
            missing.append(dist)
            r.add("pkg", "fail", f"{dist}  未安装", f"pip install {dist}", fixable=True)

    # 4. ffmpeg / ffprobe
    for exe in ("ffmpeg", "ffprobe"):
        path = shutil.which(exe)
        if path:
            r.add("ffmpeg" if exe == "ffmpeg" else "ffprobe", "ok", f"{exe}  {path}")
        else:
            r.add("ffmpeg" if exe == "ffmpeg" else "ffprobe", "fail", f"{exe} 不在 PATH",
                  "winget install Gyan.FFmpeg（装完重开终端）")

    # 5. app.py
    app = root / "app.py"
    if not app.exists():
        r.add("app", "fail", f"{root} 下没有 app.py（pull.py 依赖它）",
              "把配套的本地项目 app.py 放回项目根，或修改 pull.py 里的 import app")
    else:
        funcs = app_functions(app)
        lack = sorted(APP_FUNCS - funcs)
        if lack:
            r.add("app", "fail", "app.py 缺少函数：" + ", ".join(lack),
                  "app.py 版本不对，换成本项目配套版本")
        else:
            r.add("app", "ok", "app.py 就绪（所需函数齐全）")

    # 6. 模型
    target = root / "models" / required_model
    ready, why = model_ready(target)
    if ready:
        r.add("model", "ok", f"模型 {required_model}：{why}")
    else:
        r.add("model", "fail", f"模型 {required_model} 未就绪（{why}）",
              f"--fix 下载，或设 HF_ENDPOINT=https://hf-mirror.com", fixable=True)
    for extra in ("small", "medium"):
        if extra == required_model:
            continue
        ok, why = model_ready(root / "models" / extra)
        if ok:
            r.add("model-info", "info", f"模型 {extra} 也已就绪（{why}）")

    # 7. GPU
    smi = shutil.which("nvidia-smi")
    if smi:
        try:
            p = subprocess.run([smi, "--query-gpu=name,memory.total", "--format=csv,noheader"],
                               capture_output=True, text=True, timeout=15, errors="replace")
            gpu = (p.stdout or "").strip().splitlines()[0] if p.stdout.strip() else "未知"
            r.add("gpu", "ok", f"GPU：{gpu}")
        except Exception as e:
            r.add("gpu", "warn", f"nvidia-smi 执行失败：{e}", "将回退 CPU（慢一些）")
    else:
        r.add("gpu", "warn", "没有 nvidia-smi，将用 CPU 识别", "有 NVIDIA 显卡时装驱动可加速")

    # 8. 磁盘
    try:
        free_gb = shutil.disk_usage(root).free / 1024 ** 3
        if free_gb < 3:
            r.add("disk", "warn", f"磁盘剩余 {free_gb:.1f} GB（建议 ≥3 GB）", "清理磁盘后重试")
        else:
            r.add("disk", "ok", f"磁盘剩余 {free_gb:.1f} GB")
    except Exception as e:
        r.add("disk", "warn", f"磁盘空间读取失败：{e}")

    # 9. 可写
    try:
        with tempfile.NamedTemporaryFile(dir=root, prefix=".preflight-", delete=True):
            pass
        r.add("writable", "ok", f"项目根可写：{root}")
    except Exception as e:
        r.add("writable", "fail", f"项目根不可写：{e}", "检查目录权限")

    # 10. 目录
    for d in ("文案", "outputs", "uploads"):
        p = root / d
        if p.is_dir():
            r.add("dir", "ok", f"目录 {d}/ 存在")
        else:
            r.add("dir", "warn", f"目录 {d}/ 不存在", f"--fix 自动创建", fixable=True)

    # 11. 网络
    if network:
        ok, why = probe_network(endpoint)
        r.add("network", "ok" if ok else "warn", f"模型下载源：{why}",
              "换镜像（--hf-mirror）或稍后再试")
    else:
        r.add("network", "info", "已跳过网络检查（--no-network）")

    return r


def do_fix(root: Path, required_model: str, endpoint: str, python: Path,
           index_url: str | None, dry: bool) -> None:
    print("\n== 补齐缺失项 ==")
    # 目录
    for d in ("文案", "outputs", "uploads"):
        p = root / d
        if not p.is_dir():
            print(f"  + 创建目录 {p}")
            if not dry:
                p.mkdir(parents=True, exist_ok=True)
    # 包
    missing = [dist for dist, mod in PKGS if not (pkg_version(dist) or module_present(mod))]
    if missing:
        print(f"  + 安装 Python 包：{', '.join(missing)}")
        if not dry:
            if pip_install(missing, python, index_url) != 0:
                print("  ! pip 安装失败，请手动执行上面的命令")
    # 模型
    target = root / "models" / required_model
    ready, _ = model_ready(target)
    if not ready:
        print(f"  + 下载模型 {required_model}（源：{endpoint}）")
        if not dry:
            try:
                ensure_model(required_model, root, endpoint)
                print(f"  ✓ 模型已下载：{target}")
            except Exception as e:
                print(f"  ! 模型下载失败：{e}")
                print(f"    可以换镜像重试：--hf-mirror（或设 HF_ENDPOINT=https://hf-mirror.com）")
    if dry:
        print("  (--dry-run：以上动作均未执行)")


def main() -> int:
    ap = argparse.ArgumentParser(description="wenan-text 前置校验 / 补齐")
    ap.add_argument("--root", default=None, help="项目根（默认从脚本位置向上找 app.py/models）")
    ap.add_argument("--model", default="small", choices=sorted(MODEL_REPOS), help="必须就绪的模型（默认 small）")
    ap.add_argument("--fix", action="store_true", help="自动安装缺失的 Python 包 / 下载缺失模型 / 建目录")
    ap.add_argument("--dry-run", action="store_true", help="配合 --fix：只打印将要做什么")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--hf-mirror", action="store_true", help="模型源改用 https://hf-mirror.com")
    ap.add_argument("--hf-endpoint", default=None, help="自定义模型源（默认 env HF_ENDPOINT 或 huggingface.co）")
    ap.add_argument("--pypi-mirror", default=None, help="pip 索引（默认用 pip 自身配置）")
    ap.add_argument("--no-network", action="store_true", help="跳过网络探测")
    a = ap.parse_args()

    root = find_root(a.root)
    if not root.is_dir():
        print(f"项目根不存在：{root}", file=sys.stderr)
        return 1
    endpoint = (a.hf_endpoint or ("https://hf-mirror.com" if a.hf_mirror else None)
                or os.environ.get("HF_ENDPOINT") or "https://huggingface.co").rstrip("/")
    python = root / ".venv" / "Scripts" / "python.exe"
    if not python.exists():
        python = Path(sys.executable)

    if a.json:
        rep = check_all(root, a.model, endpoint, not a.no_network)
        out = {
            "root": str(root), "ok": not (rep.has("fail") or rep.has("warn")),
            "endpoint": endpoint, "checks": rep.items,
        }
        if a.fix:
            # JSON 模式不混入修复日志：先修复到内存日志
            import contextlib, io
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                do_fix(root, a.model, endpoint, python, a.pypi_mirror, a.dry_run)
            after = check_all(root, a.model, endpoint, not a.no_network)
            out["fix_log"] = buf.getvalue().splitlines()
            out["checks"] = after.items
            out["ok"] = not (after.has("fail") or after.has("warn"))
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if out["ok"] else 2

    print(f"wenan-pre-position  项目根：{root}")
    print(f"模型源：{endpoint}\n")
    rep = check_all(root, a.model, endpoint, not a.no_network)
    rep.render()

    if a.fix:
        do_fix(root, a.model, endpoint, python, a.pypi_mirror, a.dry_run)
        print("\n== 修复后复查 ==")
        rep = check_all(root, a.model, endpoint, not a.no_network)
        rep.render()

    bad = [i for i in rep.items if i["status"] in ("fail", "warn")]
    print()
    if not bad:
        print("结论：前置齐全 ✓  可以跑 /wenan-text 了")
        return 0
    print(f"结论：{len(bad)} 项缺失/警告")
    if any(i["fixable"] for i in rep.items if i["status"] in ("fail", "warn")):
        print("可自动补齐的项：加 --fix 重跑（如 `preflight.py --fix`）")
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已取消", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        print(f"preflight 异常：{type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
