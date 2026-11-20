"""One-command launcher: starts backend (uvicorn :8000) and frontend (vite :5173).

Usage:
    python run.py            # backend + frontend
    python run.py --backend  # backend only
    python run.py --frontend # frontend only

The frontend dev server proxies /api to the backend, so just open
http://localhost:5173 when both are up.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND_DIR = ROOT / "backend"
FRONTEND_DIR = ROOT / "frontend"


def _python() -> str:
    return sys.executable


def _node_cmd(*args: str) -> list[str]:
    """Run a node tool via local node_modules/.bin, falling back to npx."""
    if os.name == "nt":
        local = FRONTEND_DIR / "node_modules" / ".bin" / (args[0] + ".cmd")
        if local.exists():
            return [str(local), *args[1:]]
        return ["npx", *args]
    local = FRONTEND_DIR / "node_modules" / ".bin" / args[0]
    if local.exists():
        return [str(local), *args[1:]]
    return ["npx", *args]


def spawn(name: str, cmd: list[str], cwd: Path) -> subprocess.Popen:
    print(f"[run] starting {name}: {' '.join(cmd)}")
    return subprocess.Popen(cmd, cwd=str(cwd))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", action="store_true", help="start only the backend")
    parser.add_argument("--frontend", action="store_true", help="start only the frontend")
    args = parser.parse_args()
    start_backend = args.backend or not (args.backend or args.frontend)
    start_frontend = args.frontend or not (args.backend or args.frontend)

    procs: list[subprocess.Popen] = []
    try:
        if start_backend:
            procs.append(
                spawn(
                    "backend",
                    [_python(), "-m", "uvicorn", "app.main:app", "--port", "8000"],
                    BACKEND_DIR,
                )
            )
        if start_frontend:
            procs.append(spawn("frontend", _node_cmd("vite"), FRONTEND_DIR))

        print("[run] open http://localhost:5173  (Ctrl+C to stop everything)")
        for p in procs:
            p.wait()
    except KeyboardInterrupt:
        print("\n[run] shutting down…")
    finally:
        for p in procs:
            p.terminate()


if __name__ == "__main__":
    main()
