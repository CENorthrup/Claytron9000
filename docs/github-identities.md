# GitHub identities and human-gated setup

Claytron9000 uses two separate GitHub Apps. The worker performs implementation
operations; the reviewer submits independent reviews. Neither App merges a pull
request, bypasses branch protection, administers repositories, manages secrets,
or changes repository settings.

## Role definitions

The non-secret definitions live in [`config/github-apps/`](../config/github-apps/).
They are inputs to GitHub's App Manifest flow and are safe to review and commit.
Each setup creates one App; run the setup separately for each role.

| App | Permissions | Purpose |
| --- | --- | --- |
| `claytron-worker` | Metadata: read; Contents: read/write; Pull requests: read/write | Create branches, push implementation commits, and create or update PRs. |
| `claytron-reviewer` | Metadata: read; Pull requests: read/write | Read PR state and submit commit-bound findings, comments, approvals, or change requests. |

The reviewer deliberately has no Contents permission. The reviewing agent gets
code through its execution environment; the identity that posts the review is
kept separate from the implementation identity. Both Apps use selected
repository installations, never an all-repositories default.

## Human-gated setup

Run setup once per role from the Claytron9000 checkout:

```bash
python3 scripts/claytron.py setup github \
  --role worker --account YOUR_ACCOUNT \
  --repository YOUR_ACCOUNT/Claytron9000 \
  --repository YOUR_ACCOUNT/devenv
```

Repeat with `--role reviewer`. `--repository` may be repeated for any selected
repositories; the example is only an acceptance fixture and is not compiled
into the implementation.

Claytron performs the setup around two unavoidable GitHub actions:

1. It builds the committed role manifest, starts a loopback-only callback, and
   presents a `HUMAN_GATE` describing the requested permissions. The user opens
   the local setup page and approves creation in GitHub. Claytron exchanges the
   one-time manifest code, stores the returned App ID, slug, and private key,
   and never prints the response.
2. Claytron presents a second `HUMAN_GATE` with the exact selected repository
   scope and the App settings URL. The registration callback redirects the same browser
   to that settings page (with a clickable fallback link), preserving the GitHub
   session used to register the App. The user clicks
   **Install App**, and chooses **Only select repositories** in GitHub. GitHub
   redirects to the loopback callback, and Claytron verifies the installation
   account, selected mode, exact repositories, and exact permissions before
   marking the role ready.

The gates are auditable JSON state records under
`~/.local/state/claytron9000/gates` with mode 700 directories and mode 600
files. A gate says what is blocked, why, what access is requested, the exact
human action, the completion check, and the next operation. A timeout or failed
callback is a failed gate, not a generic command error. The manifest exchange
must finish within GitHub's one-hour window, so an expired setup should be
started again.

The local callback listens only on `127.0.0.1`. It uses a random state value to
bind the registration callback to the setup process. No browser automation is
used; the browser performs GitHub's official registration and installation
pages, while Claytron prepares and verifies both sides.

## Credentials

App configuration is stored outside every checkout:

```text
~/.config/claytron9000/github-apps/
├── worker/
│   ├── config.json
│   └── private-key.pem
└── reviewer/
    ├── config.json
    └── private-key.pem
```

Directories are mode 700. Configuration and private keys are mode 600 or
stricter. The conversion response's PEM is written only to the role's private
key file; webhook secrets and client secrets are not needed by this CLI and are
not stored. JWTs and installation tokens remain in memory, and installation
tokens are short-lived. Tokens are not command-line arguments, logs, gate files,
or repository files. Human `gh` authentication is not read, changed, or
replaced.

To rotate a key, generate a replacement in the App settings, store it at the
role path with mode 600, run an identity check or justified operation, and then
revoke the old key in GitHub. If a key is exposed, revoke it immediately and
repeat setup or key replacement before retrying.

## Changing selected repositories

After an installation exists, use the installation settings human gate to add
or remove a repository:

```bash
python3 scripts/claytron.py github repository \
  --role worker --action add --repository YOUR_ACCOUNT/new-project \
  --config ~/.config/claytron9000/github-apps/worker/config.json
```

Use `--action remove` to remove a repository. Claytron opens the installation
settings, polls GitHub, and records the new exact selected scope only after the
permissions and account still match. It refuses to remove the final selected
repository. It never changes an installation to all repositories silently.

## Worker safeguards

The worker reads the actual repository `default_branch` through GitHub before a
branch check, push, or PR operation. `python3 scripts/worker.py` refuses direct
pushes to that branch, merge commands, and any command named for default-branch
pushes. It can push a non-default branch using a short-lived installation token
held in ephemeral Git configuration, and can create or update a PR. The worker
does not expose a merge command or request administration, Actions, workflow,
branch-protection, or secret permissions. Branch protection and rulesets remain
useful defense in depth but are not a correctness dependency.

Example checks and PR operation:

```bash
python3 scripts/worker.py --config ~/.config/claytron9000/github-apps/worker/config.json \
  --repo YOUR_ACCOUNT/Claytron9000 check-branch --branch feature/example
python3 scripts/worker.py --config ~/.config/claytron9000/github-apps/worker/config.json \
  --repo YOUR_ACCOUNT/Claytron9000 create-pr --head feature/example --base main \
  --title 'Example' --body 'Describe the implementation.'
```

The worker and reviewer configs must have different roles and permission sets.
The reviewer code rejects worker configuration and Contents write permission.

## Reviewer safeguards

The reviewer validates its configured App identity, installation ID, account,
selected mode, exact permission set, and requested repository. It mints a token
scoped to only that repository. Before posting, it checks the live PR head and
sends the exact reviewed SHA as `commit_id`. A changed head refuses submission.
Each review records the role, exact model/profile, effort, harness, and reviewed
SHA. `APPROVE` is available only as a real review result; tests use mocked
responses and never manufacture a GitHub approval.

## Live acceptance

After both Apps are created and installed, acceptance should use a temporary
non-default branch and PR. Verify the worker bot identity, selected repository
scope, exact worker permissions, non-default push, PR creation/update, and
refusal of default-branch and merge operations. Verify the reviewer bot identity,
exact reviewer permissions, independent commit-bound review, provenance, stale
head rejection, and unchanged human `gh` identity. Record only non-secret
identifiers and URLs. The human remains the only merge authority; auto-merge and
branch-protection bypass are not configured.

## Recovery and limitations

If a callback is interrupted, inspect the gate state without copying codes or
keys, then restart the role setup. If installation verification fails, inspect
the selected-repository setting and permissions, correct them in GitHub, and
rerun the installation-scope command. Unexpected POST results must be checked in
GitHub before retrying because the remote operation may have succeeded.

GitHub's official Manifest flow still requires the account owner to use the
browser for App creation and installation. It is available for Apps owned by a
personal account, while enterprise-owned Apps require a different registration
path; this implementation targets the current personal-account acceptance
fixture. Future provider-specific identity adapters, additional worker or
reviewer roles, project lifecycle, merge automation, Hermes, containers, model
routing, and GitHub Actions deployment remain outside this phase.

Official references: [App Manifest flow](https://docs.github.com/en/apps/sharing-github-apps/registering-a-github-app-from-a-manifest),
[creating an App from a manifest](https://docs.github.com/en/rest/apps/apps#create-a-github-app-from-a-manifest),
[installing an App](https://docs.github.com/en/apps/using-github-apps/installing-your-own-github-app),
[App permissions](https://docs.github.com/en/rest/authentication/permissions-required-for-github-apps),
and [installation tokens](https://docs.github.com/en/rest/apps/apps#create-an-installation-access-token-for-an-app).
