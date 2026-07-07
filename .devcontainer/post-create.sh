#!/usr/bin/env bash
# Post-create setup for the Foundry Agent Framework Workshop devcontainer.
#
# Goals:
#   - Reliable for in-person workshops: fail loudly when something important
#     (azd install, dependency install for an existing requirements file) breaks.
#   - Safe to re-run manually.
#   - Tolerate missing optional files (don't abort).

set -u
set -o pipefail

log() {
  printf '\n\033[1;36m[post-create]\033[0m %s\n' "$*"
}

warn() {
  printf '\n\033[1;33m[post-create:warn]\033[0m %s\n' "$*"
}

err() {
  printf '\n\033[1;31m[post-create:error]\033[0m %s\n' "$*" >&2
}

# Required-step failure tracking. Anything pushed here causes a non-zero exit
# and a clearly-failed banner instead of the "ready" banner.
FAILURES=()
record_failure() {
  FAILURES+=("$1")
  err "$1"
}

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

log "Repository root: $REPO_ROOT"

############################################
# 1. Install Azure Developer CLI (azd)
############################################
if command -v azd >/dev/null 2>&1; then
  log "azd already installed: $(azd version 2>/dev/null | head -n1)"
else
  log "Installing Azure Developer CLI (azd) via official install script..."
  if ! command -v curl >/dev/null 2>&1; then
    record_failure "curl is required to install azd but is not available."
  elif curl -fsSL https://aka.ms/install-azd.sh | bash; then
    log "azd installation completed."
    # The installer may drop azd into ~/.local/bin which isn't always on PATH
    # for the rest of this script. Make sure subsequent commands can find it.
    if ! command -v azd >/dev/null 2>&1 && [ -x "$HOME/.local/bin/azd" ]; then
      export PATH="$HOME/.local/bin:$PATH"
    fi
    if ! command -v azd >/dev/null 2>&1; then
      record_failure "azd installer ran but 'azd' is still not on PATH."
    fi
  else
    record_failure "azd installation failed. Re-run: curl -fsSL https://aka.ms/install-azd.sh | bash"
  fi
fi

############################################
# 2. Install uv
############################################
if command -v uv >/dev/null 2>&1; then
  log "uv already installed: $(uv --version 2>/dev/null)"
else
  log "Installing uv via official install script..."
  if ! command -v curl >/dev/null 2>&1; then
    warn "curl is required to install uv but is not available. Skipping uv install."
  elif curl -LsSf https://astral.sh/uv/install.sh | sh; then
    log "uv installation completed."
    # The installer drops uv into ~/.local/bin; ensure subsequent commands can find it.
    if ! command -v uv >/dev/null 2>&1 && [ -x "$HOME/.local/bin/uv" ]; then
      export PATH="$HOME/.local/bin:$PATH"
    fi
    if command -v uv >/dev/null 2>&1; then
      log "uv is on PATH: $(uv --version 2>/dev/null)"
    else
      warn "uv installer ran but 'uv' is still not on PATH. pip will be used as fallback."
    fi
  else
    warn "uv installation failed. pip will be used as fallback."
  fi
fi

############################################
# 3. Install Python dependencies
############################################
if command -v uv >/dev/null 2>&1; then
  if [ -d ".venv" ]; then
    log "Using existing virtual environment at .venv"
  else
    log "Creating virtual environment at .venv (uv venv) ..."
    uv venv .venv || record_failure "Failed to create .venv with uv venv."
  fi
else
  record_failure "uv is required to install Python dependencies but is not available."
fi

log "Installing .workshop/scripts/requirements.txt ..."
uv pip install -r .workshop/scripts/requirements.txt || record_failure "Failed to install .workshop/scripts/requirements.txt."

# travel_assistant/requirements.txt only exists after attendees run the
# "Initialize Workshop" step (which copies .workshop/step_files/00/* into travel_assistant/).
# If the Codespace is created before that, use the step 00 file as the baseline.
if [ -f "travel_assistant/requirements.txt" ]; then
  log "Installing travel_assistant/requirements.txt ..."
  uv pip install -r travel_assistant/requirements.txt || record_failure "Failed to install travel_assistant/requirements.txt."
