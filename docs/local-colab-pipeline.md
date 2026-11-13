# Full pipeline with local Qwen on Colab Pro

Open `notebooks/colab_pro.ipynb` in a GPU runtime and run its cells in order.
The GitHub branch being cloned must contain these changes. `GITHUB_TOKEN` is
only for cloning the private repository; Gemini API keys are not used.

The notebook installs `requirements-colab.txt`, validates the code, and creates
one `LocalSQLPipeline`. Its lazy local HF backend is loaded once and reused for
manifest generation, question rewriting and SQL generation. No remote fallback
is configured. The optional Gemini SDK dependency supports legacy tests only.

## Stages

1. Read `dataset/train` and `dataset/public_test` (source files, README, rulebook,
   calendar, questions). Manifest generation never reads answer keys.
2. Use Qwen to generate BusinessManifest v2 per source object, in batches of at
   most 12 columns. Merge the batches, require all source objects and columns,
   validate schema, source names, evidence references and semantic constraints.
   Unknown semantics remain unresolved. Existing source-dataset manifests are
   not treated as newly generated Qwen results.
3. Ingest calendar and all businesses through the existing Raw/Typed/Semantic
   pipeline. Store DuckDB, catalog, guides and quality reports before inference.
4. Verify source fingerprints and database tables. Missing/stale preparation
   fails before inference. The SQL context uses the generated manifest directory
   and the exact same database paths, with automatic rebuilding disabled.
5. Smoke test three train questions, evaluate all train questions, and predict
   all public-test questions. Evaluation reads gold answers only for scoring.
   For holdout evaluation use `pipeline.evaluate(subset="holdout", holdout_n=6)`.

The local service validates generated JSON and retries invalid responses.
Rewrite preserves numbers and protected identifiers; failures keep the original
question and are logged. SQL runs through the existing read-only DuckDB executor,
repair and consensus logic. An unanswered question remains abstain; a complete
submission file does not imply that all answers are correct.

## Outputs and restart

```text
OUT/
  manifests/{train,public_test}/
    <business>.json
    batches/<business>/*.json
    evidence/  reports/  provenance/
  databases/{train,public_test}/
    _shared/current/data.duckdb
    <business>/current/data.duckdb
  reports/prepare_train.json
  reports/prepare_public_test.json
  reports/validation.json
  smoke/
  train/summary.json
  public_test/submission.jsonl
```

Rerun preparation after interruption: valid source batches are reused, and
DuckDB is reused when source, manifest and pipeline fingerprints match. A failed
business is recorded and stops preparation. Raw dataset files are not edited.

By default outputs are in `/content/datamate_sql_out`; deleting the runtime
deletes these files. Set `OUT` to `/content/drive/MyDrive/datamate_sql_out` before
creating the pipeline to persist them. Drive is optional for outputs, not input.

## Memory and generation limits

Defaults: Qwen3.5-9B, Transformers/HF, quantization auto, 16,384 total tokens,
6,144 manifest output tokens and 2,048 SQL output tokens. HF checks the complete
chat template token count before generation. SQL context first drops sample
rows and redundant profiles, then retains the most relevant whole table schemas
while reporting omitted tables. Rulebook, README and parse completeness remain.
Omitted tables must not be interpreted as proof of missing source data.

A manifest batch selects complete evidence sections by relevance when needed,
records omitted sections as unresolved, and keeps all target column bindings.
Any remaining context overflow fails explicitly; no truncated manifest is
accepted. Increase context only when VRAM permits. GPU
memory, speed and answer quality require a real Colab smoke test; CPU integration
tests validate orchestration with a scripted model, not Qwen quality.

Qwen3.5 uses the explicit text-only `Qwen3_5ForCausalLM` loader documented in
[Transformers](https://huggingface.co/docs/transformers/main/model_doc/qwen3_5).

## Verification scope

The notebook gates on the local pipeline, SQL, manifest, main CLI and architecture
tests. A full-suite run also found 11 pre-existing failures, reproduced from a
clean HEAD archive: old hardware-selection expectations; missing diagram/review
assets; organization key/partition expectations; and legacy Python sandbox
helpers. These are not silently treated as passing. They are outside the notebook's
SQL/local gate. Real Qwen generation and VRAM usage still need a Colab GPU run.

## CLI

```bash
python -m app.local_pipeline \
  --dataset-dir dataset --out-dir /content/datamate_sql_out \
  --model Qwen/Qwen3.5-9B --hf-quant auto --stage all
```

Stages `prepare`, `validate`, `smoke`, `evaluate`, `predict` can also be run
separately. Each CLI process loads its own model when needed; the notebook keeps
one model alive across stages. `validate` needs no GPU.

The manifest CLI also defaults to local Qwen:

```bash
python -m app.ingestion.cli.build_manifests \
  --dataset-dir dataset/train/data --output-dir outputs/manifests/train \
  --backend local --local-model Qwen/Qwen3.5-9B
```
