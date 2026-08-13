"""
Tests for kea_exporter.cli module
"""

import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch
from wsgiref.util import setup_testing_defaults

from prometheus_client import CollectorRegistry

from kea_exporter.cli import cli
from tests.support import InMemoryTarget, exporter_with


class _CapturingHTTPServer:
    def __init__(self):
        self.app = None

    def set_app(self, app):
        self.app = app

    def shutdown(self):
        pass

    def server_close(self):
        pass


class TestCLIOptions(unittest.TestCase):
    """Test CLI option parsing"""

    def setUp(self):
        self.registry = CollectorRegistry()
        self.patcher1 = patch("kea_exporter.cli.REGISTRY", self.registry)
        self.patcher2 = patch("prometheus_client.REGISTRY", self.registry)
        self.patcher1.start()
        self.patcher2.start()

    def tearDown(self):
        self.patcher1.stop()
        self.patcher2.stop()

    @patch("kea_exporter.cli.Exporter")
    @patch("kea_exporter.cli.start_http_server")
    @patch("kea_exporter.cli.time.sleep")
    def test_cli_default_port(self, mock_sleep, mock_http_server, mock_exporter):
        """Test CLI with default port"""
        # Exit after first iteration
        mock_sleep.side_effect = KeyboardInterrupt
        mock_exporter.return_value.targets = [Mock()]
        mock_http_server.return_value = (Mock(), Mock())

        from click.testing import CliRunner

        runner = CliRunner()

        result = runner.invoke(cli, ["http://localhost:8000"])

        # Should handle error and continue
        self.assertEqual(result.exit_code, 0)
        mock_http_server.assert_called_once()
        call_args = mock_http_server.call_args[0]
        self.assertEqual(call_args[0], 9547)  # port
        mock_http_server.return_value[0].shutdown.assert_called_once()
        mock_http_server.return_value[0].server_close.assert_called_once()

    @patch("kea_exporter.cli.Exporter")
    @patch("kea_exporter.cli.start_http_server")
    @patch("kea_exporter.cli.time.sleep")
    def test_cli_custom_port(self, mock_sleep, mock_http_server, mock_exporter):
        """Test CLI with custom port"""
        mock_sleep.side_effect = KeyboardInterrupt
        mock_exporter.return_value.targets = [Mock()]
        mock_http_server.return_value = (Mock(), Mock())

        from click.testing import CliRunner

        runner = CliRunner()

        result = runner.invoke(cli, ["--port", "8080", "http://localhost:8000"])

        self.assertEqual(result.exit_code, 0)
        mock_http_server.assert_called_once()
        call_args = mock_http_server.call_args[0]
        self.assertEqual(call_args[0], 8080)

    @patch("kea_exporter.cli.Exporter")
    @patch("kea_exporter.cli.start_http_server")
    @patch("kea_exporter.cli.time.sleep")
    def test_cli_default_address(self, mock_sleep, mock_http_server, mock_exporter):
        """Test CLI with default address"""
        mock_sleep.side_effect = KeyboardInterrupt
        mock_exporter.return_value.targets = [Mock()]
        mock_http_server.return_value = (Mock(), Mock())

        from click.testing import CliRunner

        runner = CliRunner()

        result = runner.invoke(cli, ["http://localhost:8000"])

        self.assertEqual(result.exit_code, 0)
        # Default address is 0.0.0.0
        mock_http_server.assert_called_once()
        call_args = mock_http_server.call_args[0]
        self.assertEqual(call_args[1], "0.0.0.0")  # address

    @patch("kea_exporter.cli.Exporter")
    @patch("kea_exporter.cli.start_http_server")
    @patch("kea_exporter.cli.time.sleep")
    def test_cli_custom_address(self, mock_sleep, mock_http_server, mock_exporter):
        """Test CLI with custom address"""
        mock_sleep.side_effect = KeyboardInterrupt
        mock_exporter.return_value.targets = [Mock()]
        mock_http_server.return_value = (Mock(), Mock())

        from click.testing import CliRunner

        runner = CliRunner()

        result = runner.invoke(cli, ["--address", "127.0.0.1", "http://localhost:8000"])

        self.assertEqual(result.exit_code, 0)
        mock_http_server.assert_called_once()
        call_args = mock_http_server.call_args[0]
        self.assertEqual(call_args[1], "127.0.0.1")

    @patch("kea_exporter.cli.Exporter")
    @patch("kea_exporter.cli.start_http_server")
    @patch("kea_exporter.cli.time.sleep")
    def test_cli_custom_timeout(self, mock_sleep, mock_http_server, mock_exporter):
        """Test CLI with custom timeout parameter"""
        mock_sleep.side_effect = KeyboardInterrupt
        mock_exporter.return_value.targets = [Mock()]
        mock_http_server.return_value = (Mock(), Mock())

        from click.testing import CliRunner

        runner = CliRunner()

        result = runner.invoke(cli, ["--timeout", "30", "http://localhost:8000"])

        self.assertEqual(result.exit_code, 0)
        # Check that timeout was passed to Exporter
        mock_exporter.assert_called_once()
        call_kwargs = mock_exporter.call_args[1]
        self.assertEqual(call_kwargs["timeout"], 30)

    @patch("kea_exporter.cli.Exporter")
    @patch("kea_exporter.cli.start_http_server")
    @patch("kea_exporter.cli.time.sleep")
    def test_cli_default_timeout(self, mock_sleep, mock_http_server, mock_exporter):
        """Test CLI with default timeout"""
        mock_sleep.side_effect = KeyboardInterrupt
        mock_exporter.return_value.targets = [Mock()]
        mock_http_server.return_value = (Mock(), Mock())

        from click.testing import CliRunner

        runner = CliRunner()

        result = runner.invoke(cli, ["http://localhost:8000"])

        self.assertEqual(result.exit_code, 0)
        # Default timeout is 10
        mock_exporter.assert_called_once()
        call_kwargs = mock_exporter.call_args[1]
        self.assertEqual(call_kwargs["timeout"], 10)

    @patch("kea_exporter.cli.Exporter")
    def test_cli_no_targets_exits(self, mock_exporter):
        """Test that CLI exits when no targets are configured"""
        mock_exporter.return_value.targets = []

        from click.testing import CliRunner

        runner = CliRunner()

        result = runner.invoke(cli, ["http://localhost:8000"])

        self.assertEqual(result.exit_code, 1)

    @patch("kea_exporter.cli.Exporter")
    @patch("kea_exporter.cli.start_http_server")
    @patch("kea_exporter.cli.time.sleep")
    def test_cli_multiple_targets(self, mock_sleep, mock_http_server, mock_exporter):
        """Test CLI with multiple targets"""
        mock_sleep.side_effect = KeyboardInterrupt
        mock_exporter.return_value.targets = [Mock(), Mock()]
        mock_http_server.return_value = (Mock(), Mock())

        from click.testing import CliRunner

        runner = CliRunner()

        result = runner.invoke(cli, ["http://server1:8000", "http://server2:8000", "/var/run/kea/socket"])

        self.assertEqual(result.exit_code, 0)
        # Should pass all targets to Exporter
        mock_exporter.assert_called_once()
        call_kwargs = mock_exporter.call_args[1]
        targets = call_kwargs["targets"]
        self.assertEqual(len(targets), 3)

    @patch("kea_exporter.cli.Exporter")
    @patch("kea_exporter.cli.start_http_server")
    @patch("kea_exporter.cli.time.sleep")
    def test_cli_interval_parameter(self, mock_sleep, mock_http_server, mock_exporter):
        """Test CLI interval parameter"""
        mock_sleep.side_effect = KeyboardInterrupt
        mock_exporter.return_value.targets = [Mock()]
        mock_http_server.return_value = (Mock(), Mock())

        from click.testing import CliRunner

        runner = CliRunner()

        result = runner.invoke(cli, ["--interval", "5", "http://localhost:8000"])

        # Interval should be used in the timer logic (tested in integration)
        self.assertEqual(result.exit_code, 0)

    @patch("kea_exporter.cli.Exporter")
    @patch("kea_exporter.cli.start_http_server")
    @patch("kea_exporter.cli.time.sleep")
    def test_cli_client_cert_options(self, mock_sleep, mock_http_server, mock_exporter):
        """Test CLI with client certificate options"""
        mock_sleep.side_effect = KeyboardInterrupt
        mock_exporter.return_value.targets = [Mock()]
        mock_http_server.return_value = (Mock(), Mock())

        from click.testing import CliRunner

        runner = CliRunner()

        # Create temporary files for testing
        cert_file = tempfile.NamedTemporaryFile(delete=False)
        key_file = tempfile.NamedTemporaryFile(delete=False)
        cert_path = cert_file.name
        key_path = key_file.name
        cert_file.close()
        key_file.close()
        self.addCleanup(os.unlink, cert_path)
        self.addCleanup(os.unlink, key_path)

        result = runner.invoke(cli, ["--client-cert", cert_path, "--client-key", key_path, "https://localhost:8000"])

        self.assertEqual(result.exit_code, 0)
        # Check that cert and key were passed to Exporter
        mock_exporter.assert_called_once()
        call_kwargs = mock_exporter.call_args[1]
        self.assertEqual(call_kwargs["client_cert"], cert_path)
        self.assertEqual(call_kwargs["client_key"], key_path)

    @patch("kea_exporter.cli.Exporter")
    @patch("kea_exporter.cli.start_http_server")
    @patch("kea_exporter.cli.time.sleep")
    def test_no_tls_verify_flag_forwarded_to_exporter(self, mock_sleep, mock_http_server, mock_exporter):
        """--no-tls-verify is parsed and forwarded to Exporter as tls_no_verify=True."""
        mock_sleep.side_effect = KeyboardInterrupt
        mock_exporter.return_value.targets = [Mock()]
        mock_http_server.return_value = (Mock(), Mock())
        from click.testing import CliRunner

        runner = CliRunner()
        runner.invoke(cli, ["--no-tls-verify", "https://kea:443"])
        call_kwargs = mock_exporter.call_args.kwargs
        self.assertTrue(call_kwargs.get("tls_no_verify"))

    @patch("kea_exporter.cli.Exporter")
    @patch("kea_exporter.cli.start_http_server")
    @patch("kea_exporter.cli.time.sleep")
    def test_ca_bundle_option_forwarded_to_exporter(self, mock_sleep, mock_http_server, mock_exporter):
        """--ca-bundle PATH is parsed and forwarded to Exporter as ca_bundle=<path>."""
        mock_sleep.side_effect = KeyboardInterrupt
        mock_exporter.return_value.targets = [Mock()]
        mock_http_server.return_value = (Mock(), Mock())
        from click.testing import CliRunner

        runner = CliRunner()
        with tempfile.NamedTemporaryFile() as f:
            runner.invoke(cli, ["--ca-bundle", f.name, "https://kea:443"])
            call_kwargs = mock_exporter.call_args.kwargs
            self.assertEqual(call_kwargs.get("ca_bundle"), f.name)


