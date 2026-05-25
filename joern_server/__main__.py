"""Run the Joern HTTP API via uvicorn."""

from __future__ import annotations


def main() -> None:
    import uvicorn

    from joern_server.config import Settings

    settings = Settings.from_env()
    uvicorn.run(
        "joern_server.app:app",
        host=settings.proxy_host,
        port=settings.proxy_port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
