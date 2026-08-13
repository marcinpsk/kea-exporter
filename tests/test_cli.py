"""Behavior of the command-line entry point through a local Kea server."""

import signal
import threading
from concurrent.futures import ThreadPoolExecutor
from wsgiref.util import setup_testing_defaults

import pytest
from click.testing import CliRunner
from prometheus_client import CollectorRegistry

from kea_exporter import __version__
from kea_exporter.cli import cli
from tests.support import stat


class _CapturingHTTPServer:
    """Capture the Prometheus server boundary without opening another socket."""

    def __init__(self):
        self.app = None
        self.port = None
        self.address = None
        self.shutdown_calls = 0
        self.close_calls = 0
        self.events = []
        self.shutdown_error = None

    def set_app(self, app):
        self.app = app

    def shutdown(self):
        self.events.append("shutdown")
        self.shutdown_calls += 1
        if self.shutdown_error is not None:
            raise self.shutdown_error

    def server_close(self):
        self.events.append("close")
        self.close_calls += 1


class _Clock:
    def __init__(self):
        self.monotonic_now = 1000.0
        self.wall_now = 5000.0

    def monotonic(self):
        return self.monotonic_now

    def wall_time(self):
        return self.wall_now


class _CLIRuntime:
    def __init__(self, registry, httpd, clock):
        self.registry = registry
        self.httpd = httpd
        self.clock = clock

    def invoke(self, *arguments, env=None):
        return CliRunner().invoke(cli, list(arguments), env=env)


