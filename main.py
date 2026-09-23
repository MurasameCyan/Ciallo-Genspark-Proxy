from __future__ import annotations

import os

import uvicorn

from app.server import app


if __name__ == "__main__":
    uvicorn.run(
        app,
        host=os.environ.get("GS_BIND_HOST") or os.environ.get("BIND_HOST") or "0.0.0.0",
        port=int(os.environ.get("GS_PORT") or os.environ.get("PORT") or 8899),
        log_level=os.environ.get("LOG_LEVEL", "info"),
    )
