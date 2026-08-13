"""Test doubles that let tests drive the real Exporter and the real adapters.

The in-memory targets exercise real Gauge objects in a real CollectorRegistry,
so assertions read the exported series rather than a mirrored copy of a gauge's
label list. The HTTP and Unix socket servers are real servers on real sockets,
so an adapter test covers the transport it claims to cover.
"""

import json
import os
import socket
import socketserver
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from kea_exporter.exporter import Exporter

TIMESTAMP = "2026-01-01 00:00:00.000000"


@dataclass(frozen=True)
class KeaResponse:
    """One scripted control response, with optional transport behavior."""

    body: object
    status: int = 200
    chunks: tuple[bytes, ...] | None = None
    delay: float = 0


class KeaControl:
    """Script Kea commands once for the HTTP and Unix socket adapters."""

    def __init__(self, config_get, statistic_get_all):
        self.responses = {
            "config-get": config_get,
            "statistic-get-all": statistic_get_all,
        }
        self.requests = []
        self._queued = defaultdict(deque)

    def queue(self, command, *responses):
        """Use the given responses before the command's stable response."""
        self._queued[command].extend(responses)

    def respond(self, request):
        """Record a command and return its next response."""
        self.requests.append(request)
        command = request["command"]
        if self._queued[command]:
            return self._queued[command].popleft()
        return self.responses[command]


def _response(value):
    """Normalize a plain payload to a scripted response."""
    return value if isinstance(value, KeaResponse) else KeaResponse(value)


def _response_bytes(response):
    """Serialize one response unless the test supplied raw bytes."""
    return response.body if isinstance(response.body, bytes) else json.dumps(response.body).encode()


class _KeaHTTPHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.headers.append(dict(self.headers))
        response = _response(self.server.control.respond(body))
        if response.delay:
            time.sleep(response.delay)
        raw = _response_bytes(response)
        self.send_response(response.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_args):
        """Silence the default stderr access log."""


class KeaHTTPServer(ThreadingHTTPServer):
    """Expose a scripted Kea control implementation over HTTP."""

    def __init__(self, control, host="127.0.0.1"):
        super().__init__((host, 0), _KeaHTTPHandler)
        self.control = control
        self.headers = []
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()

    @property
    def target(self):
        """Return the URL that reaches this server."""
        host = self.server_address[0]
        if self.address_family == socket.AF_INET6:
            host = f"[{host}]"
        return f"http://{host}:{self.server_address[1]}"

    def close(self):
        """Stop the worker thread and close the listening socket."""
        self.shutdown()
        self.server_close()
        self.thread.join()


class KeaIPv6HTTPServer(KeaHTTPServer):
    """Expose the same HTTP adapter on an IPv6 loopback socket."""

    address_family = socket.AF_INET6


class _KeaUnixSocketHandler(socketserver.BaseRequestHandler):
    def handle(self):
        raw = b""
        while True:
            chunk = self.request.recv(4096)
            if not chunk:
                return
            raw += chunk
            try:
                body = json.loads(raw)
            except json.JSONDecodeError:
                continue
            break

        response = _response(self.server.control.respond(body))
        if response.delay:
            time.sleep(response.delay)
        chunks = response.chunks or (_response_bytes(response),)
        for chunk in chunks:
            self.request.sendall(chunk)


class _ThreadingUnixStreamServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


class KeaUnixSocketServer(_ThreadingUnixStreamServer):
    """Expose a scripted Kea control implementation over a Unix socket."""

    def __init__(self, path, control):
        self.path = os.fspath(path)
        super().__init__(self.path, _KeaUnixSocketHandler)
        self.control = control
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        """Stop the worker threads and remove the socket file."""
        self.shutdown()
        self.server_close()
        self.thread.join()
        if os.path.exists(self.path):
            os.unlink(self.path)


def stat(value):
    """Wrap a value in the shape Kea reports: a list of [value, timestamp] pairs."""
    return [[value, TIMESTAMP]]


def stats(**kwargs):
    """Build a Kea statistics payload from keyword pairs, dashes written as underscores."""
    return {key.replace("_", "-"): stat(value) for key, value in kwargs.items()}


class InMemoryTarget:
    """A Kea target that yields canned statistics instead of talking to a server.

    Satisfies the surface Exporter uses from a target: a server identifier and
    stats() yielding (server_id, dhcp_version, arguments, subnets).
    """

    def __init__(self, server_id="memory://kea"):
        # Exporter reads _server_id for error messages; see ADR-0001 follow-up.
        self._server_id = server_id
        self.rows = []
        self.calls = 0

    @property
    def server_id(self):
        return self._server_id

    def add(self, dhcp_version, arguments, subnets=None):
        # Keep the mapping the caller passed; `or {}` would swap an empty one
        # for a different object and hide anything the test adds afterwards.
        self.rows.append((self._server_id, dhcp_version, arguments, {} if subnets is None else subnets))
        return self

    def stats(self):
        self.calls += 1
        yield from self.rows


class ScriptedTarget:
    """A target whose successive scrapes are scripted.

    Each entry is either the rows that scrape yields, or an exception it
    raises. Raising from the generator body matches the real adapters, where
    nothing happens until the Exporter iterates.
    """

    def __init__(self, *scrapes, server_id="memory://kea"):
        self._server_id = server_id
        self.scrapes = list(scrapes)
        self.calls = 0

    @property
    def server_id(self):
        return self._server_id

    def stats(self):
        self.calls += 1
        outcome = self.scrapes.pop(0) if self.scrapes else []
        if isinstance(outcome, Exception):
            raise outcome
        yield from outcome


class FailingTarget:
    """A target whose stats() raises, for the per-target error path."""

    def __init__(self, server_id="memory://down", error=None):
        self._server_id = server_id
        self.error = error or ConnectionError("target unreachable")

    @property
    def server_id(self):
        return self._server_id

    def stats(self):
        raise self.error
        yield  # pragma: no cover - makes stats() a generator


def exporter_with(registry, *targets, **kwargs):
    """Build an Exporter wired to the given targets, with no network at construction."""
    exporter = Exporter(targets=[], registry=registry, **kwargs)
    exporter.targets = list(targets)
    return exporter


def samples(registry, name):
    """Every exported sample of a metric, whatever its labels.

    Negative assertions must not name labels: get_sample_value matches on the
    complete label set, so a partial one returns None even when the series is
    there, and `assert ... is None` can never fail.
    """
    return [s for metric in registry.collect() for s in metric.samples if s.name == name]


def subnet4(subnet_id, cidr, pools=()):
    """A Kea DHCPv4 subnet configuration entry."""
    return {"id": subnet_id, "subnet": cidr, "pools": [{"pool": p} for p in pools]}


def subnet6(subnet_id, cidr, pools=(), pd_pools=()):
    """A Kea DHCPv6 subnet configuration entry.

    pd_pools entries are (prefix, prefix_len, delegated_len) triples.
    """
    return {
        "id": subnet_id,
        "subnet": cidr,
        "pools": [{"pool": p} for p in pools],
        "pd-pools": [
            {"prefix": prefix, "prefix-len": prefix_len, "delegated-len": delegated_len}
            for prefix, prefix_len, delegated_len in pd_pools
        ],
    }
