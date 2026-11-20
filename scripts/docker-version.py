"""Read a tool version from its Docker build stage."""

import argparse
import re
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tool", choices=("uv", "python"))
    parser.add_argument("dockerfile", type=Path)
    arguments = parser.parse_args()
    patterns = {
        "uv": r"^FROM ghcr\.io/astral-sh/uv:(\d+\.\d+\.\d+) AS uv$",
        "python": r"^FROM python:(\d+\.\d+(?:\.\d+)?)-\S+ AS base$",
    }
    try:
        content = arguments.dockerfile.read_text()
    except OSError as error:
        parser.error(str(error))
    versions = re.findall(patterns[arguments.tool], content, re.MULTILINE)
    if len(versions) != 1:
        parser.error(f"Expected one pinned {arguments.tool} stage in {arguments.dockerfile}")
    print(versions[0])


if __name__ == "__main__":
    main()
