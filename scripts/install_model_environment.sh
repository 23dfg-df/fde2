#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p evidence logs
python3 -m venv --without-pip .venv-model
bootstrap_python="${OCR_BOOTSTRAP_PYTHON:-python3}"
"$bootstrap_python" -m pip --python "$PWD/.venv-model/bin/python" install --index-url "${PIP_INDEX_URL:-https://pypi.org/simple}" pip 'vllm==0.18.1' 'pillow>=10.0.0'
.venv-model/bin/python -m pip check
.venv-model/bin/python -m pip freeze > evidence/model-requirements.freeze.txt
.venv-model/bin/python - <<'PY'
import importlib.metadata, json
from vllm.model_executor.models.registry import ModelRegistry
supported = 'HunYuanVLForConditionalGeneration' in ModelRegistry.get_supported_archs()
versions = {k: importlib.metadata.version(k) for k in ['vllm','torch','transformers']}
print(json.dumps({'versions': versions, 'HunYuanVL_supported': supported}))
assert supported
PY
