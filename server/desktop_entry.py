"""Frozen desktop entry point for the self-contained Electron distribution."""

from __future__ import annotations

import os

import uvicorn
from app import app


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=int(os.environ["MARI_DESKTOP_API_PORT"]),
        log_level="info",
        access_log=False,
    )
