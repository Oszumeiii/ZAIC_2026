"""Explicit runtime profiles and cooperative scheduling for the offline worker."""

DEFAULT_PROFILE = 'rtx5090-2gpu-6h'
FAST_PROFILES = ('rtx5090-6h', 'rtx5090-2gpu-6h')
PREPARATION_BUDGET_S = 3600
QUESTION_MAX_CALLS = 14  # up to two rewrite calls plus twelve SQL/review calls


def configure_runtime(cfg, environ):
    profile = environ.get('DATAMATE_PROFILE', DEFAULT_PROFILE)
    if profile not in ('balanced', *FAST_PROFILES):
        raise ValueError('DATAMATE_PROFILE must be balanced, rtx5090-6h or rtx5090-2gpu-6h')
    parallel = environ.get('DATAMATE_TENSOR_PARALLEL', '1' if profile == 'rtx5090-6h' else '2')
    if parallel not in ('1', '2'):
        raise ValueError('DATAMATE_TENSOR_PARALLEL must be 1 or 2')
    if profile == 'rtx5090-6h' and parallel != '1':
        raise ValueError('rtx5090-6h requires DATAMATE_TENSOR_PARALLEL=1')
    if profile == 'rtx5090-2gpu-6h' and parallel != '2':
        raise ValueError('rtx5090-2gpu-6h requires DATAMATE_TENSOR_PARALLEL=2; '
                         'select balanced or rtx5090-6h for one GPU')
    cfg.tensor_parallel = int(parallel)
    if profile in FAST_PROFILES:
        cfg.rewrite_questions = True
        cfg.max_retry = 1
        cfg.code_timeout_s = 20
        # Serial requests need a small scheduler/cache footprint on a 32 GB GPU.
        # Preserve the 16K context, BF16 weights, review and 2-of-3 consensus.
        cfg.vllm_extra.update(enforce_eager=False, max_num_seqs=4,
                              max_num_batched_tokens=4096, enable_chunked_prefill=True,
                              language_model_only=True)
    eager = environ.get('DATAMATE_ENFORCE_EAGER')
    if eager is not None:
        if eager not in ('0', '1'):
            raise ValueError('DATAMATE_ENFORCE_EAGER must be 0 or 1')
        cfg.vllm_extra['enforce_eager'] = eager == '1'
    return profile


def preparation_budget(remaining_s, remaining_preparation_s, remaining_businesses):
    """Allow a business up to twice its equal share of the preparation pool."""
    pool = max(0.0, min(remaining_s, remaining_preparation_s))
    return min(pool, 2 * pool / max(1, remaining_businesses))


def question_budget(remaining_s, remaining_questions, remaining_businesses, preparation_mean_s,
                    preparation_reserve_s=None):
    """Reserve observed preparation cost, then share remaining time among questions.

    This is a soft cap checked between model calls, not an interruption of a GPU
    generation already in progress. The shell watchdog remains the hard limit.
    """
    remaining_s = max(0.0, remaining_s)
    reserve = min(remaining_s * 0.5, remaining_businesses * preparation_mean_s)
    if preparation_reserve_s is not None:
        reserve = min(remaining_s, max(0.0, preparation_reserve_s)) if remaining_businesses else 0.0
    fair_share = (remaining_s - reserve) / max(1, remaining_questions)
    return min(120.0, remaining_s, fair_share)


def ingest_before_deadline(business, output, manifest, rulebook_path, deadline):
    """Run CPU ingestion in a separate process so its entire stage can time out.

    The child receives source paths only; it does not import or fork a live CUDA
    model. No partial database is used after a timeout or nonzero child exit.
    """
    import subprocess
    import sys
    import time
    from app.ingestion.catalog import CatalogService
    from app.llm.base import InferenceBudgetExceeded

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise InferenceBudgetExceeded('Preparation budget exhausted before ingestion')
    command = [sys.executable, '-m', 'app.ingestion.processing.pipeline',
               '--business-dir', str(business), '--output-dir', str(output),
               '--manifest', str(manifest), '--rulebook', str(rulebook_path), '--mode', 'production']
    try:
        subprocess.run(command, check=True, timeout=remaining)
    except subprocess.TimeoutExpired as error:
        raise InferenceBudgetExceeded('Preparation budget exhausted during ingestion') from error
    return CatalogService.read_catalog(output / 'data.duckdb')