class TestCLIWSGIApp(unittest.TestCase):
    """Test WSGI app behavior"""

    def setUp(self):
        self.registry = CollectorRegistry()
        self.patcher1 = patch("kea_exporter.cli.REGISTRY", self.registry)
        self.patcher2 = patch("prometheus_client.REGISTRY", self.registry)
        self.patcher1.start()
        self.patcher2.start()

    def tearDown(self):
        self.patcher1.stop()
        self.patcher2.stop()

    def _start_wsgi_app(self, interval, wall_clock=None, target=None):
        from click.testing import CliRunner

        clock = {"now": 1000.0}
        if target is None:
            target = InMemoryTarget()
        httpd = _CapturingHTTPServer()

        def exporter_factory(**_kwargs):
            return exporter_with(self.registry, target)

        clock_patcher = patch("kea_exporter.cli.time.monotonic", side_effect=lambda: clock["now"])
        clock_patcher.start()
        self.addCleanup(clock_patcher.stop)
        if wall_clock is not None:
            wall_clock_patcher = patch("kea_exporter.cli.time.time", side_effect=lambda: wall_clock["now"])
            wall_clock_patcher.start()
            self.addCleanup(wall_clock_patcher.stop)

        with (
            patch("kea_exporter.cli.Exporter", new=exporter_factory),
            patch("kea_exporter.cli.start_http_server", return_value=(httpd, None)),
            patch("kea_exporter.cli.time.sleep", side_effect=KeyboardInterrupt),
        ):
            result = CliRunner().invoke(cli, ["--interval", str(interval), "http://localhost:8000"])

        self.assertEqual(result.exit_code, 0)
        self.assertIsNotNone(httpd.app)
        return httpd.app, target, clock

    def _scrape(self, app):
        environ = {}
        setup_testing_defaults(environ)
        response = {}

        def start_response(status, headers, exc_info=None):
            response["status"] = status
            response["headers"] = headers

        body = b"".join(app(environ, start_response))
        self.assertEqual(response["status"], "200 OK")
        self.assertTrue(body)

    def test_wsgi_app_skips_scrapes_until_the_interval_elapses_and_resets_the_timestamp(self):
        app, target, clock = self._start_wsgi_app(60)

        self._scrape(app)
        # The startup skip is current behavior, but it is questionable.
        self.assertEqual(target.calls, 0)

        clock["now"] = 1059.0
        self._scrape(app)
        self.assertEqual(target.calls, 0)

        clock["now"] = 1060.0
        self._scrape(app)
        self.assertEqual(target.calls, 1)

        self._scrape(app)
        self.assertEqual(target.calls, 1)

    def test_a_backwards_wall_clock_step_does_not_stall_scrapes(self):
        wall_clock = {"now": 5000.0}
        app, target, clock = self._start_wsgi_app(60, wall_clock)

        clock["now"] = 1061.0
        wall_clock["now"] = 1400.0
        self._scrape(app)

        self.assertEqual(target.calls, 1)

    def test_wsgi_app_scrapes_on_every_request_when_the_interval_is_zero(self):
        app, target, _clock = self._start_wsgi_app(0)

        self._scrape(app)
        self.assertEqual(target.calls, 1)

        self._scrape(app)
        self.assertEqual(target.calls, 2)

    def test_concurrent_wsgi_requests_share_the_interval_gate(self):
        class CoordinatedTarget(InMemoryTarget):
            def __init__(self):
                super().__init__()
                self.entered = threading.Barrier(2)

            def stats(self):
                self.calls += 1
                try:
                    self.entered.wait(timeout=1)
                except threading.BrokenBarrierError:
                    pass
                yield from self.rows

        app, target, clock = self._start_wsgi_app(60, target=CoordinatedTarget())
        clock["now"] = 1060.0

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(self._scrape, app) for _ in range(2)]
            for future in futures:
                future.result()

        self.assertEqual(target.calls, 1)


