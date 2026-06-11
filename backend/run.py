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
import os
import sys
import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reload", action="store_true", default=False)
    parser.add_argument("--host", default="0.0.0.0")
    # Hosting platforms (Render/Railway) inject PORT; CLI flag still wins
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    args = parser.parse_args()

    config = uvicorn.Config(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )
    server = uvicorn.Server(config)

    try:
        if sys.platform == "win32":
            # ProactorEventLoop supports subprocess_exec — required by Playwright
            with asyncio.Runner(loop_factory=asyncio.ProactorEventLoop) as runner:
                runner.run(server.serve())
        else:
            asyncio.run(server.serve())
    except KeyboardInterrupt:
        # asyncio.Runner re-raises KeyboardInterrupt after cancelling the task;
        # uvicorn has already shut down gracefully by this point.
        print("Server stopped.")


if __name__ == "__main__":
    main()
