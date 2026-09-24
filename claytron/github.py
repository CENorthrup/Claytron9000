"""Shared GitHub App authentication and repository-scope helpers."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request


API_VERSION = "2026-03-10"
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
APP_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")


class GitHubError(Exception):
    """Credential-free GitHub or configuration failure."""


def read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        raise GitHubError("Cannot read valid JSON configuration.") from None


def repository_name(value: str) -> str:
    if not isinstance(value, str) or not REPOSITORY_RE.fullmatch(value):
        raise GitHubError("Repository must use the OWNER/REPOSITORY form.")
    return value


def encoded(value: bytes) -> bytes:
    return base64.urlsafe_b64encode(value).rstrip(b"=")


def app_jwt(config: dict, now: int | None = None) -> str:
    now = int(time.time()) if now is None else now
    data = b".".join(encoded(json.dumps(value, separators=(",", ":")).encode()) for value in (
        {"alg": "RS256", "typ": "JWT"},
        {"iat": now - 60, "exp": now + 540, "iss": str(config["app_id"])},
    ))
    key_path = Path(config["private_key_path"])
    try:
        fd = os.open(key_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as key:
            info = os.fstat(key.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise GitHubError("Private key must be a regular file owned by this user with mode 600 or stricter.")
            result = subprocess.run(
                ["openssl", "dgst", "-sha256", "-sign", f"/dev/fd/{key.fileno()}", "-passin", "pass:"],
                input=data, capture_output=True, pass_fds=(key.fileno(),), timeout=15,
            )
        if result.returncode:
            raise GitHubError("Unable to sign App JWT; check the private key and OpenSSL installation.")
    except GitHubError:
        raise
    except (OSError, subprocess.SubprocessError):
        raise GitHubError("Cannot read or sign with the private key; check its path and permissions.") from None
    return (data + b"." + encoded(result.stdout)).decode()


class GitHubClient:
    """Small API client that never includes credentials in diagnostics."""

    def __init__(self, config: dict, role: str, request=None):
        self.config = config
        self.role = role
        self.request = request or self._request

    def _request(self, method: str, path: str, credential: str, payload=None):
        request = urllib.request.Request(
            "https://api.github.com" + path,
            data=None if payload is None else json.dumps(payload).encode(),
            method=method,
            headers={
                "Authorization": "Bearer " + credential,
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": API_VERSION,
                "User-Agent": "claytron9000-" + self.role,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            raise GitHubError(f"GitHub API returned HTTP {exc.code}; inspect GitHub state before retrying.") from None
        except (OSError, ValueError):
            raise GitHubError("GitHub API transport or JSON failure; inspect GitHub state before retrying.") from None

    def jwt(self) -> str:
        return app_jwt(self.config)

    def app(self):
        return self.request("GET", "/app", self.jwt())

    def installation(self, repository: str):
        repository_name(repository)
        return self.request("GET", f"/repos/{repository}/installation", self.jwt())

    def verify_identity(self):
        app = self.app()
        if str(app.get("id")) != str(self.config["app_id"]):
            raise GitHubError("Configured App ID does not match the GitHub App identity.")
        if app.get("slug") != self.config["app_slug"]:
            raise GitHubError("Configured App slug does not match the GitHub App identity.")
        return app

    def verify_installation(self, repositories: list[str]):
        repositories = [repository_name(repo) for repo in repositories]
        if not repositories:
            raise GitHubError("At least one selected repository is required.")
        installation = self.installation(repositories[0])
        if installation.get("repository_selection") != "selected":
            raise GitHubError("App installation must use selected repositories, not all repositories.")
        if str(installation.get("account", {}).get("login")) != str(self.config["account_login"]):
            raise GitHubError("App installation account does not match configuration.")
        expected_permissions = self.config["permissions"]
        if installation.get("permissions") != expected_permissions:
            raise GitHubError("Installed App permissions do not match the role definition.")
        token = self.request("POST", f"/app/installations/{installation['id']}/access_tokens", self.jwt(),
                             {"repositories": [repo.split("/", 1)[1] for repo in repositories],
                              "permissions": expected_permissions})
        access_token = token.get("token")
        if not isinstance(access_token, str) or not access_token:
            raise GitHubError("GitHub did not return an installation token.")
        visible = self.request("GET", "/installation/repositories?per_page=100", access_token)
        actual = sorted(repo.get("full_name") for repo in visible.get("repositories", []))
        if actual != sorted(repositories):
            raise GitHubError("Installation repository scope differs from the requested selected repositories.")
        return {"installation_id": installation["id"], "repositories": actual, "permissions": expected_permissions}

    def token(self, repository: str):
        result = self.verify_installation([repository])
        minted = self.request("POST", f"/app/installations/{result['installation_id']}/access_tokens", self.jwt(),
                              {"repositories": [repository.split("/", 1)[1]],
                               "permissions": self.config["permissions"]})
        value = minted.get("token")
        if not isinstance(value, str) or not value:
            raise GitHubError("GitHub did not return a short-lived installation token.")
        return value

    def repository(self, repository: str, token: str):
        repository_name(repository)
        return self.request("GET", f"/repos/{repository}", token)

    def pull_request(self, repository: str, number: int, token: str):
        repository_name(repository)
        return self.request("GET", f"/repos/{repository}/pulls/{number}", token)

