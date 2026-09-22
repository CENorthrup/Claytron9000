"""Submit a commit-bound review using only a GitHub App installation identity."""

import argparse
import base64
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request

REPOSITORY = "CENorthrup/devenv"


class ReviewError(Exception):
    """Safe, deliberately credential-free diagnostic."""


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse normally echoes invalid input, which may contain a secret.
        raise ReviewError("Invalid or missing arguments; see --help.")


def arguments(argv):
    parser = Parser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path.home() / ".config/claytron-reviewer/config.json")
    parser.add_argument("--repo", required=True, choices=[REPOSITORY])
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument("--event", required=True, choices=["COMMENT", "APPROVE", "REQUEST_CHANGES"])
    parser.add_argument("--body-file", required=True, type=Path)
    parser.add_argument("--comments-file", type=Path, help="JSON array of inline findings: path, line, side, body; optional start_line/start_side")
    for name in ("model", "effort", "harness", "head-sha"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args(argv)
    if args.pr <= 0 or not re.fullmatch(r"[0-9a-fA-F]{40}", args.head_sha):
        raise ReviewError("A positive PR number and full 40-character reviewed SHA are required.")
    args.head_sha = args.head_sha.lower()
    for value in (args.model, args.effort, args.harness):
        if not value.strip() or len(value) > 200 or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ReviewError("Model, effort and harness must be nonempty single-line values.")
    return args


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        raise ReviewError("Cannot read valid JSON from the configuration or comments file.") from None


def configuration(path):
    config = read_json(path)
    if not isinstance(config, dict):
        raise ReviewError("Configuration must be a JSON object.")
    for field in ("app_id", "installation_id"):
        if not re.fullmatch(r"[1-9][0-9]*", str(config.get(field, ""))):
            raise ReviewError("Configuration requires positive app_id and installation_id values.")
    # The reviewer App identity is supplied locally, so no App name is compiled in
    # and the same mechanism can pin a differently named App without a code change.
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?", str(config.get("app_slug", ""))):
        raise ReviewError("Configuration requires the registered App slug as app_slug.")
    if not isinstance(config.get("private_key_path"), str) or not Path(config["private_key_path"]).is_absolute():
        raise ReviewError("Configuration requires an absolute private_key_path.")
    return config


def encoded(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=")


def app_jwt(config):
    now = int(time.time())
    data = b".".join(encoded(json.dumps(v, separators=(",", ":")).encode()) for v in (
        {"alg": "RS256", "typ": "JWT"},
        {"iat": now - 60, "exp": now + 540, "iss": str(config["app_id"])},
    ))
    try:
        fd = os.open(config["private_key_path"], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as key:
            info = os.fstat(key.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ReviewError("Private key must be a regular file owned by this user with mode 600 or 400.")
            result = subprocess.run(
                ["openssl", "dgst", "-sha256", "-sign", f"/dev/fd/{key.fileno()}", "-passin", "pass:"],
                input=data, capture_output=True, pass_fds=(key.fileno(),), timeout=15,
            )
        if result.returncode:
            raise ReviewError("Unable to sign App JWT; check the RSA private key and OpenSSL installation.")
    except (OSError, subprocess.SubprocessError):
        raise ReviewError("Cannot read or sign with the private key; check its path, permissions and OpenSSL.") from None
    return (data + b"." + encoded(result.stdout)).decode()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def api(method, path, credential, payload=None):
    request = urllib.request.Request(
        "https://api.github.com" + path,
        data=None if payload is None else json.dumps(payload).encode(), method=method,
        headers={"Authorization": "Bearer " + credential, "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json", "X-GitHub-Api-Version": "2026-03-10",
                 "User-Agent": "claytron-reviewer"},
    )
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise ReviewError(f"GitHub API returned HTTP {exc.code}; check App installation, permissions and review inputs. No automatic retry was made.") from None
    except (OSError, ValueError):
        raise ReviewError("GitHub API transport or JSON failure. Verify GitHub state before retrying a review submission.") from None


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
    payload = {"event": args.event, "commit_id": args.head_sha, "body": body.rstrip() + "\n\n---\n\n" + provenance + "\n"}
    if args.comments_file:
        comments = read_json(args.comments_file)
        if not isinstance(comments, list):
            raise ReviewError("Inline comments must be a JSON array.")
        for comment in comments:
            if not isinstance(comment, dict) or set(comment) - {"path", "line", "side", "body", "start_line", "start_side"}:
                raise ReviewError("Unsupported inline comment fields.")
            if (not all(isinstance(comment.get(k), str) and comment[k].strip() for k in ("path", "body"))
                    or type(comment.get("line")) is not int or comment["line"] <= 0
                    or comment.get("side") not in ("LEFT", "RIGHT")):
                raise ReviewError("Inline findings require path, body, positive line and LEFT/RIGHT side.")
            if "start_line" in comment or "start_side" in comment:
                if (type(comment.get("start_line")) is not int or comment["start_line"] <= 0
                        or comment.get("start_side") not in ("LEFT", "RIGHT")):
                    raise ReviewError("Multiline findings require positive start_line and LEFT/RIGHT start_side.")
        payload["comments"] = comments
    return payload


def submit(args, request=api, sign=app_jwt):
    payload = review_payload(args)
    config = configuration(args.config)
    jwt = sign(config)
    app = request("GET", "/app", jwt)
    if app.get("slug") != config["app_slug"] or str(app.get("id")) != str(config["app_id"]):
        raise ReviewError("App identity does not match the configured app_slug and app_id.")
    installation = request("GET", f"/repos/{args.repo}/installation", jwt)
    if (str(installation.get("id")) != str(config["installation_id"])
            or installation.get("repository_selection") != "selected"
            or installation.get("account", {}).get("login") != "CENorthrup"):
        raise ReviewError("App installation does not match the configured account, ID or selected-repository scope.")
    permissions = installation.get("permissions", {})
    if permissions.get("pull_requests") != "write" or any(
            name not in ("metadata", "pull_requests") and level != "none" for name, level in permissions.items()):
        raise ReviewError("App permissions must be Metadata read and Pull requests write only.")
    minted = request("POST", f"/app/installations/{config['installation_id']}/access_tokens", jwt,
                     {"repositories": ["devenv"], "permissions": {"pull_requests": "write"}})
    token = minted.get("token")
    if not isinstance(token, str) or not token or minted.get("permissions", {}).get("pull_requests") != "write":
        raise ReviewError("Unable to obtain an installation token with review permission.")
    repositories = request("GET", "/installation/repositories?per_page=100", token)
    if repositories.get("total_count") != 1 or [r.get("full_name") for r in repositories.get("repositories", [])] != [REPOSITORY]:
        raise ReviewError("Installation token is not scoped solely to the requested repository.")
    pr = request("GET", f"/repos/{args.repo}/pulls/{args.pr}", token)
    if pr.get("head", {}).get("sha") != args.head_sha:
        raise ReviewError("PR head changed; review the new commit before submitting.")
    result = request("POST", f"/repos/{args.repo}/pulls/{args.pr}/reviews", token, payload)
    if (result.get("user", {}).get("login") != config["app_slug"] + "[bot]"
            or result.get("commit_id") != args.head_sha
            or result.get("state") != {"COMMENT": "COMMENTED", "APPROVE": "APPROVED", "REQUEST_CHANGES": "CHANGES_REQUESTED"}[args.event]):
        raise ReviewError("Review response identity, commit or state was unexpected; inspect GitHub before retrying.")
    # Never echo response bodies, URLs or arbitrary server fields.
    review_id = result.get("id")
    if type(review_id) is not int or review_id <= 0:
        raise ReviewError("Review response lacked a valid ID; inspect GitHub before retrying.")
    return f"https://github.com/{args.repo}/pull/{args.pr}#pullrequestreview-{review_id}"


def main(argv=None):
    try:
        print(submit(arguments(argv)))
        return 0
    except ReviewError as exc:
        print("agent review: " + str(exc), file=sys.stderr)
    except Exception:
        # Unexpected API shapes or OS errors must never expose credentials in tracebacks.
        print("agent review: unexpected failure; inspect GitHub before retrying. No credentials logged.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
