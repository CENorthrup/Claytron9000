"""Additional offline checks for malformed setup and safe identity bootstrap."""

from __future__ import annotations

import importlib.util
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
                         "https://github.com/settings/apps/claytron-worker")
        with self.assertRaises(GitHubError):
            setup.app_settings_url("")

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


if __name__ == "__main__":
    unittest.main()
