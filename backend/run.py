"""
Server launcher for Python 3.14+ on Windows.

asyncio.set_event_loop_policy() is deprecated in 3.14.
Instead we use asyncio.Runner(loop_factory=...) so Playwright can
spawn subprocesses (requires ProactorEventLoop on Windows).

Usage:
    python run.py              # production-style
    python run.py --reload     # dev mode with auto-reload
"""

from __future__ import annotations

import asyncio
import sys
import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reload", action="store_true", default=False)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    config = uvicorn.Config(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )
    server = uvicorn.Server(config)

    if sys.platform == "win32":
        # ProactorEventLoop supports subprocess_exec — required by Playwright
        with asyncio.Runner(loop_factory=asyncio.ProactorEventLoop) as runner:
            runner.run(server.serve())
    else:
        asyncio.run(server.serve())


if __name__ == "__main__":
    main()
