"""Chạy code do LLM sinh ra với giới hạn thời gian.

Lưu ý: đây KHÔNG phải sandbox bảo mật. Unix dùng SIGALRM ở luồng chính; Windows dùng tiến trình con
có timeout. Bảng được sao chép trước khi chạy. Vòng nộp Docker vẫn cần cô lập môi trường phù hợp.
"""
from __future__ import annotations

import signal
import multiprocessing

import numpy as np
import pandas as pd

from app.tools.helpers import HELPERS


class CodeTimeout(Exception):
    pass


def _on_alarm(*_args):
    raise CodeTimeout("quá thời gian chạy")


def _execute(code: str, tables: dict, as_of):
    ns = {"tables": {k: v.copy() for k, v in tables.items()}, "pd": pd, "np": np, "AS_OF": pd.Timestamp(as_of), **HELPERS}
    exec(code, ns)
    return ns["result_type"], ns["result"]


def _worker(connection, code: str, tables: dict, as_of):
    """Top-level worker so Windows spawn can import it without starting the agent again."""
    try:
        connection.send((True, _execute(code, tables, as_of)))
    except BaseException as exc:
        try:
            connection.send((False, exc))
        except Exception:
            connection.send((False, RuntimeError(f"{type(exc).__name__}: {exc}")))
    finally:
        connection.close()


def _run_subprocess(code: str, tables: dict, as_of, secs: int):
    ctx = multiprocessing.get_context("spawn")
    receive, send = ctx.Pipe(duplex=False)
    process = ctx.Process(target=_worker, args=(send, code, tables, as_of), daemon=True)
    started = False
    try:
        process.start()
        started = True
        send.close()
        if not receive.poll(None if secs <= 0 else secs):
            raise CodeTimeout("quá thời gian chạy")
        try:
            ok, payload = receive.recv()
        except EOFError as exc:
            raise RuntimeError("Tiến trình chạy code kết thúc mà không trả kết quả") from exc
        if not ok:
            raise payload
        return payload
    finally:
        receive.close()
        send.close()
        if started:
            process.join(timeout=0.2)
            if process.is_alive():
                process.terminate()
                process.join()
            process.close()


def run_code(code: str, tables: dict, as_of, secs: int = 60):
    """Chạy `code` với tables/pd/np/AS_OF; trả (result_type, result) do code gán."""
    if not hasattr(signal, "SIGALRM"):
        return _run_subprocess(code, tables, as_of, secs)
    signal.signal(signal.SIGALRM, _on_alarm)
    signal.alarm(secs)
    try:
        return _execute(code, tables, as_of)
    finally:
        signal.alarm(0)
