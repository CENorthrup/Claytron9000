"""Offline reviewer tests. Keys are generated at runtime, never checked in."""
import base64
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("reviewer", ROOT / "scripts/submit-agent-review.py")
reviewer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reviewer)
SHA = "a" * 40
# Deliberately not a name this repository registers: the tool must pin whatever
# App slug the local configuration names, with none compiled in.
SLUG = "example-reviewer"


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="review tests ' $ ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.key = self.root / "private ' key.pem"
        subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", str(self.key)],
                       check=True, capture_output=True)
        self.key.chmod(0o600)
        self.config = self.root / "config.json"
        self.values = {"app_id": "12", "installation_id": "34", "app_slug": SLUG,
                       "private_key_path": str(self.key)}
        self.config.write_text(json.dumps(self.values))
        self.body = self.root / "review ' $.md"
        self.body.write_text("Reviewed the implementation and failure cases.")
        self.argv = ["--config", str(self.config), "--repo", reviewer.REPOSITORY, "--pr", "8",
                     "--event", "COMMENT", "--body-file", str(self.body), "--model", "test-profile",
                     "--effort", "test-effort", "--harness", "offline-test", "--head-sha", SHA]
        self.calls = []
        self.overrides = {}

    def request(self, method, path, credential, payload=None):
        self.calls.append((method, path, credential, payload))
        if path in self.overrides:
            return self.overrides[path]
        if path == "/app":
            return {"id": 12, "slug": SLUG}
        if path.endswith("/installation"):
            return {"id": 34, "account": {"login": "CENorthrup"}, "repository_selection": "selected",
                    "permissions": {"metadata": "read", "pull_requests": "write"}}
        if path.endswith("/access_tokens"):
            self.assertEqual(payload, {"repositories": ["devenv"], "permissions": {"pull_requests": "write"}})
            return {"token": "TOKEN_SENTINEL", "permissions": {"pull_requests": "write"}}
        if path.startswith("/installation/repositories"):
            return {"total_count": 1, "repositories": [{"full_name": reviewer.REPOSITORY}]}
        if path.endswith("/pulls/8"):
            return {"head": {"sha": SHA}}
        if path.endswith("/reviews"):
            self.assertEqual(credential, "TOKEN_SENTINEL")
            return {"id": 123, "user": {"login": SLUG + "[bot]"}, "commit_id": payload["commit_id"],
                    "state": {"COMMENT": "COMMENTED", "APPROVE": "APPROVED", "REQUEST_CHANGES": "CHANGES_REQUESTED"}[payload["event"]]}
        self.fail("Unexpected endpoint")

    def submit(self):
        return reviewer.submit(reviewer.arguments(self.argv), request=self.request)

    def test_events_provenance_inline_and_no_secret_artifacts(self):
        comments = self.root / "comments.json"
        comments.write_text(json.dumps([{"path": "scripts/file.sh", "line": 2, "side": "RIGHT", "body": "Finding."}]))
        self.argv += ["--comments-file", str(comments)]
        before = {p: p.read_bytes() for p in self.root.iterdir()}
        for event in ("COMMENT", "APPROVE", "REQUEST_CHANGES"):
            with self.subTest(event=event):
                self.argv[self.argv.index("--event") + 1] = event
                output = io.StringIO()
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                    url = self.submit()
                self.assertEqual(url, "https://github.com/CENorthrup/devenv/pull/8#pullrequestreview-123")
                self.assertEqual(output.getvalue(), "")
                payload = self.calls[-1][3]
                self.assertEqual(payload["event"], event)
                self.assertEqual(payload["commit_id"], SHA)
                self.assertIn("Role: reviewer\nModel: test-profile\nEffort: test-effort\nHarness: offline-test\nReviewed commit: " + SHA, payload["body"])
                self.assertEqual(len(payload["comments"]), 1)
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.iterdir()})

    def test_required_provenance_invalid_event_and_sha(self):
        for field in ("--model", "--effort", "--harness", "--head-sha"):
            argv = self.argv.copy()
            index = argv.index(field)
            del argv[index:index + 2]
            with self.subTest(field=field), self.assertRaises(reviewer.ReviewError):
                reviewer.arguments(argv)
        for field, value in (("--model", ""), ("--effort", "high\nforged"), ("--event", "MERGE"),
                             ("--head-sha", "abc123"), ("--repo", "CENorthrup/other")):
            argv = self.argv.copy()
            argv[argv.index(field) + 1] = value
            with self.subTest(field=field), self.assertRaises(reviewer.ReviewError):
                reviewer.arguments(argv)

    def test_stale_sha_refuses_all_events(self):
        self.overrides["/repos/CENorthrup/devenv/pulls/8"] = {"head": {"sha": "b" * 40}}
        for event in ("COMMENT", "APPROVE", "REQUEST_CHANGES"):
            self.argv[self.argv.index("--event") + 1] = event
            with self.assertRaisesRegex(reviewer.ReviewError, "review the new commit"):
                self.submit()
        self.assertFalse(any(c[1].endswith("/reviews") for c in self.calls))

    def test_missing_config_fields_and_unsafe_key(self):
        for field in self.values:
            config = self.values.copy()
            del config[field]
            self.config.write_text(json.dumps(config))
            with self.subTest(field=field), self.assertRaises(reviewer.ReviewError):
                self.submit()
        self.config.write_text(json.dumps(self.values))
        self.key.chmod(0o644)
        with self.assertRaisesRegex(reviewer.ReviewError, "mode 600 or 400"):
            self.submit()
        self.key.chmod(0o600)
        with patch.object(reviewer.os, "open", side_effect=PermissionError("SECRET")):
            with self.assertRaisesRegex(reviewer.ReviewError, "Cannot read or sign"):
                self.submit()
        self.key.unlink()
        with self.assertRaises(reviewer.ReviewError):
            self.submit()
        self.assertFalse(self.calls)

    def test_app_slug_must_come_from_configuration(self):
        for value in (None, "", "Example-Reviewer", "example reviewer", "-bad", "a" * 65):
            config = self.values.copy()
            if value is None:
                del config["app_slug"]
            else:
                config["app_slug"] = value
            self.config.write_text(json.dumps(config))
            with self.subTest(value=value), self.assertRaisesRegex(reviewer.ReviewError, "app_slug"):
                self.submit()
        self.assertFalse(self.calls)
        self.config.write_text(json.dumps(self.values))
        # A review returned under a different bot login must be rejected.
        self.overrides["/repos/CENorthrup/devenv/pulls/8/reviews"] = {
            "id": 1, "user": {"login": "someone-else[bot]"}, "commit_id": SHA, "state": "COMMENTED"}
        with self.assertRaisesRegex(reviewer.ReviewError, "identity"):
            self.submit()

    def test_jwt_signature_and_claims(self):
        token = reviewer.app_jwt(self.values)
        header, claims, signature = token.split(".")
        decode = lambda part: base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))
        self.assertEqual(json.loads(decode(header))["alg"], "RS256")
        values = json.loads(decode(claims))
        self.assertEqual(values["iss"], "12")
        self.assertEqual(values["exp"] - values["iat"], 600)
        public = self.root / "public.pem"
        public.write_bytes(subprocess.run(["openssl", "pkey", "-in", str(self.key), "-pubout"], capture_output=True, check=True).stdout)
        sig = self.root / "signature"
        sig.write_bytes(decode(signature))
        result = subprocess.run(["openssl", "dgst", "-sha256", "-verify", str(public), "-signature", str(sig)],
                                input=(header + "." + claims).encode(), capture_output=True)
        self.assertEqual(result.returncode, 0)

    def test_identity_installation_token_and_api_failures(self):
        cases = {
            "/app": {"id": 12, "slug": "other-app"},
            "/repos/CENorthrup/devenv/installation": {"id": 99},
            "/app/installations/34/access_tokens": {"token": "TOKEN_SENTINEL"},
            "/installation/repositories?per_page=100": {"total_count": 2},
        }
        for path, response in cases.items():
            with self.subTest(path=path):
                self.overrides = {path: response}
                with self.assertRaises(reviewer.ReviewError):
                    self.submit()
        self.overrides = {}
        installation = self.request("GET", "/repos/CENorthrup/devenv/installation", "")
        for permissions in ({"pull_requests": "read"}, {"pull_requests": "write", "contents": "write"}):
            installation["permissions"] = permissions
            self.overrides = {"/repos/CENorthrup/devenv/installation": installation}
            with self.assertRaisesRegex(reviewer.ReviewError, "permissions"):
                self.submit()
        for status in (401, 403, 404, 422, 500):
            failure = urllib.error.HTTPError("SECRET_URL", status, "SECRET_BODY", {}, io.BytesIO(b"TOKEN_SENTINEL"))
            with patch.object(reviewer.urllib.request, "build_opener") as opener:
                opener.return_value.open.side_effect = failure
                with self.assertRaises(reviewer.ReviewError) as caught:
                    reviewer.api("POST", "/test", "TOKEN_SENTINEL", {})
                self.assertIn(str(status), str(caught.exception))
                self.assertNotIn("SECRET", str(caught.exception))
                self.assertNotIn("TOKEN_SENTINEL", str(caught.exception))

    def test_main_failure_and_human_auth_unchanged(self):
        auth = self.root / "gh/hosts.yml"
        auth.parent.mkdir()
        auth.write_text("human-auth-sentinel")
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        environment = {"GH_CONFIG_DIR": str(auth.parent), "GH_TOKEN": "HUMAN_TOKEN"}
        for failure in (reviewer.ReviewError("GitHub API returned HTTP 403"), RuntimeError("JWT_SECRET TOKEN_SENTINEL")):
            output = io.StringIO()
            with patch.dict(os.environ, environment), patch.object(reviewer, "submit", side_effect=failure):
                original = dict(os.environ)
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                    self.assertEqual(reviewer.main(self.argv), 1)
                self.assertEqual(dict(os.environ), original)
            self.assertNotIn("JWT_SECRET", output.getvalue())
            self.assertNotIn("TOKEN_SENTINEL", output.getvalue())
            self.assertNotIn("PRIVATE KEY", output.getvalue())
        # Actual signing/submission uses only OpenSSL, never gh or a shell.
        run = subprocess.run
        with patch.dict(os.environ, environment), patch.object(reviewer.subprocess, "run", wraps=run) as invoked:
            self.submit()
            self.assertTrue(all(call.args[0][0] == "openssl" for call in invoked.call_args_list))
            self.assertTrue(all(not call.kwargs.get("shell") for call in invoked.call_args_list))
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_cli_failure_from_outside_repo_does_not_echo_inputs(self):
        result = subprocess.run(["bash", str(ROOT / "scripts/submit-agent-review.sh"), "--event", "TOKEN_SENTINEL"],
                                cwd=self.root, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("TOKEN_SENTINEL", result.stdout + result.stderr)

    def test_review_post_failure_leaves_no_credentials_or_temporary_files(self):
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        original_submit = reviewer.submit

        def fail_review(method, path, credential, payload=None):
            if path.endswith("/reviews"):
                raise reviewer.ReviewError("GitHub API returned HTTP 422")
            return self.request(method, path, credential, payload)

        output = io.StringIO()
        with patch.dict(os.environ, {"TMPDIR": str(self.root)}), patch.object(
                reviewer, "submit", side_effect=lambda args: original_submit(args, request=fail_review)):
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                self.assertEqual(reviewer.main(self.argv), 1)
        self.assertIn("HTTP 422", output.getvalue())
        self.assertNotIn("TOKEN_SENTINEL", output.getvalue())
        self.assertNotIn(self.key.read_text(), output.getvalue())
        for call in self.calls:
            if call[2]:
                self.assertNotIn(call[2], output.getvalue())
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()})


if __name__ == "__main__":
    unittest.main()
