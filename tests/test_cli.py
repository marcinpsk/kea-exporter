"""Behavior of the command-line entry point through a local Kea server."""

import signal
import socket
import threading
from queue import Queue
from wsgiref.util import setup_testing_defaults

import pytest
from click.testing import CliRunner
from prometheus_client import CollectorRegistry

from kea_exporter import __version__
from kea_exporter.cli import cli
from tests.support import KeaResponse, stat


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


def test_help_explains_runtime_behavior_and_shows_defaults():
    result = CliRunner().invoke(cli, ["--help"], prog_name="kea-exporter")

    assert result.exit_code == 0
    introduction, _options = result.output.split("\n\nOptions:\n", maxsplit=1)
    paragraphs = [" ".join(paragraph.split()) for paragraph in introduction.split("\n\n")]
    assert paragraphs == [
        "Usage: kea-exporter [OPTIONS] TARGETS...",
        "Read Kea statistics and expose them as Prometheus metrics.",
        "TARGETS are Kea HTTP URLs or Unix socket paths.",
        (
            "The exporter completes one scrape cycle at startup. It then serves Prometheus metrics over HTTP. "
            "Each request starts a scrape cycle unless the configured interval has not elapsed."
        ),
    ]
    assert "Parameters:" not in result.output
    assert "[default: 0.0.0.0]" in result.output
    assert "[default: 9547]" in result.output
    assert "[default: 0]" in result.output
    assert "[default: 10; x>=1]" in result.output
    assert "[default: 0; x>=0]" in result.output
    options = " ".join(_options.split())
    assert "Minimum interval between scrape cycles, in seconds." in options
    assert "Write one summary to stderr after each scrape cycle." in options
    assert (
        "Remove stale labels this many seconds after a source's last success. Set to 0 to wait for its next success."
    ) in options


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


def test_cli_keeps_valid_metrics_when_another_reading_is_not_numeric(cli_runtime, http_server):
    statistics = [
        {
            "result": 0,
            "arguments": {
                "pkt4-ack-sent": [["not-a-number", "2026-01-01 00:00:00.000000"]],
                "pkt4-offer-sent": stat(9),
            },
        }
    ]
    kea = http_server(statistic_get_all=statistics)

    result = cli_runtime.invoke(kea.target)

    assert result.exit_code == 0
    assert (
        cli_runtime.registry.get_sample_value(
            "kea_dhcp4_packets_sent_total",
            {"server": kea.target, "operation": "offer"},
        )
        == 9
    )
    assert "value 'not-a-number' is not a number" in result.stderr
    assert f"Failed to collect statistics from {kea.target}" not in result.stderr


def test_cli_redacts_credentials_from_an_unparsable_target():
    result = CliRunner().invoke(cli, ["http://user:secret@[::1"])

    assert result.exit_code == 1
    assert "Failed to initialize target <unparsable target with credentials>" in result.stderr
    assert "secret" not in result.output


def test_cli_announces_startup_and_shutdown(cli_runtime, http_server):
    kea = http_server()

    result = cli_runtime.invoke(kea.target)

    assert result.exit_code == 0
    assert f"Starting kea-exporter {__version__}" in result.stderr
    assert "Received signal, shutting down." in result.stderr
    assert "Scrape complete:" not in result.stderr
    assert result.stdout == "Listening on http://0.0.0.0:9547\n"
    assert cli_runtime.httpd.shutdown_calls == 1
    assert cli_runtime.httpd.close_calls == 1


@pytest.mark.parametrize(
    ("arguments", "env"),
    [
        pytest.param(("--verbose",), None, id="option"),
        pytest.param((), {"VERBOSE": "1"}, id="environment"),
    ],
)
def test_verbose_reports_the_startup_scrape(cli_runtime, http_server, arguments, env):
    kea = http_server()

    result = cli_runtime.invoke(*arguments, kea.target, env=env)

    assert result.exit_code == 0
    assert result.stderr.count("Scrape complete:") == 1
    assert (
        "Scrape complete: 1/1 target reached, 1/1 source succeeded, 1 statistic received, "
        "1 label combination updated, 0 stale labels removed in 0 ms"
    ) in result.stderr


def test_verbose_reports_partial_target_success(cli_runtime, http_server):
    unavailable = http_server(config_get=KeaResponse({}, status=503))
    available = http_server()

    result = cli_runtime.invoke("--verbose", unavailable.target, available.target)

    assert result.exit_code == 0
    assert f"Failed to collect statistics from {unavailable.target}" in result.stderr
    assert (
        "Scrape complete: 1/2 targets reached, 1/1 source succeeded, 1 statistic received, "
        "1 label combination updated, 0 stale labels removed in 0 ms"
    ) in result.stderr


def test_verbose_reports_a_failed_source_within_a_reached_target(cli_runtime, http_server):
    configuration = [
        {
            "result": 0,
            "arguments": {
                "Control-agent": {
                    "control-sockets": {
                        "dhcp4": {"socket-type": "unix"},
                        "dhcp6": {"socket-type": "unix"},
                    }
                }
            },
        }
    ]
    statistics = [
        {"result": 0, "arguments": {"pkt4-ack-sent": stat(7)}},
        {"result": 1, "text": "DHCP6 unavailable"},
    ]
    kea = http_server(configuration, statistics)

    result = cli_runtime.invoke("--verbose", kea.target)

    assert result.exit_code == 0
    assert f"Failed to collect DHCP6 source from {kea.target}: KeaCommandError: DHCP6 unavailable" in result.stderr
    assert (
        "Scrape complete: 1/1 target reached, 1/2 sources succeeded, 1 statistic received, "
        "1 label combination updated, 0 stale labels removed in 0 ms"
    ) in result.stderr


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


