"""Offline reviewer, identity-separation, and credential-safety tests."""

from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


reviewer = load("reviewer", "scripts/submit-agent-review.py")
worker = load("worker", "scripts/worker.py")
from claytron.human_gate import GateState, GateStore, HumanGate  # noqa: E402
from claytron.manifests import payload  # noqa: E402
from claytron.storage import store_app_config  # noqa: E402

SHA = "a" * 40
REPO = "Example/target"
SLUG = "example-reviewer"


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="claytron identity tests ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.key = self.root / "private-key.pem"
        subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", str(self.key)],
                       check=True, capture_output=True)
        self.key.chmod(0o600)
        self.config = self.root / "config.json"
        self.values = {"role": "reviewer", "app_id": 12, "installation_id": 34,
                       "app_slug": SLUG, "account_login": "Example", "repositories": [REPO],
                       "permissions": {"metadata": "read", "pull_requests": "write"},
                       "private_key_path": str(self.key)}
        self.config.write_text(json.dumps(self.values))
        self.config.chmod(0o600)
        self.body = self.root / "review.md"
        self.body.write_text("Reviewed the implementation and failure cases.")
        self.argv = ["--config", str(self.config), "--repo", REPO, "--pr", "8", "--event", "COMMENT",
                     "--body-file", str(self.body), "--model", "test-profile", "--effort", "test-effort",
                     "--harness", "offline-test", "--head-sha", SHA]
        self.calls = []
        self.overrides = {}

    def request(self, method, path, credential, payload=None):
        self.calls.append((method, path, credential, payload))
        if path in self.overrides:
            return self.overrides[path]
        if path == "/app":
            return {"id": 12, "slug": SLUG}
        if path.endswith("/installation"):
            return {"id": 34, "account": {"login": "Example"}, "repository_selection": "selected",
                    "permissions": self.values["permissions"]}
        if path.endswith("/access_tokens"):
            self.assertEqual(payload, {"repositories": ["target"], "permissions": self.values["permissions"]})
            return {"token": "TOKEN_SENTINEL", "permissions": self.values["permissions"]}
        if path.startswith("/installation/repositories"):
            return {"total_count": 1, "repositories": [{"full_name": REPO}]}
        if path.endswith("/pulls/8"):
            return {"head": {"sha": SHA}}
        if path.endswith("/reviews"):
            self.assertEqual(credential, "TOKEN_SENTINEL")
            return {"id": 123, "user": {"login": SLUG + "[bot]"}, "commit_id": payload["commit_id"],
                    "state": {"COMMENT": "COMMENTED", "APPROVE": "APPROVED", "REQUEST_CHANGES": "CHANGES_REQUESTED"}[payload["event"]]}
        self.fail("Unexpected endpoint")

    def submit(self):
        return reviewer.submit(reviewer.arguments(self.argv), request=self.request, sign=lambda _: "JWT")


class ReviewerTests(Fixture):
    def test_events_provenance_and_exact_scope(self):
        for event in ("COMMENT", "APPROVE", "REQUEST_CHANGES"):
            self.argv[self.argv.index("--event") + 1] = event
            result = self.submit()
            self.assertEqual(result, "https://github.com/Example/target/pull/8#pullrequestreview-123")
            body = self.calls[-1][3]["body"]
            self.assertIn("Role: reviewer\nModel: test-profile\nEffort: test-effort\nHarness: offline-test\nReviewed commit: " + SHA, body)

    def test_stale_sha_refuses_submission(self):
        self.overrides["/repos/Example/target/pulls/8"] = {"head": {"sha": "b" * 40}}
        with self.assertRaisesRegex(reviewer.ReviewError, "new commit"):
            self.submit()
        self.assertFalse(any(call[1].endswith("/reviews") for call in self.calls))

    def test_reviewer_cannot_use_worker_configuration_or_contents_write(self):
        self.values["role"] = "worker"
        self.config.write_text(json.dumps(self.values))
        with self.assertRaisesRegex(reviewer.ReviewError, "role"):
            self.submit()
        self.values["role"] = "reviewer"
        self.values["permissions"]["contents"] = "write"
        self.config.write_text(json.dumps(self.values))
        with self.assertRaises(reviewer.ReviewError):
            self.submit()

    def test_missing_configuration_fields_are_rejected(self):
        for field in ("role", "app_id", "installation_id", "app_slug", "account_login", "repositories",
                      "permissions", "private_key_path"):
            values = self.values.copy()
            del values[field]
            self.config.write_text(json.dumps(values))
            with self.subTest(field=field), self.assertRaises(reviewer.ReviewError):
                self.submit()

    def test_unexpected_identity_permissions_and_api_failures_are_safe(self):
        self.overrides["/app"] = {"id": 12, "slug": "different"}
        with self.assertRaisesRegex(reviewer.ReviewError, "identity"):
            self.submit()
        self.overrides = {"/repos/Example/target/installation": {"id": 34}}
        with self.assertRaises(reviewer.ReviewError):
            self.submit()
        self.overrides = {"/installation/repositories?per_page=100": {"total_count": 2}}
        with self.assertRaises(reviewer.ReviewError):
            self.submit()

    def test_private_key_mode_and_jwt_claims(self):
        self.key.chmod(0o644)
        with self.assertRaisesRegex(reviewer.ReviewError, "mode 600"):
            reviewer.app_jwt(self.values)
        self.key.chmod(0o600)
        token = reviewer.app_jwt(self.values)
        header, claims, signature = token.split(".")
        decode = lambda value: base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        self.assertEqual(json.loads(decode(header))["alg"], "RS256")
        self.assertEqual(json.loads(decode(claims))["iss"], "12")
        self.assertTrue(signature)


