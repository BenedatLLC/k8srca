"""promtools' command line.

    promtools serve   --url URL [--host H] [--port P]
    promtools capture --url URL --out DIR --namespace NS [--end T] [--window 7d]
    promtools report  --url URL memory|restarts NAMESPACE POD     (a quick look)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from . import notes as N
from .capture import NODE_LABELS, ONE_HOT, allowlist, capture
from .client import PromClient
from .server import build, when


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="promtools")
    sub = ap.add_subparsers(dest="cmd", required=True)
    url = dict(default=os.environ.get("PROMTOOLS_URL"),
               help="Prometheus base URL (or PROMTOOLS_URL)")

    s = sub.add_parser("serve", help="the MCP server, over streamable HTTP")
    s.add_argument("--url", **url)
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)

    c = sub.add_parser("capture", help="export an allowlist of raw samples for replay")
    c.add_argument("--url", **url)
    c.add_argument("--out", required=True, type=Path)
    c.add_argument("--namespace", "-n", action="append", required=True)
    c.add_argument("--end", help="RFC 3339 or unix seconds; the k8stools capture's "
                                 "captured_at (default: now)")
    c.add_argument("--window", default="7d")
    c.add_argument("--chunk", default="6h")
    c.add_argument("--keep-node-labels", action="store_true")
    c.add_argument("--keep-one-hot-zeros", action="store_true",
                   help="keep the 0 samples of phase, ready and node-condition series")

    r = sub.add_parser("report", help="print a report")
    r.add_argument("--url", **url)
    r.add_argument("kind", choices=["memory", "restarts", "runtime"])
    r.add_argument("namespace", nargs="?")
    r.add_argument("pod", nargs="?")
    r.add_argument("--window", default="7d")

    args = ap.parse_args(argv)
    if not args.url:
        ap.error("--url (or PROMTOOLS_URL) is required")
    client = PromClient(args.url)

    if args.cmd == "serve":
        build(client).run("streamable-http", host=args.host, port=args.port)
        return 0
    if args.cmd == "capture":
        got = capture(client, args.out, end=when(args.end) or time.time(),
                      window=N.duration(args.window), selectors=allowlist(args.namespace),
                      chunk=N.duration(args.chunk),
                      drop=None if args.keep_node_labels else NODE_LABELS,
                      one_hot=frozenset() if args.keep_one_hot_zeros else ONE_HOT)
        print(f"{got.series} series, {got.samples} samples -> {got.path}")
        return 0
    from .tools import Tools
    t = Tools(client)
    if args.kind == "runtime":
        out = t.runtime_info()
    elif args.kind == "memory":
        out = t.memory_report(args.namespace, args.pod, args.window)
    else:
        out = t.restart_report(args.namespace, args.pod, window=args.window)
    json.dump(out, sys.stdout, indent=1)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
