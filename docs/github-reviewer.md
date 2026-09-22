# Agent review submission

Claytron9000 owns the *mechanism* for submitting an agent review under a GitHub
App identity. It does not own the identity itself.

> **Scope: no GitHub App is registered by this work.**
> Creating the reviewer and worker App identities, installing them and validating
> them live is deferred to the Claytron9000 GitHub identity/bootstrap work, which
> will register `claytron-reviewer` and `claytron-worker`. Nothing here requires
> an App to exist, and no App name is compiled into the tool: the local
> configuration names the App this command will pin. When those identities are
> created, this mechanism moves or adapts to them by configuration, not by a
> rewrite.

A review records the model, effort and harness that performed it in its body,
separately from GitHub identity. That keeps agent findings from being attributed
to Clay and lets an independent identity submit `COMMENT`, `APPROVE` or
`REQUEST_CHANGES` on human-authored PRs once one exists.

Clay retains final merge authority. This tool cannot merge, does not change branch
protection or bypass rules, and must never be used to impersonate human approval.
GitHub's `Pull requests: write` permission is broader than writing reviews; the
narrow command interface is not a stronger GitHub authorization boundary.

## Registration reference (deferred to Claytron9000)

These steps are recorded so the identity work has a starting point. **Do not
perform them as part of this repository's reviewer tooling.** The Claytron9000
GitHub identity/bootstrap work owns App creation, naming and installation, and
may change any of it. `<app-slug>` below stands for whatever it registers.

1. Register the App under `CENorthrup`. The Claytron9000 work decides the name;
   this tooling does not require or assume one.
2. Set homepage URL to `https://github.com/CENorthrup/Claytron9000` and description to
   `Independent factory agent reviews with model and commit provenance`.
   Leave callback/setup URLs blank. Leave user authorization, device flow and
   webhook **Active** disabled. No OAuth flow or webhook events are needed.
3. Repository permissions: **Pull requests: Read and write**, **Metadata: Read-only**
   (automatic). All other repository, organization and account permissions: none.
   Select **Only on this account** for where the App can be installed.
   Do not give the App branch-protection bypass rights.
4. Create the App and record its **App ID** (not Client ID) and its public slug
   at `https://github.com/apps/<app-slug>`.
5. Under **Install App**, select `CENorthrup`, **Only select repositories**, and
   **devenv** only for the initial acceptance target. Record the installation ID from the resulting installation
   settings URL, `https://github.com/settings/installations/<installation-id>`.
6. Generate a private key in the App's settings. Store the downloaded PEM outside
   every checkout, at `~/.config/claytron-reviewer/private-key.pem` with mode `600`.
   Use a mode `700` directory. Do not paste the PEM, JWT or installation token into
   chat, a PR, shell arguments or logs. Do not distribute it to arbitrary VMs.
7. Create `~/.config/claytron-reviewer/config.json`, substituting the real
   values. These are local values and do not belong in Git:

   ```json
   {
     "app_id": "<App ID>",
     "installation_id": "<Installation ID>",
     "app_slug": "<app-slug>",
     "private_key_path": "/absolute/path/to/.config/claytron-reviewer/private-key.pem"
   }
   ```

   Set config mode `600` too. This is a schema example with placeholders, not a
   registered App. `app_slug` is the identity this command pins: the tool verifies
   the App it authenticated as, and the bot login that authored the review, both
   match it. No slug is compiled in, so the same mechanism serves whatever
   Claytron9000 registers. The command rejects a missing or malformed slug,
   missing/invalid IDs and unsafe key permissions, ownership, symlinks and
   unreadable keys. PEM ignore rules are a guard against accidents,
   not permission to store credentials in a checkout.

Whenever a live review is eventually attempted, run `gh auth status` and
`gh api user --jq .login` before and after, using the normal human environment. Do not change `gh` authentication. The reviewer
does not invoke `gh`, read its credentials, set `GH_TOKEN`, or write its configuration.

## Submit a completed review

Dependencies: Python 3 and OpenSSL. The `devenv` thin-client environment
provisions them, but Claytron9000 only requires them to be available in the
operator's environment and has no filesystem dependency on `devenv`.
No third-party Python packages or network access are needed for the test suite.

Review the code first and record its full head SHA. Save the actual findings in a
UTF-8 file; then submit using the exact model/profile, effort and harness used:

```bash
just submit-agent-review \
  --repo CENorthrup/devenv --pr 123 --event COMMENT \
  --body-file /tmp/review.md \
  --model 'ACTUAL MODEL/PROFILE' --effort 'ACTUAL EFFORT' \
  --harness 'ACTUAL HARNESS' --head-sha FULL_40_CHARACTER_REVIEWED_SHA
```

