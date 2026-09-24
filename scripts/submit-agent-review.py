#!/usr/bin/env python3
"""Submit a commit-bound review using only the reviewer App identity."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from claytron.github import GitHubError, app_jwt as _app_jwt, read_json, repository_name  # noqa: E402
from claytron.storage import validate_config  # noqa: E402


DEFAULT_REPOSITORY = "CENorthrup/devenv"
REPOSITORY = DEFAULT_REPOSITORY  # compatibility; runtime scope comes from configuration.
REVIEWER_PERMISSIONS = {"metadata": "read", "pull_requests": "write"}


class ReviewError(GitHubError):
    """Safe, deliberately credential-free diagnostic."""


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ReviewError("Invalid or missing arguments; see --help.")


def app_jwt(config):
    try:
        return _app_jwt(config)
    except GitHubError as exc:
        raise ReviewError(str(exc)) from None


def arguments(argv):
    parser = Parser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path.home() / ".config/claytron9000/github-apps/reviewer/config.json")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument("--event", required=True, choices=["COMMENT", "APPROVE", "REQUEST_CHANGES"])
    parser.add_argument("--body-file", required=True, type=Path)
    parser.add_argument("--comments-file", type=Path, help="JSON array of inline findings")
    for name in ("model", "effort", "harness", "head-sha"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args(argv)
    repository_name(args.repo)
    if args.pr <= 0 or not re.fullmatch(r"[0-9a-fA-F]{40}", args.head_sha):
        raise ReviewError("A positive PR number and full 40-character reviewed SHA are required.")
    args.head_sha = args.head_sha.lower()
    for value in (args.model, args.effort, args.harness):
        if not value.strip() or len(value) > 200 or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ReviewError("Model, effort and harness must be nonempty single-line values.")
    return args


def configuration(path):
    config = read_json(path)
    try:
        validate_config(config, "reviewer")
    except GitHubError as exc:
        raise ReviewError(str(exc)) from None
    if not isinstance(config.get("repositories"), list) or not config["repositories"]:
        raise ReviewError("Reviewer configuration requires selected repositories.")
    if not all(isinstance(repo, str) for repo in config["repositories"]):
        raise ReviewError("Reviewer selected repositories are invalid.")
    if config.get("permissions") != REVIEWER_PERMISSIONS:
        raise ReviewError("Reviewer App permissions must be Metadata read and Pull requests write only.")
    return config


def review_payload(args):
    try:
        body = args.body_file.read_text()
    except (OSError, ValueError):
        raise ReviewError("Cannot read the review body file.") from None
    if not body.strip():
        raise ReviewError("Review body must not be empty.")
    provenance = ("Agent review provenance\n\nRole: reviewer\n"
                  f"Model: {args.model}\nEffort: {args.effort}\nHarness: {args.harness}\n"
                  f"Reviewed commit: {args.head_sha}")
    payload = {"event": args.event, "commit_id": args.head_sha,
               "body": body.rstrip() + "\n\n---\n\n" + provenance + "\n"}
    if args.comments_file:
        try:
            comments = json.loads(args.comments_file.read_text())
        except (OSError, ValueError):
            raise ReviewError("Cannot read valid inline comments JSON.") from None
        if not isinstance(comments, list):
            raise ReviewError("Inline comments must be a JSON array.")
        for comment in comments:
            if not isinstance(comment, dict) or set(comment) - {"path", "line", "side", "body", "start_line", "start_side"}:
                raise ReviewError("Unsupported inline comment fields.")
            if (not all(isinstance(comment.get(key), str) and comment[key].strip() for key in ("path", "body"))
                    or type(comment.get("line")) is not int or comment["line"] <= 0
                    or comment.get("side") not in ("LEFT", "RIGHT")):
                raise ReviewError("Inline findings require path, body, positive line and LEFT/RIGHT side.")
            if "start_line" in comment or "start_side" in comment:
                if (type(comment.get("start_line")) is not int or comment["start_line"] <= 0
                        or comment.get("start_side") not in ("LEFT", "RIGHT")):
                    raise ReviewError("Multiline findings require positive start_line and LEFT/RIGHT start_side.")
        payload["comments"] = comments
    return payload


def submit(args, request, sign=app_jwt):
    payload = review_payload(args)
    config = configuration(args.config)
    if args.repo not in config["repositories"]:
        raise ReviewError("Repository is not in the reviewer's configured selected-repository scope.")
    jwt = sign(config)
    app = request("GET", "/app", jwt)
    if app.get("slug") != config["app_slug"] or str(app.get("id")) != str(config["app_id"]):
        raise ReviewError("App identity does not match the configured reviewer App.")
    installation = request("GET", f"/repos/{args.repo}/installation", jwt)
    if (str(installation.get("id")) != str(config.get("installation_id"))
            or installation.get("repository_selection") != "selected"
            or installation.get("account", {}).get("login") != config["account_login"]):
        raise ReviewError("Reviewer installation does not match configured account, ID or selected scope.")
    if installation.get("permissions") != config["permissions"]:
        raise ReviewError("Reviewer App permissions must be Metadata read and Pull requests write only.")
    repo_name = args.repo.split("/", 1)[1]
    minted = request("POST", f"/app/installations/{config['installation_id']}/access_tokens", jwt,
                     {"repositories": [repo_name], "permissions": config["permissions"]})
    token = minted.get("token")
    if not isinstance(token, str) or not token:
        raise ReviewError("Unable to obtain a short-lived reviewer installation token.")
    repositories = request("GET", "/installation/repositories?per_page=100", token)
    visible = [item.get("full_name") for item in repositories.get("repositories", [])]
    if repositories.get("total_count") != 1 or visible != [args.repo]:
        raise ReviewError("Reviewer installation token is not scoped solely to the requested repository.")
    pr = request("GET", f"/repos/{args.repo}/pulls/{args.pr}", token)
    if pr.get("head", {}).get("sha") != args.head_sha:
        raise ReviewError("PR head changed; review the new commit before submitting.")
    result = request("POST", f"/repos/{args.repo}/pulls/{args.pr}/reviews", token, payload)
    expected_state = {"COMMENT": "COMMENTED", "APPROVE": "APPROVED", "REQUEST_CHANGES": "CHANGES_REQUESTED"}[args.event]
    if (result.get("user", {}).get("login") != config["app_slug"] + "[bot]"
            or result.get("commit_id") != args.head_sha or result.get("state") != expected_state):
        raise ReviewError("Review response identity, commit or state was unexpected; inspect GitHub before retrying.")
    if type(result.get("id")) is not int or result["id"] <= 0:
        raise ReviewError("Review response lacked a valid ID; inspect GitHub before retrying.")
    return f"https://github.com/{args.repo}/pull/{args.pr}#pullrequestreview-{result['id']}"


def api(method, path, credential, payload=None):
    from claytron.github import GitHubClient
    return GitHubClient({}, "reviewer")._request(method, path, credential, payload)


def main(argv=None):
    try:
        print(submit(arguments(argv), request=api))
        return 0
    except ReviewError as exc:
        print("agent review: " + str(exc), file=sys.stderr)
    except Exception:
        print("agent review: unexpected failure; inspect GitHub before retrying. No credentials logged.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
