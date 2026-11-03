# Kea Exporter Tests

This directory contains comprehensive unit tests for the kea-exporter project.

## Test Coverage

The test suite covers the following modules:

- `test_init.py`: Tests for the main module, including the new DDNS enum value
- `test_http.py`: Tests for HTTP client including:
  - Basic authentication via URL credentials
  - Timeout parameter
  - DDNS module detection
  - Server ID labeling (credentials stripped from labels)
- `test_uds.py`: Tests for Unix domain socket client including server ID
- `test_exporter.py`: Tests for the exporter including:
  - DDNS metrics setup
  - Server labeling feature
  - Multi-server support
  - Per-key DDNS metrics
- `test_cli.py`: Tests for CLI including timeout parameter

## Running the Tests

### Run all tests

```bash
uv run pytest
```

### Run a specific test file

```bash
uv run pytest tests/test_http.py
```

### Run with verbose output

```bash
uv run pytest -v
```

### Run with coverage

```bash
uv run pytest --cov --cov-report=term-missing --cov-fail-under=80
```

### Test a Docker image

The Docker smoke tests require a Linux Docker host. They check the installed
packages, CLI, and a real Kea-to-Prometheus scrape with a read-only filesystem.
CI runs these tests for both images before publication.

```bash
docker build -f Dockerfile.debian -t kea-exporter-smoke:debian .
KEA_EXPORTER_TEST_IMAGE=kea-exporter-smoke:debian uv run -- pytest tests/test_docker.py
```

CI builds and tests each image on a standard native GitHub runner: `ubuntu-24.04`
for amd64 and `ubuntu-24.04-arm` for ARM64. Release tags combine the two image
digests only after all image tests pass.

Set `KEA_EXPORTER_TEST_REGISTRY` to a local test registry to run the publishing
integration test in `tests/test_ci.py`. The selected Buildx builder must reach
that registry. This test uploads temporary images and checks the release
manifest with real Docker commands.

## New Features Tested

The tests extensively cover the new features introduced in this branch:

1. **DDNS Support** (DHCPVersion.DDNS)
2. **HTTP Basic Authentication**
3. **Timeout Parameter**
4. **Server Labeling**

Tests run with `pytest` and include tests that use pytest APIs.
