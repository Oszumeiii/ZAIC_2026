# Training and verification materials

The frozen submitted worker uses the publicly released Qwen3.5-9B checkpoint
without fine-tuning. **No training or synthetic-data generation was performed to
produce this image's inference checkpoint.** No adapter, SQL few-shot bank or
calibration output is used by this frozen worker. The optional Colab development
workflow is separate and its results are not silently included.

- `src/`: training/SFT export utilities (also importable as `/code/training`).
- `data/train/`: the provided original training split, for independent evaluation;
  answers here are not read by inference. No public/private test is bundled.
- `configs/frozen.json`: exact frozen worker settings and no-training declaration.
- `requirements.txt`: full installed, pinned build environment.
- `data_manifest.json`: files, byte sizes, SHA-256, data source and sample counts.
- Inference and preprocessing: `/code/app`; entrypoint: `/code/docker/worker.py`.
- Prompts/templates are in `/code/app/llm/prompts.py`,
  `/code/app/agent/sql_solver.py`, `/code/app/agent/sql_review.py`.
- Model checkpoint/tokenizer/config/license and revision provenance:
  `/code/models/Qwen3.5-9B/MODEL_MANIFEST.json`.

There are no team training logs, optimizer states or intermediate checkpoints
because no optimization was performed. The only model checkpoint required for
verification is bundled. Upstream Qwen pretraining is described by its model card;
this package does not claim to reproduce the upstream pretraining corpus/process.

## Reproduce the frozen solution

Use the image's installed environment. No pip install/download is needed at
inference. On the reference 2 x RTX 5090 host, mount the bundled training data as
/data and run `bash /code/predict.sh` twice with clean result directories. The
worker executes source profiling, Qwen manifest generation, DuckDB ingestion,
Qwen question rewriting/SQL/review and deterministic result formatting. Its seed
is 2026, per-question sampling seeds derive from text/date, temperature 0.7,
consensus 2/3, retry count 5 and context length 16384. See `configs/frozen.json`.

GPU kernel scheduling and floating-point reductions may remain nondeterministic;
measure differences rather than assuming seed alone guarantees reproducibility.
Training hardware/time: not applicable. End-to-end GPU runtime: not measured
in this source-only development environment; benchmark the final image.

Optional future fine-tuning source is supplied for inspection only. Running it
would produce a different solution that must be repackaged with its data, logs,
settings, dependencies and final weights before the freeze deadline.
