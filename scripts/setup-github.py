#!/usr/bin/env python3
"""Create and install one Claytron GitHub App through explicit human gates."""

from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
import urllib.parse
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from claytron.github import GitHubClient, GitHubError, API_VERSION, repository_name  # noqa: E402
from claytron.human_gate import GateStore, HumanGate  # noqa: E402
from claytron.manifests import definition, payload  # noqa: E402
from claytron.storage import store_app_config  # noqa: E402


def conversion(code: str):
    request = urllib.request.Request(
        f"https://api.github.com/app-manifests/{urllib.parse.quote(code, safe='')}/conversions",
        data=b"{}", method="POST",
        headers={"Accept": "application/vnd.github+json", "Content-Type": "application/json",
                 "X-GitHub-Api-Version": API_VERSION, "User-Agent": "claytron9000-setup"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.load(response)
    except Exception as exc:
        # Do not echo response bodies, URLs containing codes, or exception text.
        raise GitHubError("GitHub App manifest conversion failed; retry the setup gate.") from exc
    if not isinstance(result, dict):
        raise GitHubError("GitHub App manifest conversion returned an unexpected response.")
    return result


def app_settings_url(slug: str) -> str:
    if not isinstance(slug, str) or not slug:
        raise GitHubError("GitHub App response lacked a valid slug.")
    return f"https://github.com/settings/apps/{urllib.parse.quote(slug, safe='')}"


def confirmation_page(title: str, heading: str, paragraphs: list[str]) -> bytes:
    content = "".join(f"<p>{html.escape(value)}</p>" for value in paragraphs)
    return ("<!doctype html><meta charset=utf-8>"
            f"<title>{html.escape(title)}</title>"
            f"<main><h1>{html.escape(heading)}</h1>{content}</main>").encode()


class Setup:
    def __init__(self, args):
        self.args = args
        self.role = args.role
        self.definition = definition(args.role)
        self.repositories = [repository_name(value) for value in args.repositories]
        if not self.repositories:
            raise GitHubError("Provide at least one selected repository with --repository.")
        self.config_root = Path(args.config_root).expanduser()
        self.gate_store = GateStore(Path(args.gate_root).expanduser())
        self.state = secrets.token_urlsafe(32)
        self.finished = threading.Event()
        self.result = None
        self.server = None
        self.gate = HumanGate.create(
            operation=f"Create the {self.definition['app_name']} GitHub App",
            reason="GitHub requires the account owner to approve creation in the browser.",
            approval=f"Create one private App with Metadata read and {', '.join(f'{k} {v}' for k, v in self.definition['permissions'].items() if k != 'metadata')}.",
            requested_permissions=[f"{key}: {value}" for key, value in self.definition["permissions"].items()],
            user_action="Open the local setup page, review the manifest, and click Create GitHub App in GitHub.",
            completion_check="Claytron receives GitHub's one-time manifest callback and verifies the returned App identity.",
            next_step="Select repositories during installation, then return to Claytron for installation verification.",
        )

    def start_url(self):
        return f"http://127.0.0.1:{self.server.server_port}/start"

    def run(self):
        self.server = HTTPServer(("127.0.0.1", 0), self.handler())
        self.server.timeout = 0.5
        self.gate_store.save(self.gate)
        print(self.gate.display())
        print(f"Local setup page: {self.start_url()}")
        print("The local callback listens only on 127.0.0.1 and stores no callback code.")
        if not self.args.no_browser:
            webbrowser.open(self.start_url())
        deadline = time.monotonic() + self.args.timeout
        while not self.finished.is_set() and time.monotonic() < deadline:
            self.server.handle_request()
        if not self.finished.is_set():
            self.gate.fail("Timed out waiting for the GitHub human gates.")
            self.gate_store.save(self.gate)
            raise GitHubError("Timed out waiting for GitHub approval or installation.")
        if self.result is not None:
            raise self.result

    def handler(self):
        setup = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                return

            def send_page(self, status, title, heading, paragraphs):
                body = confirmation_page(title, heading, paragraphs)
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def redirect_to_installation(self, url):
                body = confirmation_page("Claytron App registered", "Continue to installation",
                                         ["Registration verified. Continue to GitHub and select the requested repositories."])
                body += f'<p><a href="{html.escape(url, quote=True)}">Open App settings to install</a></p>'.encode()
                self.send_response(303)
                self.send_header("Location", url)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                path = urllib.parse.urlsplit(self.path).path
                try:
                    if path == "/start":
                        manifest = json.dumps(payload(setup.role,
                            f"http://127.0.0.1:{setup.server.server_port}/callback",
                            f"http://127.0.0.1:{setup.server.server_port}/install-callback"), separators=(",", ":"))
                        action = "https://github.com/settings/apps/new?state=" + urllib.parse.quote(setup.state)
                        form = ("<!doctype html><meta charset=utf-8><title>Claytron GitHub setup</title>"
                                "<h1>Human gate</h1><p>Review the requested App permissions before continuing.</p>"
                                f"<form method=post action=\"{html.escape(action)}\">"
                                f"<input type=hidden name=manifest value=\"{html.escape(manifest, quote=True)}\">"
                                "<button type=submit>Open GitHub App registration</button></form>")
                        body = form.encode()
                        self.send_response(200)
                        self.send_header("Content-Type", "text/html; charset=utf-8")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                        return
                    if path == "/callback":
                        if query.get("state", [""])[0] != setup.state or not query.get("code", [""])[0]:
                            raise GitHubError("GitHub registration callback state was invalid.")
                        response = conversion(query["code"][0])
                        config_path = store_app_config(setup.config_root, setup.role, response,
                                                       setup.args.account, setup.repositories,
                                                       setup.definition["permissions"])
                        try:
                            config = json.loads(config_path.read_text())
                            GitHubClient(config, setup.role).verify_identity()
                        except Exception:
                            # Do not leave credentials from a registration that
                            # cannot be authenticated by the returned App.
                            key_path = config_path.parent / "private-key.pem"
                            config_path.unlink(missing_ok=True)
                            key_path.unlink(missing_ok=True)
                            raise GitHubError("GitHub created an App that Claytron could not authenticate; check the browser account and retry.") from None
                        setup.gate.complete({"step": "app-created", "role": setup.role})
                        setup.gate_store.save(setup.gate)
                        install_gate = HumanGate.create(
                            operation=f"Install {response['slug']} on selected repositories",
                            reason="GitHub requires the account owner to choose the installation scope.",
                            approval=f"Install only these selected repositories: {', '.join(setup.repositories)}.",
                            requested_permissions=[f"{key}: {value}" for key, value in setup.definition["permissions"].items()],
                            user_action=f"Open the App settings page at {app_settings_url(response.get('slug'))}, click Install App, select Only select repositories, choose the requested repositories, and click Install.",
                            completion_check="GitHub redirects to this local callback and Claytron verifies selected scope and permissions.",
                            next_step="Claytron records the installation ID and marks the role ready.",
                        )
                        setup.install_gate = install_gate
                        setup.gate_store.save(install_gate)
                        # Private Apps owned by the current account are installed
                        # from their account-specific settings page. The public
                        # /apps/<slug>/installations/new route may return 404.
                        install_url = app_settings_url(response.get("slug"))
                        print(install_gate.display())
                        print(f"App settings URL (then click Install App): {install_url}")
                        self.redirect_to_installation(install_url)
                        return
                    if path == "/install-callback":
                        installation_id = query.get("installation_id", [""])[0]
                        if not installation_id.isdigit() or query.get("setup_action", [""])[0] != "install":
                            raise GitHubError("GitHub installation callback was invalid.")
                        config_path = setup.config_root / setup.role / "config.json"
                        config = json.loads(config_path.read_text())
                        client = GitHubClient(config, setup.role)
                        verification = client.verify_installation(setup.repositories)
                        if str(verification["installation_id"]) != installation_id:
                            raise GitHubError("GitHub installation callback did not match the verified installation.")
                        config["installation_id"] = int(installation_id)
                        config_path.write_text(json.dumps(config, indent=2) + "\n")
                        config_path.chmod(0o600)
                        setup.install_gate.complete({"step": "installation-verified", "repositories": verification["repositories"]})
                        setup.gate_store.save(setup.install_gate)
                        setup.finished.set()
                        self.send_page(200, "Claytron setup complete", "Installation verified",
                                       [f"{setup.definition['app_name']} is installed for {setup.args.account}.",
                                        "Claytron verified the selected repositories and requested permissions.",
                                        "You may close this window. The setup command has completed."])
                        return
                    self.send_page(404, "Claytron setup", "Page not found",
                                   ["This Claytron setup callback is no longer active.",
                                    "Restart the setup command and use its newly displayed local URL."])
                except Exception as exc:
                    setup.result = exc if isinstance(exc, GitHubError) else GitHubError("GitHub setup failed safely; inspect state and retry.")
                    if hasattr(setup, "install_gate"):
                        try:
                            setup.install_gate.fail("The installation callback could not be verified.")
                            setup.gate_store.save(setup.install_gate)
                        except ValueError:
                            pass
                    else:
                        try:
                            setup.gate.fail("The App registration callback could not be verified.")
                            setup.gate_store.save(setup.gate)
                        except ValueError:
                            pass
                    setup.finished.set()
                    self.send_page(400, "Claytron setup could not verify the gate",
                                   "Verification failed",
                                   ["Claytron could not verify this GitHub gate.",
                                    "No credential details were logged. Inspect the saved gate state and restart setup if needed."])

        return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", required=True, choices=["worker", "reviewer"])
    parser.add_argument("--account", required=True)
    parser.add_argument("--repository", dest="repositories", action="append", required=True,
                        help="selected OWNER/REPOSITORY; repeat for each repository")
    parser.add_argument("--config-root", default="~/.config/claytron9000/github-apps")
    parser.add_argument("--gate-root", default="~/.local/state/claytron9000/gates")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    try:
        Setup(args).run()
    except GitHubError as exc:
        print("claytron setup: " + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
