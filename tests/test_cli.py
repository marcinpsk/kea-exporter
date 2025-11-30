"""
Tests for kea_exporter.cli module
"""
import unittest
from unittest.mock import Mock, patch, MagicMock
import time

from kea_exporter.cli import Timer, cli


class TestTimer(unittest.TestCase):
    """Test Timer class"""

    def test_timer_init(self):
        """Test Timer initialization"""
        timer = Timer()
        self.assertIsNotNone(timer.start_time)
        self.assertIsInstance(timer.start_time, float)

    def test_timer_reset(self):
        """Test Timer reset"""
        timer = Timer()
        original_start = timer.start_time
        time.sleep(0.01)  # Small delay
        timer.reset()
        self.assertNotEqual(timer.start_time, original_start)
        self.assertGreater(timer.start_time, original_start)

    def test_timer_time_elapsed(self):
        """Test time_elapsed calculation"""
        timer = Timer()
        time.sleep(0.05)  # 50ms delay
        elapsed = timer.time_elapsed()
        self.assertGreater(elapsed, 0.04)  # At least 40ms (accounting for variance)
        self.assertLess(elapsed, 0.2)  # Less than 200ms

    def test_timer_time_elapsed_increases(self):
        """Test that time_elapsed increases over time"""
        timer = Timer()
        elapsed1 = timer.time_elapsed()
        time.sleep(0.01)
        elapsed2 = timer.time_elapsed()
        self.assertGreater(elapsed2, elapsed1)

    def test_timer_reset_resets_elapsed(self):
        """Test that reset resets the elapsed time"""
        timer = Timer()
        time.sleep(0.05)
        elapsed_before = timer.time_elapsed()
        timer.reset()
        elapsed_after = timer.time_elapsed()
        self.assertLess(elapsed_after, elapsed_before)
        self.assertLess(elapsed_after, 0.01)  # Should be very small after reset


