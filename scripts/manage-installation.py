#!/usr/bin/env python3
"""Add or remove a selected repository through a human-gated GitHub action."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from claytron.github import GitHubClient, GitHubError, read_json, repository_name  # noqa: E402
from claytron.human_gate import GateStore, HumanGate  # noqa: E402
from claytron.storage import validate_config  # noqa: E402

ROLE_PERMISSIONS = {
    "worker": {"metadata": "read", "contents": "write", "pull_requests": "write"},
    "reviewer": {"metadata": "read", "pull_requests": "write"},
}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", required=True, choices=["worker", "reviewer"])
    parser.add_argument("--action", required=True, choices=["add", "remove"])
    parser.add_argument("--repository", required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--gate-root", default="~/.local/state/claytron9000/gates")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    try:
        repository = repository_name(args.repository)
        config = read_json(args.config)
        validate_config(config, args.role)
        if config.get("permissions") != ROLE_PERMISSIONS[args.role]:
            raise GitHubError("Configured App permissions do not match the selected role.")
        current = list(config.get("repositories", []))
        if args.action == "add" and repository not in current:
            desired = sorted(current + [repository])
        elif args.action == "remove" and repository in current and len(current) > 1:
            desired = sorted(set(current) - {repository})
        else:
            raise GitHubError("Requested repository change is already satisfied or would remove the final scope.")
        if not config.get("installation_id"):
            raise GitHubError("Configuration has no verified installation ID.")
        gate = HumanGate.create(
            operation=f"{args.action} {repository} for the {args.role} installation",
            reason="GitHub requires a human account owner to change selected repository scope.",
            approval=f"Change the selected scope from {', '.join(sorted(current))} to {', '.join(desired)}.",
            requested_permissions=[f"{key}: {value}" for key, value in config["permissions"].items()],
            user_action="Open the installation settings, change only the selected repositories, and save.",
            completion_check="Claytron polls GitHub and verifies the exact selected scope and unchanged permissions.",
            next_step="Claytron records the new repository scope and returns success.",
        )
        store = GateStore(Path(args.gate_root).expanduser())
        store.save(gate)
        print(gate.display())
        url = f"https://github.com/settings/installations/{config['installation_id']}"
        print("Installation settings: " + url)
        if not args.no_browser:
            webbrowser.open(url)
        client = GitHubClient(config, args.role)
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            try:
                result = client.verify_installation(desired)
                config["repositories"] = result["repositories"]
                args.config.write_text(json.dumps(config, indent=2) + "\n")
                args.config.chmod(0o600)
                gate.complete({"repositories": result["repositories"]})
                store.save(gate)
                print(f"{args.role} selected repository scope verified.")
                return 0
            except GitHubError:
                time.sleep(5)
        gate.fail("Timed out waiting for the selected repository scope change.")
        store.save(gate)
        raise GitHubError("Timed out waiting for the GitHub installation scope change.")
    except GitHubError as exc:
        print("claytron installation: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
