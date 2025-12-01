"""
Tests for kea_exporter.cli module
"""

import unittest
from unittest.mock import Mock, patch

from prometheus_client import CollectorRegistry

from kea_exporter.cli import Timer, cli


class TestTimer(unittest.TestCase):
    """Test Timer class"""

    @patch("kea_exporter.cli.time.time")
    def test_timer_init(self, mock_time):
        """Test Timer initialization"""
        mock_time.return_value = 100.0  # Fake starting time
        timer = Timer()
        self.assertIsNotNone(timer.start_time)
        self.assertEqual(timer.start_time, 100.0)

    @patch("kea_exporter.cli.time.time")
    def test_timer_reset(self, mock_time):
        """Test Timer reset"""
        # Start with initial time
        mock_time.return_value = 100.0
        timer = Timer()
        original_start = timer.start_time

        # Advance time and reset
        mock_time.return_value = 100.05  # 50ms later
        timer.reset()

        self.assertNotEqual(timer.start_time, original_start)
        self.assertGreater(timer.start_time, original_start)
        self.assertEqual(timer.start_time, 100.05)

    @patch("kea_exporter.cli.time.time")
    def test_timer_time_elapsed(self, mock_time):
        """Test time_elapsed calculation"""
        # Start at time 100.0
        mock_time.return_value = 100.0
        timer = Timer()

        # Advance time by 50ms
        mock_time.return_value = 100.05
        elapsed = timer.time_elapsed()

        self.assertAlmostEqual(elapsed, 0.05, places=7)

    @patch("kea_exporter.cli.time.time")
    def test_timer_time_elapsed_increases(self, mock_time):
        """Test that time_elapsed increases over time"""
        # Start at time 100.0
        mock_time.return_value = 100.0
        timer = Timer()

        # First check at 100.01 (10ms elapsed)
        mock_time.return_value = 100.01
        elapsed1 = timer.time_elapsed()

        # Second check at 100.03 (30ms elapsed)
        mock_time.return_value = 100.03
        elapsed2 = timer.time_elapsed()

        self.assertGreater(elapsed2, elapsed1)
        self.assertAlmostEqual(elapsed1, 0.01, places=7)
        self.assertAlmostEqual(elapsed2, 0.03, places=7)

    @patch("kea_exporter.cli.time.time")
    def test_timer_reset_resets_elapsed(self, mock_time):
        """Test that reset resets the elapsed time"""
        # Start at time 100.0
        mock_time.return_value = 100.0
        timer = Timer()

        # Advance time by 50ms
        mock_time.return_value = 100.05
        elapsed_before = timer.time_elapsed()

        # Reset at 100.10 (should be new start time)
        mock_time.return_value = 100.10
        timer.reset()

        # Check elapsed immediately after reset (should be 0)
        elapsed_after = timer.time_elapsed()

        self.assertLess(elapsed_after, elapsed_before)
        self.assertAlmostEqual(elapsed_after, 0.0, places=7)
        self.assertAlmostEqual(elapsed_before, 0.05, places=7)


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
        import tempfile
        import os

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

    @patch("kea_exporter.cli.Exporter")
    @patch("kea_exporter.cli.start_http_server")
    @patch("kea_exporter.cli.make_wsgi_app")
    @patch("kea_exporter.cli.time.sleep")
    def test_wsgi_app_updates_on_interval(self, mock_sleep, mock_wsgi, mock_http_server, mock_exporter):
        """Test that WSGI app calls update based on interval"""
        mock_sleep.side_effect = KeyboardInterrupt

        mock_exporter_instance = Mock()
        mock_exporter_instance.targets = [Mock()]
        mock_exporter.return_value = mock_exporter_instance

        mock_httpd = Mock()
        mock_http_server.return_value = (mock_httpd, Mock())

        # Mock the WSGI app
        mock_wsgi_func = Mock(return_value=[b"metrics"])
        mock_wsgi.return_value = mock_wsgi_func

        from click.testing import CliRunner

        runner = CliRunner()

        result = runner.invoke(cli, ["--interval", "0", "http://localhost:8000"])

        self.assertEqual(result.exit_code, 0)
        # Check that set_app was called
        mock_httpd.set_app.assert_called_once()

        # Get the local_wsgi_app function that was set
        wsgi_app = mock_httpd.set_app.call_args[0][0]

        # Call the app to test interval logic
        environ = {}
        start_response = Mock()

        # First call should trigger update (elapsed >= interval)
        wsgi_app(environ, start_response)
        # Second call should also trigger with interval=0
        wsgi_app(environ, start_response)

        # With interval=0, update should be called every time
        self.assertGreaterEqual(mock_exporter_instance.update.call_count, 2)


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


if __name__ == "__main__":
    unittest.main()
