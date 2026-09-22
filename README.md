# Claytron9000

Claytron9000 is the software-factory control plane. It owns factory workflow,
agent roles and identities, project lifecycle, planning integration and human
gates. Environment provisioning remains owned by the separate `devenv`
repository.

This initial repository contains the reviewer submission mechanism extracted
from `devenv`. It uses a GitHub App installation token and records the actual
model, effort, harness and reviewed commit in each review. It does not create
or install GitHub Apps, orchestrate reviews, merge pull requests, or implement
the future worker identity.

## Relationship with devenv

`devenv` provisions the machine capability used here: Python 3 and OpenSSL on
the thin client. Claytron9000 does not locate a sibling `devenv` checkout and
does not depend on exe.dev, GStack, Hermes or any other environment provider.
Install the Claytron9000 checkout wherever the control-plane operator prefers;
ensure Python 3 and OpenSSL are available through the environment provider.
GStack may be provisioned as an environment capability by `devenv`; its use and
workflow integration belong here and are not implemented in this split.

The durable GitHub identity is a factory role. Model provenance is the execution
profile used for a particular review. The future `claytron-worker` identity must
remain separate from `claytron-reviewer`.

## Commands

Run `just test-agent-review` for the offline suite. Once the reviewer App has
been registered and installed, use `just submit-agent-review` with the exact
model/profile, effort, harness and full reviewed commit SHA. Registration,
installation and live non-`COMMENT` acceptance are intentionally deferred to
the future Claytron9000 identity/bootstrap work.

See [docs/github-reviewer.md](docs/github-reviewer.md) for the interface,
configuration, permissions, key rotation, diagnosis and required human setup.
