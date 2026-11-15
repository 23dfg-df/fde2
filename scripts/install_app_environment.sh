#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p evidence logs
python3 -m venv --without-pip .venv-app
bootstrap_python="${OCR_BOOTSTRAP_PYTHON:-python3}"
"$bootstrap_python" -m pip --python "$PWD/.venv-app/bin/python" install --index-url "${PIP_INDEX_URL:-https://pypi.org/simple}" pip -r requirements-app.txt
.venv-app/bin/python -m pip check
.venv-app/bin/python -m pip freeze > evidence/app-requirements.freeze.txt
