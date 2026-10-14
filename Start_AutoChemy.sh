#!/usr/bin/env bash

set -u

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
USER_DATA="$ROOT/AutoChemy_User_Data"
CFG="$USER_DATA/.autochemy_ready"
REQ="$ROOT/requirements.txt"

mkdir -p "$USER_DATA"

find_python() {
    local candidate

    for candidate in \
        "$ROOT/.venv/bin/python" \
        "$ROOT/.venv/bin/python3" \
        "$(command -v python3.12 2>/dev/null || true)" \
        "$(command -v python3.11 2>/dev/null || true)" \
        "$(command -v python3.10 2>/dev/null || true)" \
        "$(command -v python3 2>/dev/null || true)" \
        "$(command -v python 2>/dev/null || true)"; do
        [[ -n "$candidate" && -x "$candidate" ]] || continue
        if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 10) else 1)' >/dev/null 2>&1; then
            PYTHON_CMD="$candidate"
            return 0
        fi
    done

    return 1
}

PYTHON_CMD=""

# Reuse the interpreter selected during an earlier setup.
if [[ -f "$CFG" ]]; then
    IFS= read -r PYTHON_CMD < "$CFG" || PYTHON_CMD=""
    if [[ -z "$PYTHON_CMD" || ! -x "$PYTHON_CMD" ]] || \
       ! "$PYTHON_CMD" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 10) else 1)' >/dev/null 2>&1; then
        PYTHON_CMD=""
        rm -f "$CFG"
    fi
fi

if [[ -z "$PYTHON_CMD" ]] && ! find_python; then
    echo "Python 3.10+ is not installed."
    echo "Install it from https://www.python.org/downloads/ and run this script again."
    exit 1
fi

if ! "$PYTHON_CMD" -c 'import pandas, numpy, matplotlib, PIL' >/dev/null 2>&1; then
    echo "Some Python libraries are missing."
    read -r -p "Install them now? (needs internet) [y/N] " answer
    case "$answer" in
        y|Y|yes|Yes|YES)
            echo "Launching setup..."
            if ! "$PYTHON_CMD" "$ROOT/install_deps.py" "$REQ"; then
                echo "Install failed. Check internet and try again."
                exit 1
            fi
            ;;
        *)
            echo "Cancelled. Run Start AutoChemy.sh again when ready."
            exit 1
            ;;
    esac

    if ! "$PYTHON_CMD" -c 'import pandas, numpy, matplotlib, PIL' >/dev/null 2>&1; then
        echo "Libraries are still missing after installation."
        exit 1
    fi
fi

printf '%s\n' "$PYTHON_CMD" > "$CFG"
echo "Setup complete. Opening AutoChemy..."
cd "$ROOT"
"$PYTHON_CMD" "$ROOT/autochemy.py" &