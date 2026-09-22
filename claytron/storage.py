"""Secure external storage for GitHub App configuration and private keys."""

from __future__ import annotations

import json
from pathlib import Path
import os
import stat

from .github import APP_SLUG_RE, GitHubError, repository_name


def ensure_private_directory(path: Path):
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)
    if path.stat().st_mode & 0o077:
        raise GitHubError("Credential directory must have mode 700.")


def store_app_config(root: Path, role: str, response: dict, account_login: str, repositories: list[str], permissions: dict):
    pem = response.get("pem")
    app_id = response.get("id")
    slug = response.get("slug")
    if not isinstance(pem, str) or not pem or not isinstance(app_id, int) or app_id <= 0 or not isinstance(slug, str):
        raise GitHubError("GitHub App conversion response lacked required configuration.")
    ensure_private_directory(root)
    directory = root / role
    ensure_private_directory(directory)
    key = directory / "private-key.pem"
    if os.path.lexists(key):
        raise GitHubError("Private key already exists; rotate it explicitly instead of overwriting it.")
    try:
        fd = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(pem)
    except OSError:
        raise GitHubError("Cannot create the private key with exclusive mode 600 storage.") from None
    config = {
        "role": role,
        "app_id": app_id,
        "app_slug": slug,
        "account_login": account_login,
        "installation_id": None,
        "repositories": repositories,
        "permissions": permissions,
        "private_key_path": str(key),
    }
    config_path = directory / "config.json"
    if os.path.lexists(config_path):
        raise GitHubError("App configuration already exists; remove it only as part of deliberate recovery.")
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    config_path.chmod(0o600)
    return config_path


def validate_config(config: dict, role: str):
    if not isinstance(config, dict):
        raise GitHubError("App configuration must be a JSON object.")
    if config.get("role") != role:
        raise GitHubError("App configuration role does not match the requested identity.")
    for field in ("app_id", "installation_id"):
        if not str(config.get(field, "")).isdigit() or int(config[field]) <= 0:
            raise GitHubError("App configuration requires positive numeric IDs.")
    if not isinstance(config.get("app_slug"), str) or not APP_SLUG_RE.fullmatch(config["app_slug"]):
        raise GitHubError("App configuration requires a valid App slug.")
    if not isinstance(config.get("account_login"), str) or not config["account_login"].strip():
        raise GitHubError("App configuration requires an account login.")
    if not isinstance(config.get("permissions"), dict):
        raise GitHubError("App configuration requires a permissions object.")
    if not isinstance(config.get("repositories"), list) or not config["repositories"]:
        raise GitHubError("App configuration requires selected repositories.")
    for value in config["repositories"]:
        repository_name(value)
    key = Path(config.get("private_key_path", ""))
    if not key.is_absolute():
        raise GitHubError("Configured private key path must be absolute.")
    try:
        info = key.stat()
    except OSError:
        raise GitHubError("Configured private key is missing.") from None
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise GitHubError("Private key must be a regular file owned by this user with mode 600 or stricter.")
    config_path = key.parent / "config.json"
    if config_path.exists() and config_path.stat().st_mode & 0o077:
        raise GitHubError("Sensitive App configuration must have mode 600 or stricter.")
