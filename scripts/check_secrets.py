"""Reject private runtime files and recognizable credentials among tracked files."""

import re
import subprocess
from pathlib import Path

SIGNATURES = (
    re.compile(rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
    re.compile(rb"\b[0-9]{6,16}:[A-Za-z0-9_-]{32,}\b"),
    re.compile(rb"\b(?:github_pat_[A-Za-z0-9_]{35,}|gh[pousr]_[A-Za-z0-9]{30,})\b"),
    re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
)


def main():
    names = subprocess.check_output(("git", "ls-files", "-z")).decode("utf-8").split("\0")
    violations = []
    for name in filter(None, names):
        path = Path(name)
        private_name = (
            (path.name.startswith(".env") and path.name != ".env.example")
            or path.name == "hosts.toml"
            or path.suffix in {".db", ".pem", ".key"}
            or bool({"secrets", "data"}.intersection(path.parts))
        )
        if (
            path.is_symlink()
            or private_name
            or any(pattern.search(path.read_bytes()) for pattern in SIGNATURES)
        ):
            violations.append(name)
    for name in violations:
        print(f"Potential credential in tracked path: {name}")
    return int(bool(violations))


if __name__ == "__main__":
    raise SystemExit(main())
