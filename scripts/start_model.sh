#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p evidence logs
export CUDA_VISIBLE_DEVICES="${OCR_PHYSICAL_GPU:-1}"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 MAX_JOBS=4
export VLLM_NO_USAGE_STATS=1 DO_NOT_TRACK=1
export CUDA_DEVICE_ORDER=PCI_BUS_ID
export CPATH="$PWD/.tools/python-dev/usr/include/python3.12:$PWD/.tools/python-dev/usr/include${CPATH:+:$CPATH}"
model_path="${OCR_MODEL_PATH:-$PWD/../models/HunyuanOCR-1.5}"
if [[ ! "$CUDA_VISIBLE_DEVICES" =~ ^[0-9]+$ ]]; then
  echo 'OCR_PHYSICAL_GPU 必须指定一张物理显卡的数字编号。' >&2
  exit 1
fi
free_mib=$(nvidia-smi --id="$CUDA_VISIBLE_DEVICES" --query-gpu=memory.free --format=csv,noheader,nounits)
if (( free_mib < 24576 )); then
  echo "GPU $CUDA_VISIBLE_DEVICES 可用显存不足 24 GiB，暂不启动本实验。" >&2
  exit 1
fi
if [[ ! -f "$model_path/download-manifest.json" ]]; then
  echo '请先完成固定版本模型下载与校验。' >&2
  exit 1
fi
overrides=$(python3 scripts/model_overrides.py "$model_path")
printf '%s\n' "$overrides" > evidence/model-runtime-overrides.json
exec .venv-model/bin/vllm serve "$model_path" \
  --served-model-name tencent/HunyuanOCR \
  --host 127.0.0.1 --port 18401 --dtype bfloat16 \
  --gpu-memory-utilization "${OCR_GPU_MEMORY_UTILIZATION:-0.60}" \
  --max-model-len 8192 --max-num-seqs 1 --max-num-batched-tokens 8192 \
  --limit-mm-per-prompt '{"image":1,"video":0}' \
  --hf-overrides "$overrides" \
  --enforce-eager --seed 42 --no-enable-log-requests
