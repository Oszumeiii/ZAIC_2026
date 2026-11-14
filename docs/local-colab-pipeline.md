# Full pipeline with local Qwen on Colab Pro

Open `notebooks/colab_pro.ipynb` in a GPU runtime and run its cells in order.
The GitHub branch being cloned must contain these changes. `GITHUB_TOKEN` is
only for cloning the private repository; no LLM API key is needed.

The notebook installs `requirements-colab.txt`, validates the code, and creates
one `LocalSQLPipeline`. Its lazy local HF backend is loaded once and reused for
manifest generation, question rewriting and SQL generation. No remote fallback
is configured.

## Stages

1. Read `dataset/train` and `dataset/public_test` (source files, README, rulebook,
   calendar, questions). Manifest generation never reads answer keys.
2. Use Qwen to generate BusinessManifest v2 per source object, in batches of at
   most 12 columns. Merge the batches, require all source objects and columns,
   validate schema, source names, evidence references and semantic constraints.
   Unknown semantics remain unresolved. Existing source-dataset manifests are
   not treated as newly generated Qwen results.
   For documented semantic names, conflicting redundant aggregation/time-role
   metadata is canonicalized using the same contracts as the validator. Source
   names, references, units and unknown/null values are not changed. Corrections
   are recorded under `manifests/<split>/reports/normalization/<business>/`.
   Remaining invalid mappings are retried with the previous column metadata;
   exhausted failures are saved under `reports/failures/<business>/`.
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

The notebook gates on SQL/local, ingestion and submission tests. These use
scripted model responses and real DuckDB; they do not measure Qwen accuracy or
GPU throughput. The previously failing tests have been addressed in this revision.
Real improvement in PA still requires the fit/holdout runs on Colab.

## CLI

```bash
python -m app.local_pipeline \
  --dataset-dir dataset --out-dir /content/datamate_sql_out \
  --model Qwen/Qwen3.5-9B --hf-quant auto --stage all
```

Stages `prepare`, `validate`, `smoke`, `evaluate`, `develop`, `predict` can also be run
separately. Each CLI process loads its own model when needed; the notebook keeps
one model alive across stages. `validate` needs no GPU.

The manifest CLI also defaults to local Qwen:

```bash
python -m app.ingestion.cli.build_manifests \
  --dataset-dir dataset/train/data --output-dir outputs/manifests/train \
  --backend local --local-model Qwen/Qwen3.5-9B
```

## SQL quality loop and development split

The notebook now defaults to `DATASET_SPLIT="train"`. Only the selected split is
prepared, backed up and validated. `public_test` runs prediction. Each candidate
has an initial attempt plus **five repair retries** (`max_retry=5`); JSON and
manifest validation use the same configured retry count. Retries are bounded but
can be nested: these settings are not a five-call total budget.

The local pipeline enables a separate Qwen review after execution. It checks
five dimensions (grain, joins, time, units, rules), requests missing schemas,
and executes read-only BOOLEAN assertions. JOIN aggregates require assertions.
A failed/unknown review or failed assertion is fed back into repair; unsuccessful
candidates do not vote. The reviewer is still an LLM, and its assertions are not
a proof that all business errors have been detected. Expect additional inference
cost and measure coverage as well as precision.

Context selection keeps the highest-ranked table and its relationship component.
It retains a directory of omitted sources. Referencing an omitted table or a
reviewer request reloads its schema from the original context within the repair
loop. If the required component cannot fit, the question fails safely rather than
silently removing a dependency. Increasing context may be needed for large graphs.

`pipeline.develop(holdout_n=6, calibration_n=6)` partitions the 30 train businesses
into 18 fit, 6 calibration and 6 final holdout businesses, with stable hashes.
It writes:

- `learning/fit/`: predictions and scoring on development businesses.
- `learning/sql_examples.jsonl`: scored-correct SQL from fit only.
- `learning/sql_sft.jsonl`: question/schema/SQL chat examples, not an automatically trained model.
- `learning/business_groups.json`: disjoint business assignments.
- `learning/calibration/`: measurements used to calibrate the decision policy.
- `learning/holdout/`: independent evaluation of the calibrated policy.
- `learning/calibration/confidence.json`: precision, sample count and Wilson lower
  bound per answer type and vote count, bound to model, prompts and settings.

On public test, the notebook loads these artifacts when available. A calibrated
bucket needs at least 20 answers and lower precision > 0.5; otherwise it abstains.
This conservative threshold is an initial policy, not a validated competition
optimum. Wilson intervals treat questions as independent; business correlations
can make them optimistic. The final holdout is separate from calibration; do not tune on it and keep
claiming it is an independent evaluation. The code rejects using calibration businesses to score that same policy.
If no calibration file exists, normal reviewed consensus remains available;
it must not be described as calibrated confidence.

The submission validator checks finite numeric values, genuine booleans, ISO
dates (YYYY-MM-DD or YYYY-MM for month questions) and list/table shapes. It follows answer *formats*, without reading gold
answers during generation. `alias` remains evaluation metadata. `abstain` and
`unanswerable` retain their submission-specific shapes.

Ingestion fixes retain explicit empty CSV records (so a missing key cannot become
unique by dropping its row), recognize workbook month partitions using
`source_object`, restore the missing split review and diagram assets, and wire
legacy sandbox helpers. The old hardware test was updated to the intentional
Qwen3.5/HF policy. Pipeline version v3 invalidates previously prepared databases;
run preparation again after updating. Existing model weights remain cached.

After development, the notebook saves a separate learning ZIP to Drive, containing
examples, SFT records, calibration and final evaluation. Extract it into OUT before
configuring a new public-test session. CLI prediction can use `--exemplar-path`
and `--confidence-path`; signatures reject mismatched policies.

Each evaluation also writes `error_analysis.json`: PA/precision by business,
counts of generation/execution/business-review/format failures, and confidence
abstentions. Use this alongside SQL logs to prioritize fixes on fit businesses.
