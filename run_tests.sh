#!/bin/bash
set -e
echo "Running Kea Exporter Unit Tests"
python -m unittest discover tests -v
echo "All tests completed successfully!"