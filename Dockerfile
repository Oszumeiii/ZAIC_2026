# API development image: CPU only, no local model weights or CUDA dependencies.
FROM python:3.12-slim AS gemini

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    TEST_DIR=/data OUT_DIR=/output

WORKDIR /srv
COPY requirements-gemini.txt .
RUN pip install --no-cache-dir -r requirements-gemini.txt

COPY app ./app
COPY evaluation ./evaluation
COPY training ./training

CMD ["python", "-m", "app.main", "--backend", "gemini"]

# Offline GPU submission image. Last stage keeps the existing default build behavior.
FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu22.04 AS local

ENV PYTHONUNBUFFERED=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
    TEST_DIR=/data OUT_DIR=/output LOCAL_MODEL=/models/model

RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY app ./app
# COPY models/model /models/model
COPY evaluation ./evaluation
COPY training ./training

CMD ["python3", "-m", "app.main", "--backend", "local"]
