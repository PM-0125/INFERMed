import multiprocessing
import time
import pytest
from src.application.execution import ExecutionContext, ExecutionStopped
from src.infrastructure import process_supervisor


def stalled_job(value, progress_callback=None, token_callback=None):
    progress_callback("started", "", {})
    while True:
        time.sleep(0.01)
        if value == "drip":
            progress_callback("drip", "", {})


def completed_job(value, progress_callback=None, token_callback=None):
    return {"answer": value, "verification": {"status": "review_required"}}


@pytest.mark.parametrize("behavior", ["stall", "drip"])
def test_deadline_terminates_and_joins_uncooperative_work(behavior):
    scope = ExecutionContext(2)
    with pytest.raises(ExecutionStopped, match="deadline"):
        process_supervisor.run_isolated(stalled_job, behavior, scope=scope)
    assert not [
        p for p in multiprocessing.active_children() if p.name == "infermed-request"
    ]


def test_disconnect_cancels_worker_before_it_returns():
    scope = ExecutionContext(10)

    def progress(*args):
        scope.cancel()

    with pytest.raises(ExecutionStopped, match="cancelled"):
        process_supervisor.run_isolated(
            stalled_job, "stall", scope=scope, progress_callback=progress
        )
    assert not [
        p for p in multiprocessing.active_children() if p.name == "infermed-request"
    ]


def test_success_preserves_result():
    result = process_supervisor.run_isolated(
        completed_job, "source-bound result", scope=ExecutionContext(10)
    )
    assert result == {
        "answer": "source-bound result",
        "verification": {"status": "review_required"},
    }
