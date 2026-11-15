#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export OCR_MODEL_URL="${OCR_MODEL_URL:-http://127.0.0.1:18401/v1}"
export OCR_MODEL_NAME="tencent/HunyuanOCR"
export OCR_MODEL_REVISION="47644ecc4fc854efa4f505155158831f36773ee4"
export OCR_PHYSICAL_GPU=1
export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2
exec .venv-app/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 18400 --workers 1
