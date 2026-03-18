#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
.venv/bin/firecloud --root-dir "${FIRECLOUD_ROOT_DIR:-.firecloud}" run-api "$@"
