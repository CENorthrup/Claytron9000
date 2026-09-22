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
