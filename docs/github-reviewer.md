# Agent review submission

Claytron9000 owns the mechanism for submitting an agent review under the
separate `claytron-reviewer` GitHub App identity. The App definition and
human-gated creation/install flow are documented in
[github-identities.md](github-identities.md).

## Submit a completed review

Dependencies are Python 3 and OpenSSL. The `devenv` thin-client environment
provisions them as reusable machine capabilities, but this repository only
requires them to be available in the operator's environment. No third-party
Python packages are needed.

Review the code first and record its full head SHA. Save actual findings in a
UTF-8 file, then submit with the exact model/profile, effort, and harness:

```bash
just submit-agent-review \
  --repo OWNER/REPOSITORY --pr 123 --event COMMENT \
  --body-file /tmp/review.md \
  --model 'ACTUAL MODEL/PROFILE' --effort 'ACTUAL EFFORT' \
  --harness 'ACTUAL HARNESS' --head-sha FULL_40_CHARACTER_REVIEWED_SHA
```

The reviewer configuration must list the requested repository under the
selected installation scope. `COMMENT`, `APPROVE`, and `REQUEST_CHANGES` are
available; the event must match the actual review. The tool cannot merge,
modify source contents, administer repositories, bypass branch protection, or
impersonate a human approval.

Every body gets this generated provenance block:

```text
Agent review provenance

Role: reviewer
Model: <caller-provided exact model/profile>
Effort: <caller-provided effort>
Harness: <caller-provided harness>
Reviewed commit: <full reviewed SHA>
```

The App signs an RS256 JWT, verifies its configured identity and installation,
mints a short-lived token scoped to the requested repository, checks the live
PR head immediately before submission, and sends the explicit `commit_id`.
Credentials exist only in memory and restricted file descriptors. API failures
do not print response bodies, URLs containing codes, keys, JWTs, or installation
tokens. The command never invokes or changes `gh` authentication.

Inline findings use GitHub diff coordinates:

```json
[
  {"path": "scripts/example.sh", "line": 10, "side": "RIGHT", "body": "Explain the finding and its impact."}
]
```

For a multiline finding include `start_line` and `start_side`. GitHub validates
whether the location belongs to the reviewed diff. A changed PR head fails and
requires a new review. Tests use mocked API responses and never manufacture an
approval.

Run `just test-agent-review` for the offline reviewer, identity, gate, storage,
worker-safety, malformed-configuration, and unexpected-response checks.
