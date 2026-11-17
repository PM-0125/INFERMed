"""Hard request lifetime boundary for synchronous retrieval/provider libraries."""

import multiprocessing
import queue

from src.application.execution import ExecutionContext, ExecutionStopped, bind_execution


def _worker(function, argument, deadline, channel):
    scope = ExecutionContext(1)
    scope.deadline = deadline

    def progress(stage, message, payload):
        scope.check()
        channel.put(("progress", (stage, message, payload)), timeout=0.5)

    def token(text):
        scope.check()
        channel.put(("token", text), timeout=0.5)

    try:
        with bind_execution(scope):
            result = function(
                argument, progress_callback=progress, token_callback=token
            )
            scope.check()
            channel.put(("result", result), timeout=0.5)
    except Exception as exc:
        try:
            channel.put(("error", (type(exc).__name__, str(exc))), timeout=0.5)
        except queue.Full:
            pass  # Parent owns termination; a full queue cannot block cleanup.


def run_isolated(
    function, argument, *, scope, progress_callback=None, token_callback=None
):
    """Spawn overhead and all child work consume the original absolute budget."""
    scope.check()
    runtime = multiprocessing.get_context("spawn")
    channel = runtime.Queue(maxsize=128)
    process = runtime.Process(
        target=_worker,
        args=(function, argument, scope.deadline, channel),
        name="infermed-request",
        daemon=True,
    )
    process.start()
    try:
        while True:
            scope.check()
            try:
                kind, payload = channel.get(timeout=min(0.05, scope.remaining()))
            except queue.Empty:
                if not process.is_alive():
                    raise RuntimeError(
                        "Analysis worker exited without a completed result"
                    )
                continue
            if kind == "progress" and progress_callback:
                progress_callback(*payload)
            elif kind == "token" and token_callback:
                token_callback(payload)
            elif kind == "result":
                scope.check()
                return payload
            elif kind == "error":
                error_type, detail = payload
                if error_type == "ExecutionStopped":
                    raise ExecutionStopped(detail)
                raise (
                    ValueError(detail)
                    if error_type == "ValueError"
                    else RuntimeError(detail)
                )
    finally:
        # No reusable pool: a wedged request cannot consume a worker forever.
        process.join(timeout=0.1)
        if process.is_alive():
            process.terminate()
            process.join(timeout=1)
        if process.is_alive():
            process.kill()
            process.join(timeout=1)
        alive = process.is_alive()
        if not alive:
            process.close()
        channel.cancel_join_thread()
        channel.close()
        if alive:
            raise RuntimeError("Request worker could not be terminated")
