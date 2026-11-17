"""Request lifetime, independent of provider inactivity timeouts.

Context is execution-local; never serialize it into clinical evidence.
"""

from contextlib import contextmanager
from contextvars import ContextVar
import socket
import threading
import time
from functools import wraps


class ExecutionStopped(RuntimeError):
    pass


_current = ContextVar("infermed_execution", default=None)


class ExecutionContext:
    def __init__(self, timeout_s, *, clock=time.monotonic):
        self.clock = clock
        self.deadline = clock() + timeout_s
        self.cancelled = threading.Event()

    def remaining(self):
        return max(0.0, self.deadline - self.clock())

    def check(self):
        if self.cancelled.is_set():
            raise ExecutionStopped("Request cancelled")
        if self.remaining() <= 0:
            raise ExecutionStopped("Request deadline exceeded")

    def cancel(self):
        self.cancelled.set()

    @contextmanager
    def interrupt_on_stop(self, interrupt):
        self.check()
        finished = threading.Event()

        def watch():
            while not finished.wait(min(0.05, self.remaining())):
                if self.cancelled.is_set() or self.remaining() <= 0:
                    interrupt()
                    return

        watcher = threading.Thread(target=watch, name="infermed-deadline", daemon=True)
        watcher.start()
        try:
            yield
        finally:
            finished.set()
            watcher.join(timeout=1)


def current_execution():
    return _current.get()


@contextmanager
def bind_execution(context):
    token = _current.set(context)
    try:
        context.check()
        yield context
    finally:
        _current.reset(token)


def check_execution():
    scope = current_execution()
    if scope:
        scope.check()


def bounded_execution(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        scope = current_execution() or ExecutionContext(300)
        with bind_execution(scope):
            result = function(*args, **kwargs)
            scope.check()
            return result

    return wrapped


def interrupt_response(response):
    """Shutdown before close: close alone can block behind a pending read."""
    raw = getattr(response, "raw", None)
    connection = getattr(raw, "_connection", None)
    sock = getattr(connection, "sock", None)
    if sock is None:
        fp = getattr(getattr(raw, "_fp", None), "fp", None)
        sock = getattr(getattr(fp, "raw", None), "_sock", None)
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
    response.close()
