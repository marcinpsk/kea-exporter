import signal
import sys
import threading
import time
from typing import Any

import click
from prometheus_client import REGISTRY, make_wsgi_app, start_http_server

from kea_exporter import __project__, __version__
from kea_exporter.exporter import Exporter


@click.command(context_settings={"show_default": True})
@click.option(
    "-a",
    "--address",
    envvar="ADDRESS",
    type=str,
    default="0.0.0.0",
    help="Address to listen on.",
)
@click.option(
    "-p",
    "--port",
    envvar="PORT",
    type=int,
    default=9547,
    help="Port to listen on.",
)
@click.option(
    "-i",
    "--interval",
    envvar="INTERVAL",
    type=int,
    default=0,
    help="Minimum interval between two Kea queries, in seconds.",
)
@click.option(
    "-v",
    "--verbose",
    envvar="VERBOSE",
    is_flag=True,
    help="Write one summary to stderr after each Kea scrape.",
)
@click.option(
    "--client-cert",
    envvar="CLIENT_CERT",
    type=click.Path(exists=True),
    help="Path to the client certificate for HTTP requests.",
    required=False,
)
@click.option(
    "--client-key",
    envvar="CLIENT_KEY",
    type=click.Path(exists=True),
    help="Path to the client key for HTTP requests.",
    required=False,
)
@click.option(
    "--timeout",
    envvar="TIMEOUT",
    type=click.IntRange(min=1),
    default=10,
    help="Timeout for HTTP requests in seconds.",
)
@click.option(
    "--stale-timeout",
    envvar="STALE_TIMEOUT",
    type=click.IntRange(min=0),
    default=0,
    help="Remove metrics for a Kea source after this many seconds without a response. Set to 0 to disable.",
)
@click.option(
    "--no-tls-verify",
    "tls_no_verify",
    envvar="TLS_NO_VERIFY",
    is_flag=True,
    default=False,
    help="Disable TLS certificate verification for HTTPS targets (insecure).",
)
@click.option(
    "--ca-bundle",
    envvar="CA_BUNDLE",
    type=click.Path(exists=True),
    default=None,
    help="Path to a CA bundle file for TLS certificate verification.",
)
@click.argument("targets", envvar="TARGETS", nargs=-1, required=True)
@click.version_option(prog_name=__project__, version=__version__)
def cli(port, address, interval, verbose, **kwargs: Any):
    """Read Kea statistics and expose them as Prometheus metrics.

    TARGETS are Kea HTTP URLs or Unix socket paths.

    The exporter completes one scrape cycle at startup. It then serves
    Prometheus metrics over HTTP. Each request starts a scrape cycle unless
    the configured interval has not elapsed.
    """
    exporter = Exporter(**kwargs)

    if not exporter.targets:
        sys.exit(1)

    def collect():
        report = exporter.update()
        if verbose:
            click.echo(report.summary(), err=True)

    click.echo(f"Starting {__project__} {__version__}", err=True)
    collect()
    try:
        httpd, _ = start_http_server(port, address)
    except OSError as ex:
        raise click.ClickException(f"Cannot listen on http://{address}:{port}: {ex}") from ex

    last_update = time.monotonic()
    update_lock = threading.Lock()

    def local_wsgi_app(registry):
        func = make_wsgi_app(registry, False)

        def app(environ, start_response):
            nonlocal last_update
            with update_lock:
                if time.monotonic() - last_update >= interval:
                    collect()
                    last_update = time.monotonic()
            output_array = func(environ, start_response)
            return output_array

        return app

    httpd.set_app(local_wsgi_app(REGISTRY))

    click.echo(f"Listening on http://{address}:{port}")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        click.echo("Received signal, shutting down.", err=True)
        old_handler = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            httpd.shutdown()
            httpd.server_close()
        except Exception as e:
            click.echo(f"Error during shutdown: {e}", err=True)
        finally:
            signal.signal(signal.SIGINT, old_handler)
        sys.exit(0)


if __name__ == "__main__":
    cli()
