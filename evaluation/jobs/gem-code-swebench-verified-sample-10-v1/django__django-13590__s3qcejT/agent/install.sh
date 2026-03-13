#!/usr/bin/env bash
set -euo pipefail

# The agent source is uploaded into `/installed-agent/gem_code_src` during the
# Harbor setup phase. Reading the path from an env var keeps the install script
# backend-agnostic: Docker environments use the canonical in-container path,
# while the custom local environment rewrites the variable to a host path.
cd "${HARBOR_GEM_CODE_ROOT:-/installed-agent/gem_code_src}"

# Harbor setups run in clean environments, so we install project dependencies as
# part of agent setup. `uv sync --locked` keeps evaluation environments aligned
# with the committed lockfile.
if ! command -v uv >/dev/null 2>&1; then
  # SWE-bench task images frequently provide Python but not `uv`. Installing it
  # into the user site-packages keeps the setup self-contained and avoids
  # assuming a particular base image package manager.
  if command -v python3 >/dev/null 2>&1; then
    python3 -m pip install --user uv
  else
    python -m pip install --user uv
  fi
  export PATH="$HOME/.local/bin:$PATH"
fi

# Harbor's SWE-bench verifier scripts also invoke `uv` after the agent phase,
# but they do so in a fresh shell that does not inherit the PATH adjustment
# above. We therefore publish the user-installed binaries into `/usr/local/bin`
# when possible so both the agent and the later verifier/parser steps resolve
# the same executable without depending on shell-specific PATH state.
if command -v uv >/dev/null 2>&1; then
  uv_bin="$(command -v uv)"
  if [ ! -x /usr/local/bin/uv ]; then
    ln -sf "$uv_bin" /usr/local/bin/uv
  fi
fi

if command -v uvx >/dev/null 2>&1; then
  uvx_bin="$(command -v uvx)"
  if [ ! -x /usr/local/bin/uvx ]; then
    ln -sf "$uvx_bin" /usr/local/bin/uvx
  fi
fi

uv sync --locked