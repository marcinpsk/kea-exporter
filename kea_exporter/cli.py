import sys
import time
from typing import Any

import click
from prometheus_client import REGISTRY, make_wsgi_app, start_http_server

from kea_exporter import __project__, __version__
from kea_exporter.exporter import Exporter


class Timer:
    def __init__(self):
        self.reset()

    def reset(self):
        self.start_time = time.time()

    def time_elapsed(self):
        now_time = time.time()
        return now_time - self.start_time


@click.command()
@click.option(
    "-a",
    "--address",
    envvar="ADDRESS",
    type=str,
    default="0.0.0.0",
    help="Address that the exporter binds to.",
)
@click.option(
    "-p",
    "--port",
    envvar="PORT",
    type=int,
    default=9547,
    help="Port that the exporter binds to.",
)
@click.option(
    "-i",
    "--interval",
    envvar="INTERVAL",
    type=int,
    default=0,
    help="Minimal interval between two queries to Kea in seconds.",
)
@click.option(
    "--client-cert",
    envvar="CLIENT_CERT",
    type=click.Path(exists=True),
    help="Path to client certificate used to in HTTP requests",
    required=False,
)
@click.option(
    "--client-key",
    envvar="CLIENT_KEY",
    type=click.Path(exists=True),
    help="Path to client key used in HTTP requests",
    required=False,
)
@click.option(
    "--timeout",
    envvar="TIMEOUT",
    type=click.IntRange(min=1),
    default=10,
    help="Timeout for HTTP requests in seconds.",
)
@click.argument("targets", envvar="TARGETS", nargs=-1, required=True)
@click.version_option(prog_name=__project__, version=__version__)
def cli(port, address, interval, **kwargs: Any):
    """
    Start the Kea exporter, expose Prometheus metrics over HTTP, and run
    the main loop.

    Instantiates the Exporter from provided keyword arguments, verifies
    targets are configured, starts a Prometheus HTTP server bound to the
    given address and port, installs a WSGI app that triggers exporter
    updates at most once per `interval` seconds, prints the listening
    address, and blocks indefinitely to keep the server running.

    Parameters:
        port (int): TCP port to bind the Prometheus HTTP server.
        address (str): IP address or hostname to bind the Prometheus
            HTTP server.
        interval (int): Minimum number of seconds between consecutive
            exporter updates.
        **kwargs: Passed through to Exporter constructor (for example:
            targets, client_cert, client_key, timeout).
    """
    exporter = Exporter(**kwargs)

    if not exporter.targets:
        sys.exit(1)

    httpd, _ = start_http_server(port, address)

    t = Timer()

    def local_wsgi_app(registry):
        func = make_wsgi_app(registry, False)

        def app(environ, start_response):
            if t.time_elapsed() >= interval:
                exporter.update()
                t.reset()
            output_array = func(environ, start_response)
            return output_array

        return app

    httpd.set_app(local_wsgi_app(REGISTRY))

    click.echo(f"Listening on http://{address}:{port}")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        try:
            httpd.shutdown()
            httpd.server_close()
        except Exception as e:
            click.echo(f"Error during shutdown: {e}", err=True)
        sys.exit(0)


if __name__ == "__main__":
    cli()
