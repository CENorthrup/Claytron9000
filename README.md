# Claytron9000

Claytron9000 is the software-factory control plane. It owns factory workflow,
agent roles and identities, project lifecycle, planning integration and human
gates. Environment provisioning remains owned by the separate `devenv`
repository.

This repository contains the reviewer submission mechanism extracted from
`devenv`, reproducible definitions for the worker and reviewer GitHub Apps, and
the human-gated setup needed to create and install them. It records the actual
model, effort, harness and reviewed commit in each review. Human merge authority
remains outside both App identities.

## Relationship with devenv

`devenv` provisions the machine capability used here: Python 3 and OpenSSL on
the thin client. Claytron9000 does not locate a sibling `devenv` checkout and
does not depend on exe.dev, GStack, Hermes or any other environment provider.
Install the Claytron9000 checkout wherever the control-plane operator prefers;
ensure Python 3 and OpenSSL are available through the environment provider.
GStack may be provisioned as an environment capability by `devenv`; its use and
workflow integration belong here and are not implemented in this split.

The durable GitHub identities are separate factory roles. Model provenance is
the execution profile used for a particular review. `claytron-worker` can write
implementation branches and PRs; `claytron-reviewer` can submit independent
reviews without Contents write access.

## Commands

Run `just test-agent-review` for the offline suite. Create each App through the
human-gated setup, for example:

```bash
python3 scripts/claytron.py setup github --role worker --account YOUR_ACCOUNT \
  --repository YOUR_ACCOUNT/Claytron9000 --repository YOUR_ACCOUNT/devenv
python3 scripts/claytron.py setup github --role reviewer --account YOUR_ACCOUNT \
  --repository YOUR_ACCOUNT/Claytron9000 --repository YOUR_ACCOUNT/devenv
```

Use `just submit-agent-review` with the exact model/profile, effort, harness and
full reviewed commit SHA. The worker interface is exposed through
`python3 scripts/worker.py`; it refuses default-branch pushes and merge
operations.

See [docs/github-identities.md](docs/github-identities.md) for the permissions,
human gates, credential storage, installation-scope changes, worker safeguards,
reviewer independence, live acceptance and deferred work. See
[docs/github-reviewer.md](docs/github-reviewer.md) for the review interface.
