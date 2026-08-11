"""Credentials embedded in a target must not reach the log.

These drive the real Exporter constructor: mutual TLS configured with only a
certificate raises before any network call, so the initialisation-failure path
runs without a Kea server.
"""

import pytest
from prometheus_client import CollectorRegistry

from kea_exporter.exporter import Exporter

# Raises ValueError inside KeaHTTPClient before it opens a connection.
CERT_WITHOUT_KEY = {"client_cert": "/nonexistent/cert.pem"}


@pytest.fixture
def registry():
    return CollectorRegistry()


@pytest.mark.parametrize(
    "target",
    [
        "http://user:s3cret@kea.local:8000",
        # urlparse reports an empty username here, which is falsy.
        "http://:s3cret@kea.local:8000",
    ],
)
def test_password_never_reaches_the_initialisation_failure_message(registry, capsys, target):
    Exporter(targets=[target], registry=registry, **CERT_WITHOUT_KEY)

    out = capsys.readouterr().out
    assert "Failed to initialize target" in out, out
    assert "s3cret" not in out, out
    assert "http://kea.local:8000" in out, out
