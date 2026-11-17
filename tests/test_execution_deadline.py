import threading
import time

import pytest

from src.application import execution


def test_deadline_budget_is_shared_and_not_reset_by_nested_binding():
    now = [10.0]
    scope = execution.ExecutionContext(20, clock=lambda: now[0])
    with execution.bind_execution(scope):
        now[0] = 25
        assert execution.current_execution().remaining() == 5
        with execution.bind_execution(scope):
            now[0] = 30
            with pytest.raises(execution.ExecutionStopped, match="deadline"):
                execution.current_execution().check()
    assert execution.current_execution() is None


def test_cancel_releases_registered_blocked_transport_without_next_token():
    scope = execution.ExecutionContext(10)
    released = threading.Event()
    with scope.interrupt_on_stop(released.set):
        scope.cancel()
        assert released.wait(1)
    with pytest.raises(execution.ExecutionStopped, match="cancelled"):
        scope.check()


def test_deadline_releases_stalled_transport_and_drip_does_not_extend_it():
    scope = execution.ExecutionContext(0.08)
    released = threading.Event()
    with scope.interrupt_on_stop(released.set):
        while not released.wait(0.005):
            # Progress does not reset the request clock.
            assert scope.remaining() <= 0.08
        with pytest.raises(execution.ExecutionStopped):
            scope.check()


def test_finished_transport_is_not_closed_later():
    scope = execution.ExecutionContext(0.05)
    released = threading.Event()
    with scope.interrupt_on_stop(released.set):
        pass
    assert not released.wait(0.1)
