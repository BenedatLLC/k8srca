"""A fake Prometheus over real HTTP, so the client is tested on the wire.

`respond(path, params)` decides each answer; every request is recorded with its
method, so a test can assert what was (and was not) sent.
"""

import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


class FakeProm:
    def __init__(self):
        self.requests: list[tuple[str, str, dict]] = []
        self.respond = lambda path, params: {"status": "success", "data": {}}

    def handler(self):
        fake = self

        class H(BaseHTTPRequestHandler):
            def _any(self):
                u = urllib.parse.urlsplit(self.path)
                params = urllib.parse.parse_qs(u.query)
                fake.requests.append((self.command, u.path, params))
                body = fake.respond(u.path, params)
                code = 200 if body.get("status") == "success" else 400
                raw = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            do_GET = do_POST = do_PUT = do_DELETE = _any

            def log_message(self, *a):
                pass

        return H


@pytest.fixture
def prom():
    fake = FakeProm()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), fake.handler())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    fake.url = f"http://127.0.0.1:{srv.server_address[1]}"
    yield fake
    srv.shutdown()


def ok(data):
    return {"status": "success", "data": data}


def vector(*rows):
    """rows: (labels, value)."""
    return ok({"resultType": "vector",
               "result": [{"metric": m, "value": [1792352556.0, str(v)]} for m, v in rows]})


def matrix(*rows):
    """rows: (labels, [(ts, value), ...])."""
    return ok({"resultType": "matrix",
               "result": [{"metric": m, "values": [[t, str(v)] for t, v in pts]}
                          for m, pts in rows]})


TARGETS = ok({"activeTargets": [
    {"labels": {"job": "kube-state-metrics", "instance": "ksm:8080"}, "scrapeInterval": "15s",
     "health": "up"},
    {"labels": {"job": "kubernetes-nodes-cadvisor", "instance": "minikube"},
     "scrapeInterval": "1m", "health": "up"}]})