def test_cli_reports_an_occupied_listen_port_without_a_traceback(http_server, monkeypatch):
    kea = http_server()
    registry = CollectorRegistry()
    monkeypatch.setattr("kea_exporter.cli.REGISTRY", registry)
    monkeypatch.setattr("prometheus_client.REGISTRY", registry)

    with socket.socket() as occupied_port:
        occupied_port.bind(("127.0.0.1", 0))
        occupied_port.listen()
        port = occupied_port.getsockname()[1]

        result = CliRunner().invoke(
            cli,
            ["--address", "127.0.0.1", "--port", str(port), kea.target],
        )

    assert result.exit_code == 1
    assert f"Error: Cannot listen on http://127.0.0.1:{port}" in result.stderr
    assert "Address already in use" in result.stderr
    assert "Traceback" not in result.output
    assert not isinstance(result.exception, OSError)


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


def test_verbose_reports_only_scrapes_that_pass_the_interval_gate(cli_runtime, http_server, capsys):
    kea = http_server()
    result = cli_runtime.invoke("--verbose", "--interval", "60", kea.target)
    assert result.exit_code == 0
    assert result.stderr.count("Scrape complete:") == 1
    capsys.readouterr()

    scrape(cli_runtime.httpd.app)
    assert "Scrape complete:" not in capsys.readouterr().err

    cli_runtime.clock.monotonic_now = 1060.0
    scrape(cli_runtime.httpd.app)
    assert capsys.readouterr().err.count("Scrape complete:") == 1


def test_verbose_reports_stale_labels_removed_by_a_scrape(cli_runtime, http_server, capsys):
    kea = http_server()
    result = cli_runtime.invoke("--verbose", kea.target)
    assert result.exit_code == 0
    kea.control.responses["statistic-get-all"] = [{"result": 0, "arguments": {}}]
    capsys.readouterr()

    scrape(cli_runtime.httpd.app)

    assert (
        "Scrape complete: 1/1 target reached, 1/1 source succeeded, 0 statistics received, "
        "0 label combinations updated, 1 stale label removed in 0 ms"
    ) in capsys.readouterr().err


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
    errors = Queue()

    def concurrent_scrape():
        try:
            callers_ready.wait(timeout=15)
            scrape(cli_runtime.httpd.app)
        except Exception as error:
            errors.put(error)

    workers = [threading.Thread(target=concurrent_scrape, daemon=True) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=15)

    assert not [worker for worker in workers if worker.is_alive()], "concurrent scrapes did not finish"
    if not errors.empty():
        raise errors.get()
    assert statistic_request_count(kea) == 2


def test_wsgi_render_and_update_share_one_lock(cli_runtime, http_server):
    class BlockingCollector:
        def __init__(self):
            self.first_render = threading.Event()
            self.second_render = threading.Event()
            self.release_first = threading.Event()
            self.calls = 0
            self.lock = threading.Lock()

        def describe(self):
            return []

        def collect(self):
            with self.lock:
                self.calls += 1
                call = self.calls
            if call == 1:
                self.first_render.set()
                if not self.release_first.wait(timeout=15):
                    raise TimeoutError("first render was not released")
            else:
                self.second_render.set()
            return []

    kea = http_server()
    result = cli_runtime.invoke("--interval", "0", kea.target)
    assert result.exit_code == 0
    collector = BlockingCollector()
    cli_runtime.registry.register(collector)
    errors = Queue()

    def concurrent_scrape():
        try:
            scrape(cli_runtime.httpd.app)
        except Exception as error:
            errors.put(error)

    workers = [threading.Thread(target=concurrent_scrape, daemon=True) for _ in range(2)]
    workers[0].start()
    try:
        assert collector.first_render.wait(timeout=15)
        workers[1].start()
        second_render_started_early = collector.second_render.wait(timeout=5)
    finally:
        collector.release_first.set()
        for worker in workers:
            if worker.ident is not None:
                worker.join(timeout=15)

    assert not [worker for worker in workers if worker.is_alive()], "concurrent scrapes did not finish"
    if not errors.empty():
        raise errors.get()
    assert not second_render_started_early, "another scrape started while the first response was rendering"
    assert collector.second_render.is_set()
    assert statistic_request_count(kea) == 3


def test_sigint_is_ignored_before_server_shutdown(cli_runtime, http_server, monkeypatch):
    kea = http_server()
    previous_handler = object()
    installed_handlers = []

    def record_signal(_signum, handler):
        installed_handlers.append(handler)
        cli_runtime.httpd.events.append("ignore" if handler is signal.SIG_IGN else "restore")
        return previous_handler

    monkeypatch.setattr(signal, "signal", record_signal)

    result = cli_runtime.invoke(kea.target)

    assert result.exit_code == 0
    assert cli_runtime.httpd.events == ["ignore", "shutdown", "close", "restore"]
    assert installed_handlers == [signal.SIG_IGN, previous_handler]


def test_shutdown_failure_is_reported_and_the_signal_handler_is_restored(
    cli_runtime,
    http_server,
    monkeypatch,
):
    kea = http_server()
    cli_runtime.httpd.shutdown_error = RuntimeError("cannot stop server")
    previous_handler = object()
    installed_handlers = []

    def record_signal(_signum, handler):
        installed_handlers.append(handler)
        cli_runtime.httpd.events.append("ignore" if handler is signal.SIG_IGN else "restore")
        return previous_handler

    monkeypatch.setattr(signal, "signal", record_signal)

    result = cli_runtime.invoke(kea.target)

    assert result.exit_code == 0
    assert "Error during shutdown: cannot stop server" in result.stderr
    assert cli_runtime.httpd.events == ["ignore", "shutdown", "restore"]
    assert installed_handlers == [signal.SIG_IGN, previous_handler]
