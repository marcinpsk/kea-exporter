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
python -m unittest discover tests
```

### Run a specific test file
```bash
python -m unittest tests.test_http
```

### Run with verbose output
```bash
python -m unittest discover tests -v
```

## New Features Tested

The tests extensively cover the new features introduced in this branch:

1. **DDNS Support** (DHCPVersion.DDNS)
2. **HTTP Basic Authentication**
3. **Timeout Parameter**
4. **Server Labeling**

Tests use Python's built-in `unittest` framework.