"""Durable step runner with advisory OS locks and process-group subprocess cancellation."""

from __future__ import annotations

import errno
import fcntl
import os
import signal
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

from mk_studio.state import StateStore, StepTicket


class RunnerError(RuntimeError):
    """Base error for runner lock or execution failures."""


class ConcurrentRunError(RunnerError):
    """Raised when an advisory run or editor-writer lock is already held."""


class SubprocessCancelledError(RunnerError):
    """Raised when a child process group is cancelled or times out."""


@contextmanager
def advisory_file_lock(lock_path: str | Path, lock_label: str = "run") -> Iterator[int]:
    """Acquire a non-blocking exclusive advisory OS file lock (fcntl.flock)."""
    resolved = Path(lock_path).expanduser().resolve(strict=False)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(resolved), os.O_CREAT | os.O_RDWR, 0o600)
    acquired = False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK):
                raise ConcurrentRunError(
                    f"Cannot acquire {lock_label} lock at {resolved}: another process or run holds the lock"
                ) from exc
            raise
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode("utf-8"))
        os.fsync(fd)
        yield fd
    finally:
        if acquired:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(fd)


def terminate_process_group(
    pgid: int,
    grace_seconds: float = 0.5,
    proc: subprocess.Popen[Any] | None = None,
) -> None:
    """Terminate an entire process group cleanly via SIGTERM then SIGKILL."""
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return

    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            break
        try:
            os.killpg(pgid, 0)
        except (ProcessLookupError, PermissionError):
            return
        time.sleep(0.02)

    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        return



@dataclass(frozen=True)
class SubprocessResult:
    """Completed subprocess result."""

    returncode: int
    stdout: str
    stderr: str
    pid: int


def run_cancellable_subprocess(
    cmd: list[str],
    *,
    cancel_event: threading.Event | None = None,
    timeout_seconds: float | None = None,
    cwd: str | Path | None = None,
    check: bool = True,
) -> SubprocessResult:
    """Run a command in its own process group and kill the group on cancel or timeout."""
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    pgid = proc.pid
    start_time = time.monotonic()

    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                terminate_process_group(pgid, proc=proc)
                proc.communicate()
                raise SubprocessCancelledError(f"Subprocess group {pgid} cancelled via cancel_event: {cmd[0]}")

            if timeout_seconds is not None and (time.monotonic() - start_time) > timeout_seconds:
                terminate_process_group(pgid, proc=proc)
                proc.communicate()
                raise SubprocessCancelledError(
                    f"Subprocess group {pgid} timed out after {timeout_seconds:.2f}s: {cmd[0]}"
                )

            ret = proc.poll()
            if ret is not None:
                stdout, stderr = proc.communicate()
                result = SubprocessResult(
                    returncode=ret,
                    stdout=stdout or "",
                    stderr=stderr or "",
                    pid=pgid,
                )
                if check and ret != 0:
                    raise subprocess.CalledProcessError(ret, cmd, output=result.stdout, stderr=result.stderr)
                return result

            time.sleep(0.02)
    except BaseException:
        if proc.poll() is None:
            terminate_process_group(pgid, proc=proc)
            proc.communicate()
        raise


class StepRunner:
    """Executes steps with short DB transactions and advisory run/editor locks."""

    def __init__(self, store: StateStore, lock_dir: str | Path) -> None:
        self.store = store
        self.lock_dir = Path(lock_dir).expanduser().resolve(strict=False)
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        self.run_lock_path = self.lock_dir / "run.lock"
        self.editor_lock_path = self.lock_dir / "editor-writer.lock"

    @contextmanager
    def acquire_run_lock(self) -> Iterator[int]:
        with advisory_file_lock(self.run_lock_path, lock_label="run") as fd:
            yield fd

    @contextmanager
    def acquire_editor_writer_lock(self) -> Iterator[int]:
        with advisory_file_lock(self.editor_lock_path, lock_label="editor-writer") as fd:
            yield fd

    def execute_step(
        self,
        *,
        run_id: str,
        step_name: str,
        step_kind: str,
        input_hash: str,
        action: Callable[[StepTicket], dict[str, Any]],
        expected_output_path: str | Path | None = None,
    ) -> tuple[StepTicket, dict[str, Any]]:
        """Execute a step only if not already completed for the same idempotency key."""
        lock_ctx = (
            self.acquire_editor_writer_lock()
            if step_kind == "editor_mutation"
            else contextlib.nullcontext()
        )
        with lock_ctx:
            ticket = self.store.begin_step(
                run_id=run_id,
                step_name=step_name,
                step_kind=step_kind,
                input_hash=input_hash,
                expected_output_path=expected_output_path,
            )
            if ticket.already_completed:
                return ticket, ticket.cached_receipt

            try:
                receipt = action(ticket) or {}
            except Exception as exc:
                self.store.finish_step(
                    ticket,
                    status="failed",
                    error_message=str(exc),
                )
                raise

            ext_id = receipt.get("external_target_id")
            out_hash = receipt.get("output_hash")
            self.store.finish_step(
                ticket,
                status="succeeded",
                output_hash=out_hash,
                external_target_id=str(ext_id) if ext_id is not None else None,
                receipt=receipt,
            )
            return ticket, receipt


import contextlib  # placed at module bottom for type cleanliness
