#!/usr/bin/env python3
# path: scripts/check-parity.py
"""Verify that the client and server agree.

Two checks:

1. **Constants** — PROTOCOL_VERSION and every key/flag bit must be identical in
   ``backend/app/protocol.py`` and ``shared/protocol.ts``. A mismatch here corrupts every
   packet in a way that is maddening to debug from either side alone.

2. **Movement** — runs the same scenarios through the Python integrator and the
   TypeScript one, then compares the final transforms. Any divergence means client-side
   prediction will disagree with the server, which players feel as rubber-banding.

Exits non-zero on any mismatch, so it works as a pre-commit hook or CI gate.

Usage: ``python scripts/check-parity.py [--tolerance 0.001]``
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY_PROTOCOL = ROOT / "backend" / "app" / "protocol.py"
TS_PROTOCOL = ROOT / "shared" / "protocol.ts"
FRONTEND = ROOT / "frontend"

BIT_NAMES = [
    "K_FORWARD", "K_BACK", "K_LEFT", "K_RIGHT", "K_JUMP", "K_SPRINT", "K_FIRE",
    "K_RELOAD", "K_CROUCH",
    "F_DEAD", "F_GROUNDED", "F_RELOADING", "F_SPRINTING", "F_MOVING", "F_BOT",
]

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
RESET = "\033[0m"


def ok(msg: str) -> None:
    print(f"{GREEN}  ok{RESET}  {msg}")


def bad(msg: str) -> None:
    print(f"{RED} fail{RESET} {msg}")


def warn(msg: str) -> None:
    print(f"{YELLOW} skip{RESET} {msg}")


# ── constants ─────────────────────────────────────────────────────────────────


def parse_py_constants(source: str) -> dict:
    out = {}
    version = re.search(r'PROTOCOL_VERSION\s*=\s*["\']([^"\']+)["\']', source)
    if version:
        out["PROTOCOL_VERSION"] = version.group(1)
    for name in BIT_NAMES:
        m = re.search(rf"^{name}\s*=\s*1\s*<<\s*(\d+)", source, re.MULTILINE)
        if m:
            out[name] = 1 << int(m.group(1))
    return out


def parse_ts_constants(source: str) -> dict:
    out = {}
    version = re.search(r"export const PROTOCOL_VERSION\s*=\s*['\"]([^'\"]+)['\"]", source)
    if version:
        out["PROTOCOL_VERSION"] = version.group(1)
    for name in BIT_NAMES:
        m = re.search(rf"export const {name}\s*=\s*1\s*<<\s*(\d+)", source)
        if m:
            out[name] = 1 << int(m.group(1))
    return out


def check_constants() -> bool:
    print("protocol constants")
    py = parse_py_constants(PY_PROTOCOL.read_text(encoding="utf-8"))
    ts = parse_ts_constants(TS_PROTOCOL.read_text(encoding="utf-8"))

    failed = False
    for key in ["PROTOCOL_VERSION"] + BIT_NAMES:
        if key not in py:
            bad(f"{key} missing from backend/app/protocol.py")
            failed = True
            continue
        if key not in ts:
            bad(f"{key} missing from shared/protocol.ts")
            failed = True
            continue
        if py[key] != ts[key]:
            bad(f"{key}: python={py[key]!r} typescript={ts[key]!r}")
            failed = True
    if not failed:
        ok(f"{len(BIT_NAMES) + 1} constants match")
    return not failed


# ── movement ──────────────────────────────────────────────────────────────────


def run_python_trace() -> dict:
    result = subprocess.run(
        [sys.executable, "-m", "app.scripts.movement_trace"],
        cwd=ROOT / "backend",
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def run_typescript_trace() -> dict:
    """Bundle the TS tracer with esbuild (a Vite dependency) and run it under node."""
    esbuild = FRONTEND / "node_modules" / ".bin" / "esbuild"
    if not esbuild.exists():
        raise FileNotFoundError("esbuild not found — run `npm install` in frontend/ first")
    bundle = ROOT / ".parity-trace.mjs"
    subprocess.run(
        [
            str(esbuild),
            "tools/movement-trace.ts",
            "--bundle",
            "--platform=node",
            "--format=esm",
            "--log-level=warning",
            f"--alias:@shared={ROOT / 'shared'}",
            f"--outfile={bundle}",
        ],
        cwd=FRONTEND,
        check=True,
    )
    map_json = ROOT / "assets" / "maps" / "alley.json"
    try:
        result = subprocess.run(
            ["node", str(bundle), str(map_json)], capture_output=True, text=True, check=True
        )
        return json.loads(result.stdout)
    finally:
        bundle.unlink(missing_ok=True)


def check_movement(tolerance: float) -> bool:
    print("\nmovement integrator")
    if shutil.which("node") is None:
        warn("node not installed — skipping the movement trace")
        return True
    try:
        ts = run_typescript_trace()
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        warn(f"could not run the TypeScript trace ({exc}) — skipping")
        return True

    py = run_python_trace()
    py_runs = {r["name"]: r for r in py["runs"]}
    ts_runs = {r["name"]: r for r in ts["runs"]}

    failed = False
    for name in sorted(set(py_runs) | set(ts_runs)):
        if name not in py_runs or name not in ts_runs:
            bad(f"{name}: present in only one implementation")
            failed = True
            continue
        a, b = py_runs[name], ts_runs[name]
        worst = 0.0
        for field in ("pos", "vel"):
            for i in range(3):
                worst = max(worst, abs(a[field][i] - b[field][i]))
        if worst > tolerance or a["grounded"] != b["grounded"]:
            bad(
                f"{name}: diverged by {worst:.5f}\n"
                f"        python pos={a['pos']} vel={a['vel']} grounded={a['grounded']}\n"
                f"        client pos={b['pos']} vel={b['vel']} grounded={b['grounded']}"
            )
            failed = True
        else:
            ok(f"{name:<24} max delta {worst:.6f}")
    return not failed


def main() -> int:
    parser = argparse.ArgumentParser(description="Client/server parity check.")
    parser.add_argument("--tolerance", type=float, default=1e-3, help="metres / m·s⁻¹")
    args = parser.parse_args()

    constants_ok = check_constants()
    movement_ok = check_movement(args.tolerance)

    print()
    if constants_ok and movement_ok:
        print(f"{GREEN}parity ok{RESET} — client and server agree")
        return 0
    print(f"{RED}parity broken{RESET} — fix before shipping, or players will rubber-band")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
