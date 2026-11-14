#!/usr/bin/env bash
set -euo pipefail
cd /code
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1
export HF_HUB_DISABLE_TELEMETRY=1 VLLM_NO_USAGE_STATS=1 DO_NOT_TRACK=1
export PYTHONHASHSEED=0 CUBLAS_WORKSPACE_CONFIG=:4096:8
# Includes imports, weights, ingestion and inference. Leave 60 seconds for collection.
exec timeout --signal=TERM --kill-after=10s 21540s python3 -m docker.worker
