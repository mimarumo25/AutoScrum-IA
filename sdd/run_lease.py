"""Exclusion mutua entre procesos para una corrida sobre el mismo proyecto."""
import json
import os
import time
import atexit
import hashlib
from pathlib import Path


class RunBusyError(RuntimeError):
    """Otra instancia conserva el lease del proyecto."""


_ACTIVE: list["RunLease"] = []


class RunLease:
    def __init__(self, workdir: str | Path, wait_seconds: float):
        self.workdir = Path(workdir).resolve()
        canonical = os.path.normcase(str(self.workdir))
        identity = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        self.path = self.workdir.parent / ".sdd-locks" / f"{identity}.lock"
        self.wait_seconds = max(0.0, float(wait_seconds))
        self.stream = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            self.stream.write(b"\0")
            self.stream.flush()
        deadline = time.monotonic() + self.wait_seconds
        while True:
            try:
                _lock(self.stream)
                break
            except OSError as error:
                if time.monotonic() >= deadline:
                    self.stream.close()
                    self.stream = None
                    raise RunBusyError(
                        f"ya existe una corrida activa para {self.workdir}"
                    ) from error
                time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
        self.stream.seek(0)
        self.stream.truncate()
        self.stream.write(json.dumps({
            "pid": os.getpid(), "acquired_at": time.time(),
        }).encode("utf-8"))
        self.stream.flush()
        return self

    def __exit__(self, *_exc):
        if self.stream is not None:
            try:
                _unlock(self.stream)
            finally:
                self.stream.close()
                self.stream = None


def _lock(stream) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(stream) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def acquire(workdir: str | Path, wait_seconds: float) -> RunLease:
    return RunLease(workdir, wait_seconds)


def hold_until_exit(workdir: str | Path, wait_seconds: float) -> RunLease:
    """Adquiere antes de leer estado y libera aun si main retorna temprano."""
    lease = acquire(workdir, wait_seconds)
    lease.__enter__()
    _ACTIVE.append(lease)
    atexit.register(lease.__exit__, None, None, None)
    return lease