class TestCLIEnvironmentVariables(unittest.TestCase):
    """Test CLI environment variable support"""

    def setUp(self):
        self.registry = CollectorRegistry()
        self.patcher1 = patch("kea_exporter.cli.REGISTRY", self.registry)
        self.patcher2 = patch("prometheus_client.REGISTRY", self.registry)
        self.patcher1.start()
        self.patcher2.start()

    def tearDown(self):
        self.patcher1.stop()
        self.patcher2.stop()

    @patch("kea_exporter.cli.Exporter")
    @patch("kea_exporter.cli.start_http_server")
    @patch("kea_exporter.cli.time.sleep")
    def test_cli_port_from_env(self, mock_sleep, mock_http_server, mock_exporter):
        """Test PORT environment variable"""
        mock_sleep.side_effect = KeyboardInterrupt
        mock_exporter.return_value.targets = [Mock()]
        mock_http_server.return_value = (Mock(), Mock())

        from click.testing import CliRunner

        runner = CliRunner()

        result = runner.invoke(cli, ["http://localhost:8000"], env={"PORT": "9999"})

        self.assertEqual(result.exit_code, 0)
        mock_http_server.assert_called_once()
        call_args = mock_http_server.call_args[0]
        self.assertEqual(call_args[0], 9999)

    @patch("kea_exporter.cli.Exporter")
    @patch("kea_exporter.cli.start_http_server")
    @patch("kea_exporter.cli.time.sleep")
    def test_cli_address_from_env(self, mock_sleep, mock_http_server, mock_exporter):
        """Test ADDRESS environment variable"""
        mock_sleep.side_effect = KeyboardInterrupt
        mock_exporter.return_value.targets = [Mock()]
        mock_http_server.return_value = (Mock(), Mock())

        from click.testing import CliRunner

        runner = CliRunner()

        result = runner.invoke(cli, ["http://localhost:8000"], env={"ADDRESS": "192.168.1.1"})

        self.assertEqual(result.exit_code, 0)
        mock_http_server.assert_called_once()
        call_args = mock_http_server.call_args[0]
        self.assertEqual(call_args[1], "192.168.1.1")

    @patch("kea_exporter.cli.Exporter")
    @patch("kea_exporter.cli.start_http_server")
    @patch("kea_exporter.cli.time.sleep")
    def test_cli_timeout_from_env(self, mock_sleep, mock_http_server, mock_exporter):
        """Test TIMEOUT environment variable"""
        mock_sleep.side_effect = KeyboardInterrupt
        mock_exporter.return_value.targets = [Mock()]
        mock_http_server.return_value = (Mock(), Mock())

        from click.testing import CliRunner

        runner = CliRunner()

        result = runner.invoke(cli, ["http://localhost:8000"], env={"TIMEOUT": "25"})

        self.assertEqual(result.exit_code, 0)
        mock_exporter.assert_called_once()
        call_kwargs = mock_exporter.call_args[1]
        self.assertEqual(call_kwargs["timeout"], 25)

    @patch("kea_exporter.cli.Exporter")
    @patch("kea_exporter.cli.start_http_server")
    @patch("kea_exporter.cli.time.sleep")
    def test_cli_targets_from_env(self, mock_sleep, mock_http_server, mock_exporter):
        """Test TARGETS environment variable"""
        mock_sleep.side_effect = KeyboardInterrupt
        mock_exporter.return_value.targets = [Mock()]
        mock_http_server.return_value = (Mock(), Mock())

        from click.testing import CliRunner

        runner = CliRunner()

        result = runner.invoke(cli, [], env={"TARGETS": "http://server1:8000 http://server2:8000"})

        self.assertEqual(result.exit_code, 0)
        mock_exporter.assert_called_once()
        call_kwargs = mock_exporter.call_args[1]
        # Click parses space-separated targets from env
        targets = call_kwargs["targets"]
        self.assertEqual(len(targets), 2)


