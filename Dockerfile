# BTC permits alternatives with RTX 5090 / sm_120 support. Keep the newer Qwen3.5 stack.
# Recommended v0.10.1.1 is not a mandatory base; verify this alternative on both GPUs.
ARG BASE_IMAGE=vllm/vllm-openai:v0.32.0@sha256:befccf0e7e4089aa23926bb6f887a85c0d46c2a99e6de5c5ffdf5cb7957c5b34
FROM ${BASE_IMAGE}
ENTRYPOINT []
ENV PYTHONUNBUFFERED=1 PYTHONHASHSEED=0 \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1 \
    HF_HUB_DISABLE_TELEMETRY=1 VLLM_NO_USAGE_STATS=1 DO_NOT_TRACK=1 \
    HF_HOME=/tmp/huggingface XDG_CACHE_HOME=/tmp/cache TMPDIR=/tmp \
    TOKENIZERS_PARALLELISM=false CUBLAS_WORKSPACE_CONFIG=:4096:8 NCCL_P2P_DISABLE=1
WORKDIR /code
COPY docker/requirements.txt /code/docker/requirements.txt
RUN python3 -m pip install --no-cache-dir -r /code/docker/requirements.txt \
    && python3 -m pip check
# Separate weight layer; changing source does not invalidate the weight copy.
COPY models/Qwen3.5-9B /code/models/Qwen3.5-9B
COPY app /code/app
COPY training /code/training
COPY evaluation /code/evaluation
COPY schemas /code/schemas
COPY docker /code/docker
COPY predict.sh /code/predict.sh
COPY docker/README.md /code/README.md
COPY docker/training_code /code/training_code
COPY training /code/training_code/src
COPY dataset/train /code/training_code/data/train
COPY docker/requirements.txt /code/requirements.txt
RUN chmod 0555 /code/predict.sh /code/docker/run.sh \
    && python3 -m docker.verify_model /code/models/Qwen3.5-9B \
    && python3 -c "import torch, vllm, transformers; assert tuple(map(int,torch.version.cuda.split('.')[:2])) >= (12,8); print(torch.__version__, vllm.__version__, transformers.__version__)" \
    && python3 -m pip freeze > /code/docker/environment.lock.txt \
    && cp /code/docker/environment.lock.txt /code/training_code/requirements.txt \
    && python3 -m docker.build_artifacts
CMD ["bash", "/code/predict.sh"]
