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
    stage = "uv" if arguments.tool == "uv" else "base"
    declarations = re.findall(
        rf"^[ \t]*FROM[ \t]+[^\n]+[ \t]+AS[ \t]+{stage}[ \t]*$",
        content,
        re.MULTILINE | re.IGNORECASE,
    )
    version = re.fullmatch(patterns[arguments.tool], declarations[0]) if len(declarations) == 1 else None
    if version is None:
        parser.error(f"Expected one pinned {arguments.tool} stage in {arguments.dockerfile}")
    print(version.group(1))


if __name__ == "__main__":
    main()