@pytest.fixture
def cli_runtime(monkeypatch):
    """Run the real CLI until its long-running sleep starts."""
    registry = CollectorRegistry()
    httpd = _CapturingHTTPServer()
    clock = _Clock()

    def start_http_server(port, address):
        httpd.port = port
        httpd.address = address
        return httpd, None

    def stop_after_startup(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr("kea_exporter.cli.REGISTRY", registry)
    monkeypatch.setattr("prometheus_client.REGISTRY", registry)
    monkeypatch.setattr("kea_exporter.cli.start_http_server", start_http_server)
    monkeypatch.setattr("kea_exporter.cli.time.monotonic", clock.monotonic)
    monkeypatch.setattr("kea_exporter.cli.time.time", clock.wall_time)
    monkeypatch.setattr("kea_exporter.cli.time.sleep", stop_after_startup)
    return _CLIRuntime(registry, httpd, clock)


def statistic_request_count(server):
    return sum(request["command"] == "statistic-get-all" for request in server.control.requests)


def scrape(app):
    environ = {}
    setup_testing_defaults(environ)
    response = {}

    def start_response(status, headers, exc_info=None):
        response["status"] = status
        response["headers"] = headers

    body = b"".join(app(environ, start_response))
    assert response["status"] == "200 OK"
    assert body


def test_cli_collects_metrics_before_serving(cli_runtime, http_server):
    """The registry is populated before the HTTP adapter can serve it."""
    statistics = [{"result": 0, "arguments": {"pkt4-ack-sent": stat(7)}}]
    kea = http_server(statistic_get_all=statistics)

    result = cli_runtime.invoke("--interval", "60", kea.target)

    assert result.exit_code == 0
    assert (
        cli_runtime.registry.get_sample_value(
            "kea_dhcp4_packets_sent_total",
            {"server": kea.target, "operation": "ack"},
        )
        == 7
    )
    assert cli_runtime.httpd.app is not None


def test_cli_announces_startup_and_shutdown(cli_runtime, http_server):
    kea = http_server()

    result = cli_runtime.invoke(kea.target)

    assert result.exit_code == 0
    assert f"Starting kea-exporter {__version__}" in result.stderr
    assert "Received signal, shutting down." in result.stderr
    assert result.stdout == "Listening on http://0.0.0.0:9547\n"
    assert cli_runtime.httpd.shutdown_calls == 1
    assert cli_runtime.httpd.close_calls == 1


@pytest.mark.parametrize(
    ("arguments", "env", "expected_port", "expected_address"),
    [
        ((), None, 9547, "0.0.0.0"),
        (("--port", "8080", "--address", "127.0.0.1"), None, 8080, "127.0.0.1"),
        ((), {"PORT": "9999", "ADDRESS": "127.0.0.1"}, 9999, "127.0.0.1"),
    ],
)
def test_cli_uses_the_requested_listen_address(
    cli_runtime,
    http_server,
    arguments,
    env,
    expected_port,
    expected_address,
):
    kea = http_server()

    result = cli_runtime.invoke(*arguments, kea.target, env=env)

    assert result.exit_code == 0
    assert cli_runtime.httpd.port == expected_port
    assert cli_runtime.httpd.address == expected_address
    assert f"Listening on http://{expected_address}:{expected_port}" in result.stdout


def test_cli_collects_from_targets_supplied_by_the_environment(cli_runtime, http_server):
    first = http_server(statistic_get_all=[{"result": 0, "arguments": {"pkt4-ack-sent": stat(3)}}])
    second = http_server(statistic_get_all=[{"result": 0, "arguments": {"pkt4-ack-sent": stat(5)}}])

    result = cli_runtime.invoke(env={"TARGETS": f"{first.target} {second.target}"})

    assert result.exit_code == 0
    for server, expected in ((first, 3), (second, 5)):
        assert (
            cli_runtime.registry.get_sample_value(
                "kea_dhcp4_packets_sent_total",
                {"server": server.target, "operation": "ack"},
            )
            == expected
        )


def test_cli_exits_when_every_target_has_invalid_configuration(cli_runtime, http_server, tmp_path):
    kea = http_server()
    certificate = tmp_path / "client.pem"
    certificate.touch()

    result = cli_runtime.invoke("--client-cert", str(certificate), kea.target)

    assert result.exit_code == 1
    assert "Both client_cert and client_key must be provided" in result.stderr
    assert cli_runtime.httpd.app is None


def test_cli_forwards_tls_options_to_the_http_adapter(cli_runtime, http_server, tmp_path):
    kea = http_server()
    certificate = tmp_path / "client.pem"
    key = tmp_path / "client.key"
    ca_bundle = tmp_path / "ca.pem"
    certificate.touch()
    key.touch()
    ca_bundle.touch()

    result = cli_runtime.invoke(
        "--client-cert",
        str(certificate),
        "--client-key",
        str(key),
        "--ca-bundle",
        str(ca_bundle),
        "--no-tls-verify",
        kea.target,
    )

    assert result.exit_code == 0
    assert "--no-tls-verify takes precedence over --ca-bundle" in result.stderr
    assert statistic_request_count(kea) == 1


def test_wsgi_app_waits_one_interval_after_the_startup_scrape(cli_runtime, http_server):
    kea = http_server()
    result = cli_runtime.invoke("--interval", "60", kea.target)
    assert result.exit_code == 0

    scrape(cli_runtime.httpd.app)
    assert statistic_request_count(kea) == 1

    cli_runtime.clock.monotonic_now = 1059.0
    scrape(cli_runtime.httpd.app)
    assert statistic_request_count(kea) == 1

    cli_runtime.clock.monotonic_now = 1060.0
    scrape(cli_runtime.httpd.app)
    assert statistic_request_count(kea) == 2

    scrape(cli_runtime.httpd.app)
    assert statistic_request_count(kea) == 2


def test_a_backwards_wall_clock_step_does_not_stall_scrapes(cli_runtime, http_server):
    kea = http_server()
    result = cli_runtime.invoke("--interval", "60", kea.target)
    assert result.exit_code == 0

    cli_runtime.clock.monotonic_now = 1061.0
    cli_runtime.clock.wall_now = 1400.0
    scrape(cli_runtime.httpd.app)

    assert statistic_request_count(kea) == 2


def test_wsgi_app_scrapes_on_every_request_when_the_interval_is_zero(cli_runtime, http_server):
    kea = http_server()
    result = cli_runtime.invoke("--interval", "0", kea.target)
    assert result.exit_code == 0

    scrape(cli_runtime.httpd.app)
    assert statistic_request_count(kea) == 2

    scrape(cli_runtime.httpd.app)
    assert statistic_request_count(kea) == 3


def test_concurrent_wsgi_requests_share_the_interval_gate(cli_runtime, http_server):
    kea = http_server()
    result = cli_runtime.invoke("--interval", "60", kea.target)
    assert result.exit_code == 0
    cli_runtime.clock.monotonic_now = 1060.0
    callers_ready = threading.Barrier(2)

    def concurrent_scrape():
        callers_ready.wait()
        scrape(cli_runtime.httpd.app)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(concurrent_scrape) for _ in range(2)]
        for future in futures:
            future.result()

    assert statistic_request_count(kea) == 2


def test_sigint_is_ignored_before_server_shutdown(cli_runtime, http_server, monkeypatch):
    kea = http_server()

    def record_signal(_signum, handler):
        cli_runtime.httpd.events.append("ignore" if handler is signal.SIG_IGN else "restore")
        return "previous handler"

    monkeypatch.setattr(signal, "signal", record_signal)

    result = cli_runtime.invoke(kea.target)

    assert result.exit_code == 0
    assert cli_runtime.httpd.events == ["ignore", "shutdown", "close", "restore"]


def test_shutdown_failure_is_reported_and_the_signal_handler_is_restored(
    cli_runtime,
    http_server,
    monkeypatch,
):
    kea = http_server()
    cli_runtime.httpd.shutdown_error = RuntimeError("cannot stop server")

    def record_signal(_signum, handler):
        cli_runtime.httpd.events.append("ignore" if handler is signal.SIG_IGN else "restore")
        return "previous handler"

    monkeypatch.setattr(signal, "signal", record_signal)

    result = cli_runtime.invoke(kea.target)

    assert result.exit_code == 0
    assert "Error during shutdown: cannot stop server" in result.stderr
    assert cli_runtime.httpd.events == ["ignore", "shutdown", "restore"]