elif [ -f ".workshop/step_files/00/requirements.txt" ]; then
  log "travel_assistant/requirements.txt not found; using .workshop/step_files/00/requirements.txt as the workshop baseline."
  uv pip install -r .workshop/step_files/00/requirements.txt || record_failure "Failed to install .workshop/step_files/00/requirements.txt."
else
  warn "No travel_assistant or .workshop/step_files/00 requirements found; skipping workshop dependency install."
fi

############################################
# 4. Install the Azure Skills plugin for GitHub Copilot CLI
############################################
# The GitHub Copilot CLI itself is installed declaratively via the
# `ghcr.io/devcontainers/features/copilot-cli` devcontainer feature; Node.js
# (needed by the Azure/Foundry MCP servers the plugin wires in) comes from the
# `node` feature. Here we register the marketplace and install the plugin.
# This is best-effort: a failure here should not block the workshop. Commands
# are bounded with `timeout` (when available); on failure any captured output is
# surfaced and timeouts are reported explicitly, so a hang or error is visible
# rather than silently swallowed.
if command -v copilot >/dev/null 2>&1; then
  # Use an array so an empty value expands to nothing and word-splitting is
  # not affected by a customized IFS.
  TIMEOUT=()
  command -v timeout >/dev/null 2>&1 && TIMEOUT=(timeout 180)

  report_plugin_failure() {
    # $1 = exit status, $2 = action label, $3 = manual command hint
    if [ "$1" -eq 124 ]; then
      warn "$2 timed out after 180s. Inside Copilot CLI, run: $3"
    else
      warn "$2 failed. Inside Copilot CLI, run: $3"
    fi
    [ -n "$plugin_out" ] && printf '%s\n' "$plugin_out"
  }

  log "Registering the Azure Skills marketplace for GitHub Copilot CLI ..."
  if plugin_out="$("${TIMEOUT[@]}" copilot plugin marketplace add microsoft/azure-skills 2>&1)"; then
    log "Installing the Azure Skills plugin (azure@azure-skills) ..."
    if plugin_out="$("${TIMEOUT[@]}" copilot plugin install azure@azure-skills 2>&1)"; then
      log "Azure Skills plugin installed."
    else
      report_plugin_failure "$?" "Azure Skills plugin install" "/plugin install azure@azure-skills"
    fi
  else
    report_plugin_failure "$?" "Azure Skills marketplace registration" "/plugin marketplace add microsoft/azure-skills"
  fi
else
  warn "GitHub Copilot CLI ('copilot') not found on PATH; skipping Azure Skills plugin install. Rebuild the container so the copilot-cli feature is applied."
fi

############################################
# 5. Print tool versions
############################################
log "Installed tool versions:"

print_version() {
  local name="$1"
  shift
  if command -v "$name" >/dev/null 2>&1; then
    local out
    if out="$("$@" 2>&1 | head -n1)"; then
      printf '  - %-8s %s\n' "$name:" "$out"
    else
      printf '  - %-8s (failed to read version)\n' "$name:"
    fi
  else
    printf '  - %-8s NOT INSTALLED\n' "$name:"
  fi
}

print_version python  python --version
print_version uv      uv --version
print_version az      az version --output tsv --query '"azure-cli"'
print_version azd     azd version
if command -v az >/dev/null 2>&1; then
  bicep_out="$(az bicep version 2>&1 | head -n1)"
  printf '  - %-8s %s\n' "bicep:" "${bicep_out:-(not installed)}"
else
  printf '  - %-8s NOT INSTALLED\n' "bicep:"
fi
print_version git     git --version
print_version gh      gh --version
print_version node    node --version
print_version copilot copilot --version

############################################
# 6. Final status / next steps
############################################
if [ "${#FAILURES[@]}" -gt 0 ]; then
  cat <<EOF

============================================================
 Devcontainer post-create FAILED (${#FAILURES[@]} issue(s))
EOF
  for f in "${FAILURES[@]}"; do
    printf '   - %s\n' "$f"
  done
  cat <<'EOF'

 Fix the issues above (or re-run: bash .devcontainer/post-create.sh)
 before starting the workshop.
============================================================
EOF
  exit 1
fi

cat <<'EOF'

============================================================
 Devcontainer ready 🎉

 Authentication is NOT automated. When you are ready, run:

   az login
   azd auth login

 Then continue with the workshop instructions in README.md.
============================================================
EOF
