#!/usr/bin/env bash
# kennelbox installer
# Usage:
#   ./install.sh                 interactive
#   ./install.sh --yes           accept all prompts
#   ./install.sh --no-firejail   skip firejail apt step

set -e

# ---------------------------------------------------------------------------
# Colours (fall back gracefully if tput not available)
# ---------------------------------------------------------------------------
if command -v tput &>/dev/null && tput colors &>/dev/null; then
  RED=$(tput setaf 1); GREEN=$(tput setaf 2); YELLOW=$(tput setaf 3)
  CYAN=$(tput setaf 6); BOLD=$(tput bold); RESET=$(tput sgr0)
else
  RED=""; GREEN=""; YELLOW=""; CYAN=""; BOLD=""; RESET=""
fi

info()    { echo "${CYAN}  [info]${RESET}  $*"; }
success() { echo "${GREEN}  [ ok ]${RESET}  $*"; }
warn()    { echo "${YELLOW}  [warn]${RESET}  $*"; }
fail()    { echo "${RED}  [fail]${RESET}  $*"; exit 1; }

confirm() {
  # confirm <question>  — skipped with --yes
  if [[ "$OPT_YES" == "1" ]]; then return 0; fi
  read -r -p "${BOLD}$1 [Y/n]${RESET} " ans
  case "$ans" in [nN]*) return 1 ;; *) return 0 ;; esac
}

# ---------------------------------------------------------------------------
# Parse args
# ---------------------------------------------------------------------------
OPT_YES=0
OPT_NO_FIREJAIL=0
for arg in "$@"; do
  case "$arg" in
    --yes|-y)          OPT_YES=1 ;;
    --no-firejail)     OPT_NO_FIREJAIL=1 ;;
    --help|-h)
      echo "Usage: $0 [--yes] [--no-firejail]"
      exit 0
      ;;
    *)
      fail "Unknown argument: $arg  (use --help)"
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
echo ""
echo "${CYAN}${BOLD}╔══════════════════════════════╗"
echo "║  KENNELBOX  //  v0.1.0       ║"
echo "║  installer                   ║"
echo "╚══════════════════════════════╝${RESET}"
echo ""

# ---------------------------------------------------------------------------
# Step 1: OS check
# ---------------------------------------------------------------------------
info "Checking operating system..."
if [[ "$(uname -s)" != "Linux" ]]; then
  fail "kennelbox requires Linux. Detected: $(uname -s)"
fi
success "Linux detected"

# ---------------------------------------------------------------------------
# Step 2: Python version check (>= 3.10)
# ---------------------------------------------------------------------------
info "Checking Python version (3.10+ required)..."

PYTHON=""
for candidate in python3 python; do
  if command -v "$candidate" &>/dev/null; then
    ver=$("$candidate" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    major=$(echo "$ver" | cut -d. -f1)
    minor=$(echo "$ver" | cut -d. -f2)
    if [[ "$major" -ge 3 && "$minor" -ge 10 ]]; then
      PYTHON="$candidate"
      break
    fi
  fi
done

if [[ -z "$PYTHON" ]]; then
  fail "Python 3.10+ not found. Install it from https://python.org or via your package manager."
fi

PY_VERSION=$("$PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")')
success "Found $PYTHON $PY_VERSION"

# ---------------------------------------------------------------------------
# Step 3: firejail (optional)
# ---------------------------------------------------------------------------
if [[ "$OPT_NO_FIREJAIL" == "1" ]]; then
  warn "Skipping firejail install (--no-firejail). Commands run with CWD restriction only."
elif command -v firejail &>/dev/null; then
  success "firejail already installed: $(firejail --version 2>&1 | head -1)"
elif command -v apt-get &>/dev/null; then
  echo ""
  warn "firejail not found. It provides full kernel-level sandboxing."
  if confirm "  Install firejail via apt-get (requires sudo)?"; then
    info "Installing firejail..."
    if sudo apt-get install -y firejail; then
      success "firejail installed"
    else
      warn "apt-get install failed. Continuing without firejail (CWD-only restriction)."
    fi
  else
    warn "Skipping firejail. Continuing without full sandboxing."
  fi
else
  warn "apt-get not available. Install firejail manually for full sandboxing."
  warn "  https://github.com/netblue30/firejail"
fi

# ---------------------------------------------------------------------------
# Step 4: pip install
# ---------------------------------------------------------------------------
info "Installing kennelbox Python package..."

# Find pip
PIP=""
for candidate in pip pip3; do
  if command -v "$candidate" &>/dev/null; then
    PIP="$candidate"
    break
  fi
done

if [[ -z "$PIP" ]]; then
  # Try python -m pip as fallback
  if "$PYTHON" -m pip --version &>/dev/null 2>&1; then
    PIP="$PYTHON -m pip"
  else
    fail "pip not found. Install pip: $PYTHON -m ensurepip --upgrade"
  fi
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

info "Running: $PIP install -e \"$SCRIPT_DIR\""
if $PIP install -e "$SCRIPT_DIR" --quiet; then
  success "kennelbox installed"
else
  fail "pip install failed. Check the output above for details."
fi

# ---------------------------------------------------------------------------
# Step 5: Verify
# ---------------------------------------------------------------------------
info "Verifying installation..."
if command -v kennelbox &>/dev/null; then
  success "kennelbox is on PATH: $(command -v kennelbox)"
else
  warn "kennelbox not found on PATH. You may need to add pip's bin dir to PATH:"
  warn "  export PATH=\"\$HOME/.local/bin:\$PATH\""
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "${BOLD}${GREEN}╔══════════════════════════════════════╗"
echo "║  kennelbox installation complete!   ║"
echo "╚══════════════════════════════════════╝${RESET}"
echo ""
echo "  Python:    ${PY_VERSION}"
echo "  firejail:  $(command -v firejail &>/dev/null && echo "$(firejail --version 2>&1 | head -1)" || echo "not installed (software CWD restriction only)")"
echo ""
echo "  ${BOLD}Next steps:${RESET}"
echo "    cd /path/to/your/project"
echo "    kennelbox init"
echo "    kennelbox run --agent <name>"
echo ""
