"""Run the gateway: uv run aigateway  (reads GATEWAY_CONFIG, GATEWAY_DB, GATEWAY_PROVIDER, PORT)."""

from __future__ import annotations

import os


def main() -> None:
    import uvicorn

    uvicorn.run(
        "aigateway.app:create_app",
        factory=True,
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8080")),
        access_log=False,
    )


if __name__ == "__main__":
    main()
