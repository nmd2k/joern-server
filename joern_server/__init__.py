"""Joern HTTP client for ``/query-sync`` (multi-URL pool, timeouts, basic auth)."""

# Lazy import: joern_server.client depends on training.eval.executor which is not
# present inside the unified Docker image.  Importing the package (e.g. when
# running `python -m joern_server.proxy`) must NOT trigger that heavy import.
def __getattr__(name: str):
    if name == "JoernHTTPQueryExecutor":
        from joern_server.client import JoernHTTPQueryExecutor  # noqa: PLC0415
        return JoernHTTPQueryExecutor
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = ["JoernHTTPQueryExecutor"]
