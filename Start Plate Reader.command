#!/bin/bash

set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
cd "$PROJECT_DIR"

export PLATE_READER_ENV="development"
export PLATE_READER_STORAGE_MODE="fake-cloud"
export PLATE_READER_DATABASE_PATH=".data/plate-reader.sqlite"
export ARROW_DEFAULT_MEMORY_POOL="system"

echo "Starting Plate Reader Database…"
echo "Project: $PROJECT_DIR"
echo "Database: $PROJECT_DIR/.data/plate-reader.sqlite"
echo

if [[ -x "$PROJECT_DIR/.venv/bin/python" ]]; then
    PYTHON="$PROJECT_DIR/.venv/bin/python"
else
    UV=""
    for candidate in \
        "$PROJECT_DIR/.tools/uv-venv/bin/uv" \
        "/opt/homebrew/bin/uv" \
        "/usr/local/bin/uv"
    do
        if [[ -x "$candidate" ]]; then
            UV="$candidate"
            break
        fi
    done

    if [[ -z "$UV" ]] && command -v uv >/dev/null 2>&1; then
        UV="$(command -v uv)"
    fi

    if [[ -z "$UV" ]]; then
        echo "ERROR: Python environment is missing and uv could not be found."
        echo "Install uv from https://docs.astral.sh/uv/ and try again."
        echo
        read -r -p "Press Return to close this window…" _
        exit 1
    fi

    echo "Preparing the locked Python environment for the first run…"
    UV_CACHE_DIR="$PROJECT_DIR/.tools/uv-cache" "$UV" sync --all-groups --frozen
    PYTHON="$PROJECT_DIR/.venv/bin/python"
fi

echo "The application will open at http://localhost:8501"
echo "Keep this window open while using the application."
echo "Press Control-C here to stop it."
echo

"$PYTHON" -m streamlit run "$PROJECT_DIR/app.py" \
    --server.headless=false \
    --server.address=localhost \
    --server.port=8501

echo
echo "Plate Reader Database stopped."
read -r -p "Press Return to close this window…" _
