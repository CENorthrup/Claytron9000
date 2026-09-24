#!/usr/bin/env python3
"""Claytron9000 command entry point."""

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # Forward nested command options without making the top-level parser
    # reinterpret role-specific flags such as --role and --repository.
    if argv[:2] == ["setup", "github"]:
        return subprocess.call([sys.executable, str(ROOT / "scripts/setup-github.py"), *argv[2:]])
    if argv[:2] == ["github", "repository"]:
        return subprocess.call([sys.executable, str(ROOT / "scripts/manage-installation.py"), *argv[2:]])
    parser = argparse.ArgumentParser(prog="claytron")
    subparsers = parser.add_subparsers(dest="command", required=True)
    setup = subparsers.add_parser("setup")
    setup_sub = setup.add_subparsers(dest="setup_command", required=True)
    github = setup_sub.add_parser("github", help="create and install a Claytron GitHub App")
    github.add_argument("args", nargs=argparse.REMAINDER)
    installation = subparsers.add_parser("github")
    installation_sub = installation.add_subparsers(dest="github_command", required=True)
    manage = installation_sub.add_parser("repository")
    manage.add_argument("args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.command == "setup" and args.setup_command == "github":
        return subprocess.call([sys.executable, str(ROOT / "scripts/setup-github.py"), *args.args])
    if args.command == "github" and args.github_command == "repository":
        return subprocess.call([sys.executable, str(ROOT / "scripts/manage-installation.py"), *args.args])
    parser.error("unsupported command")


if __name__ == "__main__":
    raise SystemExit(main())