class TestCLIOptions(unittest.TestCase):
    """Test CLI option parsing"""

    @patch('kea_exporter.cli.Exporter')
    @patch('kea_exporter.cli.start_http_server')
    @patch('kea_exporter.cli.time.sleep')
    def test_cli_default_port(self, mock_sleep, mock_http_server, mock_exporter):
        """Test CLI with default port"""
        mock_sleep.side_effect = KeyboardInterrupt  # Exit after first iteration
        mock_exporter.return_value.targets = [Mock()]
        mock_http_server.return_value = (Mock(), None)

        from click.testing import CliRunner
        runner = CliRunner()

        result = runner.invoke(cli, ['http://localhost:8000'])

        # Default port is 9547
        self.assertEqual(result.exit_code, 0)
        mock_http_server.assert_called_once()
        call_args = mock_http_server.call_args[0]
        self.assertEqual(call_args[0], 9547)  # port

    @patch('kea_exporter.cli.Exporter')
    @patch('kea_exporter.cli.start_http_server')
    @patch('kea_exporter.cli.time.sleep')
    def test_cli_custom_port(self, mock_sleep, mock_http_server, mock_exporter):
        """Test CLI with custom port"""
        mock_sleep.side_effect = KeyboardInterrupt
        mock_exporter.return_value.targets = [Mock()]
        mock_http_server.return_value = (Mock(), None)

        from click.testing import CliRunner
        runner = CliRunner()

        result = runner.invoke(cli, ['--port', '8080', 'http://localhost:8000'])

        mock_http_server.assert_called_once()
        call_args = mock_http_server.call_args[0]
        self.assertEqual(call_args[0], 8080)

    @patch('kea_exporter.cli.Exporter')
    @patch('kea_exporter.cli.start_http_server')
    @patch('kea_exporter.cli.time.sleep')
    def test_cli_default_address(self, mock_sleep, mock_http_server, mock_exporter):
        """Test CLI with default address"""
        mock_sleep.side_effect = KeyboardInterrupt
        mock_exporter.return_value.targets = [Mock()]
        mock_http_server.return_value = (Mock(), None)

        from click.testing import CliRunner
        runner = CliRunner()

        result = runner.invoke(cli, ['http://localhost:8000'])

        # Default address is 0.0.0.0
        mock_http_server.assert_called_once()
        call_args = mock_http_server.call_args[0]
        self.assertEqual(call_args[1], "0.0.0.0")  # address

    @patch('kea_exporter.cli.Exporter')
    @patch('kea_exporter.cli.start_http_server')
    @patch('kea_exporter.cli.time.sleep')
    def test_cli_custom_address(self, mock_sleep, mock_http_server, mock_exporter):
        """Test CLI with custom address"""
        mock_sleep.side_effect = KeyboardInterrupt
        mock_exporter.return_value.targets = [Mock()]
        mock_http_server.return_value = (Mock(), None)

        from click.testing import CliRunner
        runner = CliRunner()

        result = runner.invoke(cli, ['--address', '127.0.0.1', 'http://localhost:8000'])

        mock_http_server.assert_called_once()
        call_args = mock_http_server.call_args[0]
        self.assertEqual(call_args[1], "127.0.0.1")

    @patch('kea_exporter.cli.Exporter')
    @patch('kea_exporter.cli.start_http_server')
    @patch('kea_exporter.cli.time.sleep')
    def test_cli_custom_timeout(self, mock_sleep, mock_http_server, mock_exporter):
        """Test CLI with custom timeout parameter"""
        mock_sleep.side_effect = KeyboardInterrupt
        mock_exporter.return_value.targets = [Mock()]
        mock_http_server.return_value = (Mock(), None)

        from click.testing import CliRunner
        runner = CliRunner()

        result = runner.invoke(cli, ['--timeout', '30', 'http://localhost:8000'])

        # Check that timeout was passed to Exporter
        mock_exporter.assert_called_once()
        call_kwargs = mock_exporter.call_args[1]
        self.assertEqual(call_kwargs['timeout'], 30)

    @patch('kea_exporter.cli.Exporter')
    @patch('kea_exporter.cli.start_http_server')
    @patch('kea_exporter.cli.time.sleep')
    def test_cli_default_timeout(self, mock_sleep, mock_http_server, mock_exporter):
        """Test CLI with default timeout"""
        mock_sleep.side_effect = KeyboardInterrupt
        mock_exporter.return_value.targets = [Mock()]
        mock_http_server.return_value = (Mock(), None)

        from click.testing import CliRunner
        runner = CliRunner()

        result = runner.invoke(cli, ['http://localhost:8000'])

        # Default timeout is 10
        mock_exporter.assert_called_once()
        call_kwargs = mock_exporter.call_args[1]
        self.assertEqual(call_kwargs['timeout'], 10)

    @patch('kea_exporter.cli.Exporter')
    def test_cli_no_targets_exits(self, mock_exporter):
        """Test that CLI exits when no targets are configured"""
        mock_exporter.return_value.targets = []

        from click.testing import CliRunner
        runner = CliRunner()

        result = runner.invoke(cli, ['http://localhost:8000'])

        self.assertEqual(result.exit_code, 1)

    @patch('kea_exporter.cli.Exporter')
    @patch('kea_exporter.cli.start_http_server')
    @patch('kea_exporter.cli.time.sleep')
    def test_cli_multiple_targets(self, mock_sleep, mock_http_server, mock_exporter):
        """Test CLI with multiple targets"""
        mock_sleep.side_effect = KeyboardInterrupt
        mock_exporter.return_value.targets = [Mock(), Mock()]
        mock_http_server.return_value = (Mock(), None)

        from click.testing import CliRunner
        runner = CliRunner()

        result = runner.invoke(cli, [
            'http://server1:8000',
            'http://server2:8000',
            '/var/run/kea/socket'
        ])

        # Should pass all targets to Exporter
        mock_exporter.assert_called_once()
        call_kwargs = mock_exporter.call_args[1]
        targets = call_kwargs['targets']
        self.assertEqual(len(targets), 3)

    @patch('kea_exporter.cli.Exporter')
    @patch('kea_exporter.cli.start_http_server')
    @patch('kea_exporter.cli.time.sleep')
    def test_cli_interval_parameter(self, mock_sleep, mock_http_server, mock_exporter):
        """Test CLI interval parameter"""
        mock_sleep.side_effect = KeyboardInterrupt
        mock_exporter.return_value.targets = [Mock()]
        mock_http_server.return_value = (Mock(), None)

        from click.testing import CliRunner
        runner = CliRunner()

        result = runner.invoke(cli, ['--interval', '5', 'http://localhost:8000'])

        # Interval should be used in the timer logic (tested in integration)
        self.assertEqual(result.exit_code, 0)

    @patch('kea_exporter.cli.Exporter')
    @patch('kea_exporter.cli.start_http_server')
    @patch('kea_exporter.cli.time.sleep')
    def test_cli_client_cert_options(self, mock_sleep, mock_http_server, mock_exporter):
        """Test CLI with client certificate options"""
        mock_sleep.side_effect = KeyboardInterrupt
        mock_exporter.return_value.targets = [Mock()]
        mock_http_server.return_value = (Mock(), None)

        from click.testing import CliRunner
        runner = CliRunner()

        # Create temporary files for testing
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False) as cert_file, \
             tempfile.NamedTemporaryFile(delete=False) as key_file:
            cert_path = cert_file.name
            key_path = key_file.name

            result = runner.invoke(cli, [
                '--client-cert', cert_path,
                '--client-key', key_path,
                'https://localhost:8000'
            ])

            # Check that cert and key were passed to Exporter
            mock_exporter.assert_called_once()
            call_kwargs = mock_exporter.call_args[1]
            self.assertEqual(call_kwargs['client_cert'], cert_path)
            self.assertEqual(call_kwargs['client_key'], key_path)

            # Cleanup
            import os
            os.unlink(cert_path)
            os.unlink(key_path)


