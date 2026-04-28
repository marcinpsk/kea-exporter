|checks| |license|

.. |checks| image:: https://github.com/marcinpsk/kea-exporter/actions/workflows/checks.yml/badge.svg
   :alt: Lint & Test
   :target: https://github.com/marcinpsk/kea-exporter/actions/workflows/checks.yml

.. |license| image:: https://img.shields.io/github/license/marcinpsk/kea-exporter
   :alt: GitHub license
   :target: https://github.com/marcinpsk/kea-exporter/blob/main/LICENSE

kea-exporter
============

Prometheus Exporter for the ISC Kea DHCP Server.

Fork
============
This project is a fork of the original repository:

Upstream: https://github.com/mweinelt/kea-exporter

This fork exists to provide fixes/updates that are not present on upstream.
If they show up on upstream, I'll revisit whether to keep this one.

Key differences from upstream:

- Tested against **Kea 3.0** without Control Agent (direct HTTP API only)
- Support for subnets inside **shared-networks** (not just top-level ``subnet4``/``subnet6``)
- DDNS metrics support
- Per-server metric deduplication (multi-target setups)
- Failed target retry on each scrape cycle (up to 10 attempts)
- Configurable HTTP request timeout (``--timeout``)
- Configurable stale-label timeout for unreachable servers (``--stale-timeout``)
- TLS verification control: custom CA bundle (``--ca-bundle``) or disable verification (``--no-tls-verify``)

.. note::

   There is **no PyPI package** for this fork. The ``pip install kea-exporter``
   command installs the **upstream** package from mweinelt. To install this fork,
   use Docker (recommended) or install directly from the Git repository.

Installation
------------

Docker (Recommended)
/////////////////////

Docker images are published to GitHub Container Registry:

::

    $ docker pull ghcr.io/marcinpsk/kea-exporter
    $ docker pull ghcr.io/marcinpsk/kea-exporter:latest-debian
    $ docker pull ghcr.io/marcinpsk/kea-exporter:latest-alpine

Run with environment variables:

::

    $ docker run -d \
        -p 9547:9547 \
        -e TARGETS="http://kea-server:8000" \
        -e TIMEOUT=30 \
        ghcr.io/marcinpsk/kea-exporter

Or with explicit arguments:

::

    $ docker run -d \
        -p 9547:9547 \
        ghcr.io/marcinpsk/kea-exporter \
        --timeout 30 \
        http://kea-server:8000

From Git Repository
////////////////////

::

    $ pip install git+https://github.com/marcinpsk/kea-exporter.git

PyPI (Upstream Only)
/////////////////////

.. image:: https://repology.org/badge/vertical-allrepos/kea-exporter.svg
   :alt: Package versions via repology.org
   :target: https://repology.org/project/kea-exporter/versions

The PyPI package installs the **upstream** version (mweinelt/kea-exporter),
not this fork:

::

    $ pip install kea-exporter


Features
--------

- DHCP4 & DHCP6 Metrics (tested against Kea 3.0, should work with 2.4+)
- DDNS Metrics
- Subnets inside shared-networks
- Configuration and statistics via HTTP/HTTPS API or Unix domain socket
- Multiple Kea targets with per-server labels
- Automatic retry of failed targets on each scrape (up to 10 attempts)
- Configurable HTTP request timeout
- Client certificate (mTLS) support
- Configurable stale-label timeout — auto-remove metrics for unreachable servers (``--stale-timeout``)
- TLS certificate verification: custom CA bundle (``--ca-bundle``) or disable verification (``--no-tls-verify``)

Testing Status
//////////////

This fork is actively tested against **Kea 3.0** using **HTTP/HTTPS only**
(no Control Agent). Unix domain socket support is maintained but not currently
tested — it should still work but is not guaranteed.


Known Limitations
-----------------

The following features are not supported yet, help is welcome.

- Custom Subnet Identifiers
- Automatic config reload (through inotify)

Usage
-----

