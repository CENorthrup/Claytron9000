set shell := ["bash", "-euo", "pipefail", "-c"]
set positional-arguments

default:
    @just --list

# Submit one App-authenticated, commit-bound review.
submit-agent-review *args:
    bash scripts/submit-agent-review.sh "$@"

# Exercise reviewer authentication and submission offline with generated test keys.
test-agent-review:
    bash tests/agent-review.sh
    python3 -B tests/identity.py

setup-github *args:
    python3 scripts/claytron.py setup github {{args}}

worker *args:
    python3 scripts/worker.py {{args}}
