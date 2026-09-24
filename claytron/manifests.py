"""Build GitHub App Manifest payloads from committed role definitions."""

from __future__ import annotations

import json
from pathlib import Path

from .github import GitHubError


ROOT = Path(__file__).resolve().parents[1]


def definition(role: str) -> dict:
    if role not in ("worker", "reviewer"):
        raise GitHubError("Role must be worker or reviewer.")
    path = ROOT / "config" / "github-apps" / f"{role}.json"
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        raise GitHubError("GitHub App role definition is missing or invalid.") from None
    return value


def payload(role: str, redirect_url: str, setup_url: str) -> dict:
    value = definition(role)
    return {
        "name": value["app_name"],
        "url": value["homepage_url"],
        "description": value["description"],
        "redirect_url": redirect_url,
        "setup_url": setup_url,
        "public": value["public"],
        "default_permissions": value["permissions"],
        "default_events": value["events"],
    }