The uppercase example values must be replaced by real review metadata. The tool
requires caller-supplied values; it cannot infer or attest which model reasoned
about the code. From outside the checkout use its absolute
`scripts/submit-agent-review.sh` path via `bash`. Use `--config /absolute/path` to
select a different local config. Repository scope is explicitly limited to
`CENorthrup/devenv` for the initial acceptance target; adding repositories
requires a deliberate future change.

Optional `--comments-file /tmp/findings.json` attaches inline findings to the same
review. This is a JSON array using GitHub's diff coordinates:

```json
[
  {"path": "scripts/example.sh", "line": 10, "side": "RIGHT", "body": "Explain the finding and its impact."}
]
```

For a multiline finding include `start_line` and `start_side`. GitHub validates
whether the location belongs to the reviewed diff. `LEFT` refers to removed code,
`RIGHT` to added/current code. No unrelated issue comments are posted.

Every body gets this stable, generated block:

```text
Agent review provenance

Role: reviewer
Model: <caller-provided exact model/profile>
Effort: <caller-provided effort>
Harness: <caller-provided harness>
Reviewed commit: <full reviewed SHA>
```

The App signs an RS256 JWT, verifies its own slug and the configured installation,
and mints a short-lived token limited to this repository and PR write permission.
Credentials exist only in process memory and pipes; signing reads the original
restricted key through an open file descriptor. No credential temp files are
created, on either success or failure. API redirects are refused.

All events check the live PR head immediately before submission and send explicit
`commit_id`. A stale SHA fails and requires another review. GitHub does not offer
an atomic compare-and-submit operation: a push racing this last check can still
occur, but the review stays bound to the explicitly reviewed commit, never the
newer one. Human merge decisions must use reviews of the current head.

`COMMENT` means no explicit blocking/approval decision is appropriate.
`REQUEST_CHANGES` means findings should block merge. `APPROVE` means an independent
review found no remaining blockers for that exact revision. Passing tests alone
is not a review, and a new commit does not automatically clear blocking findings.

## Rotation and diagnosis

Generate another key in the same App's settings. Replace the local PEM atomically
with the new file, keeping ownership and mode `600`; then validate it with a real,
justified review and revoke the old key in GitHub. Revoke a compromised key
immediately. Keep downloads and backups out of repositories; delete obsolete
local copies deliberately. No installation tokens are stored to rotate locally.

For failures, check local config, key ownership/mode, RSA PEM format and OpenSSL.
For HTTP 401 check the App ID/key pairing and system clock. For 403/404 check
installation ID, selected repository access, suspension and PR write permission.
For 422 check inline diff locations, event and review body. API errors suppress
raw response bodies to avoid exposing credentials. On transport failures or an
unexpected response after POST, inspect the PR before retrying: GitHub may have
accepted the review. The tool never retries a review automatically.

Verify installation scope at <https://github.com/settings/installations>: the
reviewer installation must show **Only select repositories: devenv**, not all
repositories. The command verifies selected-repository installation mode and
restricts each minted token to `devenv`; the settings page is the audit for other
repositories deliberately or accidentally added to the installation itself.

## Completion scope and deferred live validation

**This work is complete without a GitHub App.** Everything testable without a live
App is implemented and covered offline: argument and provenance validation, JWT
signing, App/installation/permission pinning, token scoping, the commit-bound head
check, credential-safe failures and human `gh` auth preservation. No App is
registered, no identity is claimed, and no bot review or approval was manufactured.

Live validation is **deferred to the Claytron9000 GitHub identity/bootstrap work**
and is explicitly not a gate on this repository's reviewer tooling. When
`claytron-reviewer` exists and is installed, that work should:

1. Record the normal human `gh` identity without copying tokens into evidence.
2. Have an independent reviewer examine a real PR and record the model/profile,
   effort, harness and full head SHA.
3. Submit that justified review through this command with `app_slug` set to the
   registered slug. Verify the author is `<app-slug>[bot]`, the generated
   provenance is accurate, and `commit_id` matches the reviewed SHA. Save only the
   review URL and nonsecret evidence.
4. Establish a real non-`COMMENT` event (`APPROVE` or `REQUEST_CHANGES`) justified
   by that independent review. A test approval of unreviewed code is prohibited.
5. Verify the normal human identity again.

Until then, agent reviews continue to be posted under the human account, and the
provenance block in the review body remains the way to tell who reasoned about the
code.

### Offline validation

Run `just test-agent-review`. The tests generate disposable RSA keys in temporary
directories whose paths contain shell-sensitive characters, mock GitHub, and
exercise events, provenance, signing, stale heads, configured-identity and
permission failures, secret-safe errors and human auth preservation. Machine
bootstrap and environment tests remain in `devenv`.

GitHub references: [App registration](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/registering-a-github-app),
[App JWTs](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-json-web-token-jwt-for-a-github-app),
[installation tokens](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app),
[review API](https://docs.github.com/en/rest/pulls/reviews).
