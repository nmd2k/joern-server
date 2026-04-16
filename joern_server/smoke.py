"""CLI smoke test against a running Joern server (same semantics as docker healthcheck)."""

from __future__ import annotations

import argparse
import os

from joern_server.client import JoernHTTPQueryExecutor


def main() -> None:
    p = argparse.ArgumentParser(description="POST a trivial CPGQL to Joern /query-sync")
    p.add_argument(
        "--url",
        default=os.environ.get("JOERN_HTTP_URL", "http://127.0.0.1:8080"),
        help="Base URL (default: env JOERN_HTTP_URL or http://127.0.0.1:8080)",
    )
    p.add_argument("--user", default=os.environ.get("JOERN_SERVER_AUTH_USERNAME"))
    p.add_argument("--password", default=os.environ.get("JOERN_SERVER_AUTH_PASSWORD"))
    args = p.parse_args()

    auth = None
    if args.user and args.password:
        auth = (args.user, args.password)

    with JoernHTTPQueryExecutor(args.url, auth=auth, timeout=60.0) as ex:
        out = ex.execute('val _smoke = "ok"')
    print(out)
    if not out.get("success"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