class TestCLIShutdown(unittest.TestCase):
    """Test CLI shutdown behaviour"""

    def setUp(self):
        self.registry = CollectorRegistry()
        self.patcher1 = patch("kea_exporter.cli.REGISTRY", self.registry)
        self.patcher2 = patch("prometheus_client.REGISTRY", self.registry)
        self.patcher1.start()
        self.patcher2.start()

    def tearDown(self):
        self.patcher1.stop()
        self.patcher2.stop()

    @patch("kea_exporter.cli.Exporter")
    @patch("kea_exporter.cli.start_http_server")
    @patch("kea_exporter.cli.time.sleep")
    @patch("signal.signal")
    def test_sigint_ignored_before_httpd_shutdown(self, mock_signal, mock_sleep, mock_http_server, mock_exporter):
        """signal.signal(SIGINT, SIG_IGN) is called before httpd.shutdown() on KeyboardInterrupt."""
        import signal as signal_module

        mock_sleep.side_effect = KeyboardInterrupt
        mock_exporter.return_value.targets = [Mock()]
        mock_httpd = Mock()
        mock_http_server.return_value = (mock_httpd, Mock())

        call_order = []
        mock_signal.side_effect = lambda *args: call_order.append("signal")
        mock_httpd.shutdown.side_effect = lambda: call_order.append("shutdown")

        from click.testing import CliRunner

        runner = CliRunner()
        runner.invoke(cli, ["http://localhost:8000"])

        # signal.signal must have been called with SIGINT, SIG_IGN (may not be the last call
        # since the handler is restored in a finally block)
        mock_signal.assert_any_call(signal_module.SIGINT, signal_module.SIG_IGN)

        # signal must have been set before shutdown was called
        self.assertIn("signal", call_order)
        self.assertIn("shutdown", call_order)
        self.assertLess(call_order.index("signal"), call_order.index("shutdown"))


if __name__ == "__main__":
    unittest.main()
