"""Additional offline checks for malformed setup and safe identity bootstrap."""

from __future__ import annotations

import importlib.util
import io
from types import SimpleNamespace
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("setup_github", ROOT / "scripts/setup-github.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)
from claytron.github import GitHubError  # noqa: E402


class SetupTests(unittest.TestCase):
    def test_registered_app_uses_account_settings_url_for_installation(self):
        self.assertEqual(setup.app_settings_url("claytron-worker"),
                         "https://github.com/settings/apps/claytron-worker/installations")
        with self.assertRaises(GitHubError):
            setup.app_settings_url("")

    def test_registration_redirect_has_clickable_fallback(self):
        handler_class = setup.Setup.handler(SimpleNamespace())
        handler = object.__new__(handler_class)
        handler.wfile = io.BytesIO()
        handler.send_response = mock.Mock()
        handler.send_header = mock.Mock()
        handler.end_headers = mock.Mock()
        url = setup.app_settings_url("claytron-reviewer")
        handler.redirect_to_installation(url)
        handler.send_response.assert_called_once_with(303)
        handler.send_header.assert_any_call("Location", url)
        handler.send_header.assert_any_call("Referrer-Policy", "no-referrer")
        self.assertIn(f'href="{url}"', handler.wfile.getvalue().decode())

    def test_setup_requires_selected_repositories(self):
        with tempfile.TemporaryDirectory() as directory:
            args = type("Args", (), {"role": "reviewer", "repositories": [], "config_root": directory,
                                      "gate_root": directory, "account": "Example", "timeout": 1,
                                      "no_browser": True})()
            with self.assertRaisesRegex(GitHubError, "selected repository"):
                setup.Setup(args)

    def test_conversion_failure_does_not_echo_secret(self):
        with self.assertRaisesRegex(GitHubError, "conversion failed"):
            with mock.patch("urllib.request.urlopen", side_effect=RuntimeError("TOKEN_SENTINEL")):
                setup.conversion("CODE_SENTINEL")

    def test_confirmation_page_is_explicit_html_without_credentials(self):
        page = setup.confirmation_page(
            "Claytron setup complete", "Installation verified",
            ["Claytron verified the selected repositories and requested permissions.",
             "You may close this window."]
        ).decode()
        self.assertIn("<title>Claytron setup complete</title>", page)
        self.assertIn("<h1>Installation verified</h1>", page)
        self.assertNotIn("private-key.pem", page)
        self.assertNotIn("jwt", page.lower())

    def test_confirmation_page_escapes_callback_content(self):
        page = setup.confirmation_page("<title>", "<heading>", ["<secret>"]).decode()
        self.assertNotIn("<secret>", page)
        self.assertIn("&lt;secret&gt;", page)


if __name__ == "__main__":
    unittest.main()
