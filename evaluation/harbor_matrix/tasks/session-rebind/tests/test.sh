#!/usr/bin/env bash
set -euo pipefail

# Harbor's verifier contract is "run tests and write a scalar reward".
# We intentionally execute pytest from the copied gem-code checkout so
# every task reuses the same interpreter and dependency set prepared by
# the installed-agent setup step.
WORKSPACE_PATH="${HARBOR_WORKSPACE_PATH:-/workspace}"
TESTS_PATH="${HARBOR_TESTS_PATH:-/tests}"
VERIFIER_LOGS_PATH="${HARBOR_VERIFIER_LOGS_PATH:-/logs/verifier}"

cd "${WORKSPACE_PATH}"
export PYTHONPATH="${WORKSPACE_PATH}/evaluation_fixture/src${PYTHONPATH:+:$PYTHONPATH}"

if uv run pytest -q "${TESTS_PATH}/test_outputs.py"; then
  echo 1 > "${VERIFIER_LOGS_PATH}/reward.txt"
else
  echo 0 > "${VERIFIER_LOGS_PATH}/reward.txt"
  exit 1
fi
