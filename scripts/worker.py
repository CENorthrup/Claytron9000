#!/usr/bin/env python3
"""Safe GitHub worker operations for non-default branches and pull requests."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from claytron.github import GitHubClient, GitHubError, read_json, repository_name  # noqa: E402
from claytron.storage import validate_config  # noqa: E402

WORKER_PERMISSIONS = {"metadata": "read", "contents": "write", "pull_requests": "write"}


def refuse(operation: str):
    raise GitHubError(f"Worker refuses {operation}; human merge authority and default-branch protection are mandatory.")


def default_branch(client: GitHubClient, repository: str, token: str) -> str:
    value = client.repository(repository, token).get("default_branch")
    if not isinstance(value, str) or not value:
        raise GitHubError("GitHub did not return the repository default branch.")
    return value


def assert_safe_branch(branch: str, default: str):
    if branch == default:
        raise GitHubError("Worker refuses to operate on the repository default branch.")
    if not branch or branch.startswith("-"):
        raise GitHubError("Worker requires a nonempty non-option branch name.")


def create_or_update_pr(client, repository, token, head, base, title, body, number=None):
    actual_default = default_branch(client, repository, token)
    if base == actual_default:
        # PRs may target the default branch; only direct writes are forbidden.
        pass
    assert_safe_branch(head, actual_default)
    if number is None:
        return client.request("POST", f"/repos/{repository}/pulls", token,
                              {"title": title, "head": head, "base": base, "body": body})
    return client.request("PATCH", f"/repos/{repository}/pulls/{number}", token,
                          {"title": title, "body": body, "base": base})


def push_branch(client, repository, token, branch=None):
    actual_default = default_branch(client, repository, token)
    if branch is None:
        result = subprocess.run(["git", "branch", "--show-current"], check=True, capture_output=True, text=True)
        branch = result.stdout.strip()
    assert_safe_branch(branch, actual_default)
    environment = os.environ.copy()
    # Git reads this ephemeral config from the environment; the token is never a
    # command-line argument, file, repository config value, or persisted secret.
    environment.update({
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
        "GIT_CONFIG_VALUE_0": "Authorization: Bearer " + token,
    })
    try:
        subprocess.run(["git", "push", "origin", branch], check=True, env=environment)
    except (OSError, subprocess.CalledProcessError):
        raise GitHubError("Worker push failed; inspect the remote and branch state without retrying blindly.") from None
    return branch


def commit_changes(client, repository, token, message):
    actual_default = default_branch(client, repository, token)
    current = subprocess.run(["git", "branch", "--show-current"], check=True, capture_output=True, text=True).stdout.strip()
    assert_safe_branch(current, actual_default)
    try:
        subprocess.run(["git", "add", "-A"], check=True)
        subprocess.run(["git", "commit", "-m", message], check=True)
    except (OSError, subprocess.CalledProcessError):
        raise GitHubError("Worker commit failed; inspect the working tree before retrying.") from None
    return current


def create_branch(client, repository, token, branch):
    actual_default = default_branch(client, repository, token)
    assert_safe_branch(branch, actual_default)
    try:
        subprocess.run(["git", "switch", "-c", branch], check=True)
    except (OSError, subprocess.CalledProcessError):
        raise GitHubError("Worker branch creation failed; inspect the working tree before retrying.") from None
    return branch


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--repo", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check-branch")
    check.add_argument("--branch", required=True)
    branch = sub.add_parser("create-branch")
    branch.add_argument("--branch", required=True)
    commit = sub.add_parser("commit")
    commit.add_argument("--message", required=True)
    create = sub.add_parser("create-pr")
    create.add_argument("--head", required=True)
    create.add_argument("--base", required=True)
    create.add_argument("--title", required=True)
    create.add_argument("--body", required=True)
    create.add_argument("--number", type=int)
    push = sub.add_parser("push")
    push.add_argument("--branch")
    sub.add_parser("merge")
    sub.add_parser("push-default")
    args = parser.parse_args(argv)
    try:
        repository_name(args.repo)
        if args.command in ("merge", "push-default"):
            refuse(args.command)
        config = read_json(args.config)
        validate_config(config, "worker")
        if config.get("permissions") != WORKER_PERMISSIONS:
            raise GitHubError("Worker App permissions must be Metadata read, Contents write and Pull requests write only.")
        client = GitHubClient(config, "worker")
        token = client.token(args.repo)
        if args.command == "check-branch":
            actual_default = default_branch(client, args.repo, token)
            assert_safe_branch(args.branch, actual_default)
            print("worker branch is safe")
        elif args.command == "create-branch":
            print("worker created " + create_branch(client, args.repo, token, args.branch))
        elif args.command == "commit":
            print("worker committed on " + commit_changes(client, args.repo, token, args.message))
        elif args.command == "push":
            print("worker pushed " + push_branch(client, args.repo, token, args.branch))
        else:
            result = create_or_update_pr(client, args.repo, token, args.head, args.base,
                                         args.title, args.body, args.number)
            print(json.dumps({"number": result.get("number"), "url": result.get("html_url")}, sort_keys=True))
        return 0
    except GitHubError as exc:
        print("claytron worker: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