Pass one or multiple Kea HTTP API endpoints — either a Control-Agent URL or a
direct DHCP daemon HTTP endpoint (Control Agent is optional for Kea 2.7.2+) —
or Unix Domain Socket paths to the ``kea-exporter`` executable. All other
options are optional.

::

	Usage: kea-exporter [OPTIONS] TARGETS...

	Options:
	  -a, --address TEXT             Address that the exporter binds to.
	  -p, --port INTEGER             Port that the exporter binds to.
	  -i, --interval INTEGER         Minimal interval between two queries to Kea in
	                                 seconds.
	  --client-cert PATH             Path to client certificate used in HTTP requests
	  --client-key PATH              Path to client key used in HTTP requests
	  --timeout INTEGER RANGE        Timeout for HTTP requests in seconds.  [x>=1]
	  --stale-timeout INTEGER RANGE  Remove metrics for a server that has not
	                                 responded for this many seconds. 0 disables
	                                 the timeout (default).  [x>=0]
	  --no-tls-verify                Disable TLS certificate verification for
	                                 HTTPS targets (insecure).
	  --ca-bundle PATH               Path to a CA bundle file for TLS certificate
	                                 verification.
	  --version                      Show the version and exit.
	  --help                         Show this message and exit.

You can also configure the exporter using environment variables:

::

   export ADDRESS="0.0.0.0"
   export PORT="9547"
   export INTERVAL="7"
   export TIMEOUT="30"
   export TARGETS="http://router.example.com:8000"
   export CLIENT_CERT="/etc/kea-exporter/client.crt"
   export CLIENT_KEY="/etc/kea-exporter/client.key"
   export STALE_TIMEOUT="300"
   export CA_BUNDLE="/etc/ssl/certs/my-ca.pem"
   # export TLS_NO_VERIFY="1"  # insecure — disables TLS verification


Configure Kea HTTP API
////////////////////////

The exporter uses Kea's HTTP API (or legacy control socket) to request both
configuration and statistics. For Kea 2.7.2+ the Control Agent is optional —
the exporter can discover services directly from the DHCP daemon config.

Consult the Kea documentation on how to set up the management API:

- https://kea.readthedocs.io/en/latest/arm/dhcp4-srv.html#management-api-for-the-dhcpv4-server
- https://kea.readthedocs.io/en/latest/arm/dhcp6-srv.html#management-api-for-the-dhcpv6-server

HTTPS / TLS
///////////

TLS certificate verification is enabled by default for all HTTPS targets.

To use a **custom CA certificate bundle** (e.g. for internal PKI or self-signed
certificates)::

    $ kea-exporter --ca-bundle /path/to/ca-bundle.pem https://kea-server:8443
    $ export CA_BUNDLE=/path/to/ca-bundle.pem

To **disable TLS verification entirely** (development or testing only — insecure)::

    $ kea-exporter --no-tls-verify https://kea-server:8443
    $ export TLS_NO_VERIFY=1

Stale-Label Timeout
///////////////////

By default, metrics for an unreachable server are kept indefinitely until the
server starts responding again. Use ``--stale-timeout`` to automatically remove
them after a given number of seconds::

    $ kea-exporter --stale-timeout 300 http://kea-server:8000
    $ export STALE_TIMEOUT=300

A value of ``0`` (the default) disables the timeout.

Permissions
///////////

When using Unix domain sockets, Kea Exporter needs to be able to read and
write on the socket, hence its permissions might need to be modified accordingly.

Development
-----------

An ``.envrc.example`` file is provided for `direnv <https://direnv.net/>`_
users to simplify the development setup:

::

    $ cp .envrc.example .envrc
    $ direnv allow

.. warning::

   **Always review the contents of any ``.envrc`` file before enabling it.**
   The file modifies your shell environment (PATH, VIRTUAL_ENV). The example
   sets up a Python virtualenv via ``uv`` if available. Inspect the file to
   ensure it does nothing unexpected in your environment.

Grafana-Dashboard
/////////////////

A dashboard for this exporter is available at https://grafana.com/grafana/dashboards/12688.