class BootstrapAndGateTests(unittest.TestCase):
    def test_manifest_permissions_are_distinct_and_selected(self):
        worker_manifest = payload("worker", "http://127.0.0.1/callback", "http://127.0.0.1/install")
        reviewer_manifest = payload("reviewer", "http://127.0.0.1/callback", "http://127.0.0.1/install")
        self.assertEqual(worker_manifest["default_permissions"], {"metadata": "read", "contents": "write", "pull_requests": "write"})
        self.assertEqual(reviewer_manifest["default_permissions"], {"metadata": "read", "pull_requests": "write"})
        self.assertNotIn("contents", reviewer_manifest["default_permissions"])

    def test_gate_transitions_and_secure_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = GateStore(root / "gates")
            gate = HumanGate.create("install App", "GitHub approval", "selected repositories", ["pull_requests: write"],
                                    "approve in GitHub", "installation callback", "verify installation")
            path = store.save(gate)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual((root / "gates").stat().st_mode & 0o777, 0o700)
            self.assertEqual(store.load(gate.gate_id).state, GateState.PENDING.value)
            gate.complete({"installation_id": 9})
            store.save(gate)
            self.assertEqual(store.load(gate.gate_id).state, GateState.COMPLETED.value)
            with self.assertRaises(ValueError):
                gate.fail("late")

    def test_app_response_storage_never_puts_private_key_in_json(self):
        with tempfile.TemporaryDirectory() as directory:
            config = store_app_config(Path(directory), "reviewer",
                                      {"id": 9, "slug": SLUG, "pem": "PRIVATE_KEY_SENTINEL"}, "Example", [REPO],
                                      {"metadata": "read", "pull_requests": "write"})
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)
            self.assertEqual((config.parent / "private-key.pem").stat().st_mode & 0o777, 0o600)
            self.assertNotIn("PRIVATE_KEY_SENTINEL", config.read_text())
            self.assertIn("PRIVATE_KEY_SENTINEL", (config.parent / "private-key.pem").read_text())


class WorkerSafetyTests(unittest.TestCase):
    def test_default_branch_and_merge_refusals(self):
        with self.assertRaisesRegex(worker.GitHubError, "default branch"):
            worker.assert_safe_branch("main", "main")
        with self.assertRaisesRegex(worker.GitHubError, "merge"):
            worker.refuse("merge")
        with self.assertRaisesRegex(worker.GitHubError, "default branch"):
            worker.refuse("push to the default branch")

        class Client:
            def repository(self, repo, token):
                return {"default_branch": "main"}
        with self.assertRaisesRegex(worker.GitHubError, "default branch"):
            with patch.object(worker.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "main\n", "")):
                worker.commit_changes(Client(), REPO, "TOKEN", "unsafe")

    def test_pr_creation_requires_non_default_head(self):
        class Client:
            def repository(self, repo, token):
                return {"default_branch": "main"}
            def request(self, method, path, token, payload):
                self.call = (method, path, payload)
                return {"number": 7, "html_url": "https://github.com/Example/target/pull/7"}
        client = Client()
        result = worker.create_or_update_pr(client, REPO, "TOKEN", "feature/work", "main", "Title", "Body")
        self.assertEqual(result["number"], 7)
        with self.assertRaisesRegex(worker.GitHubError, "default branch"):
            worker.create_or_update_pr(client, REPO, "TOKEN", "main", "main", "Title", "Body")


if __name__ == "__main__":
    unittest.main()
