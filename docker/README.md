# ZAIC 2026 — offline SQL worker

## Container contract

`/code/predict.sh` is the image entrypoint. BTC mounts the dataset at `/data`
read-only and a writable result directory at `/result`. Expected input:
`questions.jsonl`, `rulebook.md`, `calendar.csv`, and `data/<business>/`.
The worker never reads `answers.jsonl`. No data, manifests, questions, answer
files or caches from public/private test are embedded in the image. The provided
train split is bundled separately under `/code/training_code/data/train` for verification.

Outputs:
- `/result/submission.jsonl`: one valid row per input id, initially abstain;
  atomically replaced after **every completed question**, in input order.
- `/result/logs/question_000000.json`, etc.: `id`, `answer`, integer `elapsed_ms`,
  `thinking`, `code` and `output`, plus full model calls, SQL execution traces,
  status/seed/business review/consensus. Each file contains one JSON record.
  Business questions enter a batch before source preparation; elapsed_ms runs
  from that point through the final answer, including shared ingestion and waits.
  Model loading is separate. Pending questions have abstain and elapsed 0.
  `thinking` is null when no reasoning was generated; otherwise it contains
  verbatim reasoning strings in call order, including shared manifest calls.
  Raw generated responses are also retained without truncation.
- `/result/logs/run.json`: configuration and frozen model inventory.
- `/result/logs/completion.json` or `fatal.json`: completion/failure information.

Temporary databases and manifests live in a fresh directory under `/tmp`, never
in `/data`. Interrupted in-flight questions retain their previous abstain output;
completed answers are preserved. The outer watchdog ends at 21,540 seconds plus
at most 10 seconds for shutdown; the cooperative budget is 21,300 seconds,
including model loading, verification, ingestion and inference. A time budget
prevents exceeding the limit; it does not prove all questions finish in six hours.

## Declared inference model

Only **Qwen/Qwen3.5-9B**, original floating-point weights, is used for manifests,
question rewriting, SQL and review. No embedding/reranker/classifier model or
remote API is used. The model card describes a 9B language model with a vision
encoder. The packaging checker counts **all stored tensors**, including unused
vision/MTP weights and any experts, and rejects counts above 15,000,000,000.
Do not count only active MoE parameters. Tensor parallelism shards this one
model across two GPUs; it does not introduce another model.

Licence: Apache-2.0, bundled LICENSE. Official model card:
https://huggingface.co/Qwen/Qwen3.5-9B
The exact commit, pre-cutoff revision date, hashes and parameter count are in
`/code/models/Qwen3.5-9B/MODEL_MANIFEST.json`. Preparation rejects a revision dated
on/after 26 October 2026. A model name alone is not release-date evidence.
This package uses pretrained weights; no fine-tuned adapter is bundled.
Training and inference source are included under `/code/training`, `/code/app`
and `/code/evaluation`. Training utilities must only consume allowed train or
synthetic data, never public/private test examples or answers. Inference contains
no rules mapping specific question/business identifiers to answers.

## Prepare and build (network allowed only before submission)

The BTC guide recommends `vllm/vllm-openai:v0.10.1.1` but explicitly permits
alternatives supporting RTX 5090 / compute capability 12.0. We retain the newer
immutable v0.32.0 base for Qwen3.5 support. Its Blackwell kernels and actual runtime
must be tested on the reference GPUs before submission. Registry inspection
confirmed this tag resolves to the pinned digest and contains linux/amd64.
The startup preflight also executes a BF16 matrix multiply on both GPUs and logs
compute capabilities and peer-access availability; this does not replace a full run. The guide's NCCL 2.27.7
upgrade is a workaround for its older sample; do not downgrade a newer stack
blindly. This worker sets `NCCL_P2P_DISABLE=1`, disables custom all-reduce, uses
16 GiB shared memory, clears ENTRYPOINT and defaults CMD to bash predict.sh.

Choose an immutable pre-cutoff public model commit, then run:

```bash
python3 -m docker.prepare_model --revision FULL_40_CHARACTER_COMMIT_SHA
# This build-time step downloads weights and writes provenance/checksums.
docker build --platform linux/amd64 -t zaic2026_datamate_<team_name>:final .
```

The build copies weights **inside** the image, checks checksums/parameter count,
checks CUDA >= 12.8, and records installed dependencies. Missing weights fail the
build. Preserve the resulting image digest and `docker/environment.lock.txt`;
freeze/save that exact image rather than rebuilding after the deadline.
The worker uses the base's PyTorch/vLLM, BF16, tensor parallel size 2 and eager
execution. Test on the actual two RTX 5090 GPUs; CUDA version alone does not
prove the image includes appropriate Blackwell kernels.

```bash
mkdir -p result
docker run --rm --gpus '"device=0,1"' --network none --shm-size=16g \
  --entrypoint bash \
  -v "$PWD/dataset/public_test:/data:ro" \
  -v "$PWD/result:/result" zaic2026_datamate_<team_name>:final /code/predict.sh
# Freeze and export one tag. Script validates linux/amd64 and tar <= 100 GB.
bash docker/export.sh <team_name> ./release
```

Export produces `.tar.gz`, `.tar.gz.md5`, `.tar.gz.size.txt` (uncompressed bytes)
and image-inspection metadata. Submit the archive link, MD5 and uncompressed size.
Do not rename it after generating MD5. Verify the downloaded archive on a second
machine with `md5sum -c`. Use the exact frozen image for the private phase.

## Training verification package

`/code/training_code/` contains its own README, pinned installed environment,
source, the provided train data, frozen configuration, sample counts and
`data_manifest.json` with local paths, byte sizes and SHA-256. No team training,
synthetic generation or adapters were used for this frozen checkpoint; the README
states this explicitly. No optimizer/checkpoint logs are invented. Exact model
revision, original first-weight release date, licence and stored parameter count
are appended to `/code/README.md` during build from the verified model manifest.

Official guideline:
https://dl-challenge.zalo.ai/2026/datamate_dkewxftdzo/zac2026_DataMate_docker_guideline_final.pdf

## Reproducibility and validation

HF offline flags and the local-path guard prevent model download fallback.
`--network none` is required for the rehearsal. Seeds are fixed for initialization
and reset from question text/date before each question. vLLM sampling uses explicit
per-call seeds; temperature remains nonzero for diverse consensus samples.
Run twice from empty result directories on identical hardware/image/input and
compare submissions and PA. These measures do **not** guarantee the required
PA difference <= 0.03; that requires measurement. Preserve both sets of logs. Estimate private runtime as model initialization plus
mean end-to-end question time times 500, and validate on the full run; shared
business batch elapsed_ms values overlap and must not simply be summed.

The frozen worker currently uses reviewed consensus without a Colab confidence
artifact: HF calibration is incompatible with the vLLM policy. Do not silently
copy that confidence report into this image. SQL training exports and calibration
code are included for rebuilding/evaluating a future frozen policy.

Source tests use tiny data/scripted local models and real DuckDB. They cannot
establish full-dataset completion time, RTX 5090 compatibility or leaderboard PA.