class TestCLIWSGIApp(unittest.TestCase):
    """Test WSGI app behavior"""

    @patch('kea_exporter.cli.Exporter')
    @patch('kea_exporter.cli.start_http_server')
    @patch('kea_exporter.cli.make_wsgi_app')
    @patch('kea_exporter.cli.time.sleep')
    def test_wsgi_app_updates_on_interval(self, mock_sleep, mock_wsgi, mock_http_server, mock_exporter):
        """Test that WSGI app calls update based on interval"""
        mock_sleep.side_effect = KeyboardInterrupt

        mock_exporter_instance = Mock()
        mock_exporter_instance.targets = [Mock()]
        mock_exporter.return_value = mock_exporter_instance

        mock_httpd = Mock()
        mock_http_server.return_value = (mock_httpd, None)

        # Mock the WSGI app
        mock_wsgi_func = Mock(return_value=[b"metrics"])
        mock_wsgi.return_value = mock_wsgi_func

        from click.testing import CliRunner
        runner = CliRunner()

        result = runner.invoke(cli, ['--interval', '0', 'http://localhost:8000'])

        # Check that set_app was called
        mock_httpd.set_app.assert_called_once()

        # Get the local_wsgi_app function that was set
        wsgi_app = mock_httpd.set_app.call_args[0][0]

        # Call the app to test interval logic
        environ = {}
        start_response = Mock()

        # First call should trigger update (elapsed >= interval)
        result1 = wsgi_app(environ, start_response)
        # Second call should also trigger with interval=0
        result2 = wsgi_app(environ, start_response)

        # With interval=0, update should be called every time
        self.assertGreaterEqual(mock_exporter_instance.update.call_count, 1)


class TestCLIEnvironmentVariables(unittest.TestCase):
    """Test CLI environment variable support"""

    @patch('kea_exporter.cli.Exporter')
    @patch('kea_exporter.cli.start_http_server')
    @patch('kea_exporter.cli.time.sleep')
    def test_cli_port_from_env(self, mock_sleep, mock_http_server, mock_exporter):
        """Test PORT environment variable"""
        mock_sleep.side_effect = KeyboardInterrupt
        mock_exporter.return_value.targets = [Mock()]
        mock_http_server.return_value = (Mock(), None)

        from click.testing import CliRunner
        runner = CliRunner()

        result = runner.invoke(cli, ['http://localhost:8000'], env={'PORT': '9999'})

        mock_http_server.assert_called_once()
        call_args = mock_http_server.call_args[0]
        self.assertEqual(call_args[0], 9999)

    @patch('kea_exporter.cli.Exporter')
    @patch('kea_exporter.cli.start_http_server')
    @patch('kea_exporter.cli.time.sleep')
    def test_cli_address_from_env(self, mock_sleep, mock_http_server, mock_exporter):
        """Test ADDRESS environment variable"""
        mock_sleep.side_effect = KeyboardInterrupt
        mock_exporter.return_value.targets = [Mock()]
        mock_http_server.return_value = (Mock(), None)

        from click.testing import CliRunner
        runner = CliRunner()

        result = runner.invoke(cli, ['http://localhost:8000'], env={'ADDRESS': '192.168.1.1'})

        mock_http_server.assert_called_once()
        call_args = mock_http_server.call_args[0]
        self.assertEqual(call_args[1], "192.168.1.1")

    @patch('kea_exporter.cli.Exporter')
    @patch('kea_exporter.cli.start_http_server')
    @patch('kea_exporter.cli.time.sleep')
    def test_cli_timeout_from_env(self, mock_sleep, mock_http_server, mock_exporter):
        """Test TIMEOUT environment variable"""
        mock_sleep.side_effect = KeyboardInterrupt
        mock_exporter.return_value.targets = [Mock()]
        mock_http_server.return_value = (Mock(), None)

        from click.testing import CliRunner
        runner = CliRunner()

        result = runner.invoke(cli, ['http://localhost:8000'], env={'TIMEOUT': '25'})

        mock_exporter.assert_called_once()
        call_kwargs = mock_exporter.call_args[1]
        self.assertEqual(call_kwargs['timeout'], 25)

    @patch('kea_exporter.cli.Exporter')
    @patch('kea_exporter.cli.start_http_server')
    @patch('kea_exporter.cli.time.sleep')
    def test_cli_targets_from_env(self, mock_sleep, mock_http_server, mock_exporter):
        """Test TARGETS environment variable"""
        mock_sleep.side_effect = KeyboardInterrupt
        mock_exporter.return_value.targets = [Mock()]
        mock_http_server.return_value = (Mock(), None)

        from click.testing import CliRunner
        runner = CliRunner()

        result = runner.invoke(cli, [], env={'TARGETS': 'http://server1:8000 http://server2:8000'})

        mock_exporter.assert_called_once()
        call_kwargs = mock_exporter.call_args[1]
        # Click parses space-separated targets from env
        targets = call_kwargs['targets']
        self.assertGreater(len(targets), 0)


if __name__ == '__main__':
    unittest.main()