"""Credentials embedded in a target must not reach the log or the metrics.

The server id becomes the ``server`` label on every series, so a credential
left in it is served to anyone scraping /metrics, not merely written to a log.

These tests drive the real Exporter: the failure cases raise inside
KeaHTTPClient before it opens a connection, and the scrape cases talk to a
real HTTP server on localhost that answers the commands Kea would.
"""

import base64
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from prometheus_client import CollectorRegistry, generate_latest

from kea_exporter.exporter import Exporter

# Raises ValueError inside KeaHTTPClient before it opens a connection.
CERT_WITHOUT_KEY = {"client_cert": "/nonexistent/cert.pem"}

PASSWORD = "s3cret"
STATISTICS = {"pkt4-ack-sent": [[7, "2026-01-01 00:00:00.000000"]]}


class KeaHandler(BaseHTTPRequestHandler):
    """Answers the commands KeaHTTPClient issues, recording what it was sent."""

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.seen_auth.append(self.headers.get("Authorization"))

        if body.get("command") == "statistic-get-all":
            payload = [{"result": 0, "arguments": STATISTICS}]
        else:
            payload = [{"result": 0, "arguments": {"Dhcp4": {"subnet4": []}}}]

        raw = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        """Silence the default stderr access log."""


@pytest.fixture
def registry():
    return CollectorRegistry()


@pytest.fixture
def no_proxy(monkeypatch):
    """Keep an ambient proxy configuration out of a loopback test."""
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")


def serve(family, host):
    server = type("Server", (ThreadingHTTPServer,), {"address_family": family})((host, 0), KeaHandler)
    server.seen_auth = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def kea(no_proxy):
    server = serve(socket.AF_INET, "127.0.0.1")
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture
def kea6(no_proxy):
    try:
        server = serve(socket.AF_INET6, "::1")
    except OSError as ex:
        pytest.skip(f"no IPv6 loopback to bind: {ex}")
    yield server
    server.shutdown()
    server.server_close()


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        (f"http://user:{PASSWORD}@kea.local:8000", "http://kea.local:8000"),
        # urlparse reports an empty username here, which is falsy.
        (f"http://:{PASSWORD}@kea.local:8000", "http://kea.local:8000"),
        # urlparse reports an IPv6 host without its brackets.
        (f"http://user:{PASSWORD}@[::1]:8000", "http://[::1]:8000"),
    ],
)
def test_password_never_reaches_the_initialisation_failure_message(registry, capsys, target, expected):
    Exporter(targets=[target], registry=registry, **CERT_WITHOUT_KEY)

    out = capsys.readouterr().out
    assert "Failed to initialize target" in out, out
    assert PASSWORD not in out, out
    assert expected in out, out


@pytest.mark.parametrize(
    ("userinfo", "expected_auth"),
    [
        (f"user:{PASSWORD}@", f"user:{PASSWORD}"),
        (f":{PASSWORD}@", f":{PASSWORD}"),
    ],
)
def test_password_never_reaches_the_exported_metrics(registry, kea, userinfo, expected_auth):
    """A password in the server id would be served on /metrics as a label."""
    port = kea.server_address[1]
    exporter = Exporter(targets=[f"http://{userinfo}127.0.0.1:{port}"], registry=registry)
    exporter.update()

    exposition = generate_latest(registry).decode()
    assert PASSWORD not in exposition, exposition
    assert f'server="http://127.0.0.1:{port}"' in exposition

    # Stripping the URL must not cost the authentication it carried.
    expected = "Basic " + base64.b64encode(expected_auth.encode()).decode()
    assert kea.seen_auth and set(kea.seen_auth) == {expected}, kea.seen_auth


def test_ipv6_target_keeps_its_brackets_when_credentials_are_stripped(registry, kea6):
    """urlparse reports an IPv6 host without its brackets, and requests then
    rejects the rebuilt URL outright: InvalidURL: Failed to parse."""
    port = kea6.server_address[1]
    exporter = Exporter(targets=[f"http://:{PASSWORD}@[::1]:{port}"], registry=registry)
    exporter.update()

    exposition = generate_latest(registry).decode()
    assert PASSWORD not in exposition, exposition
    assert f'server="http://[::1]:{port}"' in exposition, exposition


def test_username_without_a_password_is_stripped_without_authenticating(registry, kea):
    """requests derives no credentials from this shape, so neither may we."""
    port = kea.server_address[1]
    exporter = Exporter(targets=[f"http://user@127.0.0.1:{port}"], registry=registry)
    exporter.update()

    assert f'server="http://127.0.0.1:{port}"' in generate_latest(registry).decode()
    assert kea.seen_auth and set(kea.seen_auth) == {None}, kea.seen_auth
