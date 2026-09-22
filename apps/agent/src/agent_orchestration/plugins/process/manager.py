"""Session-owned background process management."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
import os
from pathlib import Path
import secrets
import signal
from typing import BinaryIO

from apps.agent.src.agent_orchestration.plugins.process.config import ProcessConfig
from apps.agent.src.agent_orchestration.plugins.process.cursor import (
    CursorError,
    ProcessCursorCodec,
)
from apps.agent.src.agent_orchestration.plugins.process.models import (
    ProcessRecord,
    ProcessSnapshot,
)


BackgroundStarter = Callable[
    [str, Callable[[], Awaitable[None]]], asyncio.Task[None]
]
SnapshotPublisher = Callable[[ProcessSnapshot], Awaitable[None]]


class ProcessOperationError(RuntimeError):
    """An expected process operation failure with a stable public code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class ProcessManager:
    """Own subprocesses and their logs for one Session event loop."""

    COMMAND_MAX_BYTES = 32 * 1024
    READ_CHUNK_BYTES = 64 * 1024
    GROUP_POLL_INITIAL_SECONDS = 0.05
    GROUP_POLL_MAX_SECONDS = 1.0

    def __init__(
        self,
        *,
        session_id: str,
        workspace_path: str | Path,
        processes_dir: str | Path,
        config: ProcessConfig,
        cursor_codec: ProcessCursorCodec,
        start_background_work: BackgroundStarter,
        publish_snapshot: SnapshotPublisher,
    ) -> None:
        self.session_id = session_id
        self.workspace_path = Path(workspace_path).expanduser().resolve(strict=True)
        self.processes_dir = Path(processes_dir).expanduser().resolve(strict=True)
        self.config = config
        self.cursor_codec = cursor_codec
        self._start_background_work = start_background_work
        self._publish_snapshot = publish_snapshot
        self._records: dict[str, ProcessRecord] = {}
        self._lock = asyncio.Lock()
        self._publication_lock = asyncio.Lock()
        self._accepting = False
        self._start_reservations = 0
        self._starts_idle = asyncio.Event()
        self._starts_idle.set()

    async def start_accepting(self) -> None:
        async with self._lock:
            self._accepting = True

    async def start(
        self,
        command: str,
        *,
        workdir: str | None = None,
        origin_task_id: str | None = None,
    ) -> ProcessSnapshot:
        normalized_command = self._validate_command(command)
        resolved_workdir = self._resolve_workdir(workdir)
        async with self._lock:
            if not self._accepting:
                raise ProcessOperationError(
                    "tool_unavailable", "process plugin is not accepting starts"
                )
            running = sum(
                record.status == "running" for record in self._records.values()
            )
            if running + self._start_reservations >= self.config.max_running_processes:
                raise ProcessOperationError(
                    "process_limit_reached",
                    "maximum number of running processes has been reached",
                )
            self._start_reservations += 1
            self._starts_idle.clear()

        record: ProcessRecord | None = None
        try:
            try:
                process_id, log_path, log_file = self._create_log()
            except OSError as error:
                raise ProcessOperationError(
                    "spawn_failed", "could not create the process log"
                ) from error
            spawn_task = asyncio.create_task(
                asyncio.create_subprocess_exec(
                    "bash",
                    "-lc",
                    normalized_command,
                    cwd=str(resolved_workdir),
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    start_new_session=True,
                )
            )
            cancelled_during_spawn = await self._wait_preserving_cancellation(
                spawn_task
            )
            try:
                process = spawn_task.result()
            except asyncio.CancelledError:
                log_file.close()
                self._unlink_quietly(log_path)
                raise
            except Exception as error:
                log_file.close()
                self._unlink_quietly(log_path)
                raise ProcessOperationError(
                    "spawn_failed", "operating system could not create the process"
                ) from error
            record = ProcessRecord(
                process_id=process_id,
                process=process,
                process_group_id=process.pid,
                command=normalized_command,
                workdir=resolved_workdir,
                started_at=datetime.now(UTC),
                log_path=log_path,
                log_file=log_file,
                log_generation=secrets.token_hex(16),
                origin_task_id=origin_task_id,
            )
            if cancelled_during_spawn:
                await self._rollback_start_uninterruptibly(record)
                raise asyncio.CancelledError
            async with self._lock:
                if not self._accepting:
                    raise ProcessOperationError(
                        "tool_unavailable",
                        "process plugin stopped accepting during start",
                    )
                self._records[record.process_id] = record
            try:
                record.supervisor_task = self._start_background_work(
                    f"process:{record.process_id}",
                    lambda: self._supervise(record),
                )
                publish_task = asyncio.create_task(
                    self._publish_serialized(record.snapshot())
                )
                cancelled_during_publish = (
                    await self._wait_preserving_cancellation(publish_task)
                )
                if cancelled_during_publish:
                    try:
                        publish_task.result()
                    except BaseException:
                        await self._rollback_start_uninterruptibly(record)
                    else:
                        record.committed.set()
                    raise asyncio.CancelledError
                publish_task.result()
            except asyncio.CancelledError:
                if not record.committed.is_set():
                    await self._rollback_start_uninterruptibly(record)
                raise
            except BaseException:
                await self._rollback_start_uninterruptibly(record)
                raise
            record.committed.set()
            return record.snapshot()
        except BaseException:
            if (
                record is not None
                and not record.committed.is_set()
                and not record.discarded
            ):
                await asyncio.shield(self._rollback_start(record))
            raise
        finally:
            async with self._lock:
                self._start_reservations -= 1
                if self._start_reservations == 0:
                    self._starts_idle.set()

    async def get(self, process_id: str) -> ProcessSnapshot:
        async with self._lock:
            return self._get_record(process_id).snapshot()

    async def list(
        self, *, cursor: str | None = None, page_size: int | None = None
    ) -> dict[str, object]:
        size = self._validate_page_size(page_size)
        async with self._lock:
            records = sorted(
                self._records.values(),
                key=lambda item: (-item.started_at.timestamp(), item.process_id),
            )
            start_index = 0
            if cursor is not None:
                try:
                    started_at, process_id = self.cursor_codec.decode_list(
                        cursor, session_id=self.session_id
                    )
                except CursorError as error:
                    raise ProcessOperationError(
                        "invalid_cursor", str(error)
                    ) from error
                for index, record in enumerate(records):
                    if (
                        self._cursor_time(record.started_at) == started_at
                        and record.process_id == process_id
                    ):
                        start_index = index + 1
                        break
                else:
                    raise ProcessOperationError(
                        "invalid_cursor", "cursor anchor is no longer available"
                    )
            page = records[start_index : start_index + size]
            has_more = start_index + len(page) < len(records)
            next_cursor = None
            if has_more and page:
                last = page[-1]
                next_cursor = self.cursor_codec.encode_list(
                    self.session_id,
                    self._cursor_time(last.started_at),
                    last.process_id,
                )
            return {
                "processes": [record.snapshot() for record in page],
                "page_size": size,
                "has_more": has_more,
                "next_cursor": next_cursor,
            }

    async def read_logs(
        self,
        process_id: str,
        *,
        cursor: str | None = None,
        limit_bytes: int | None = None,
    ) -> dict[str, object]:
        limit = self._validate_log_limit(limit_bytes)
        async with self._lock:
            record = self._get_record(process_id)
            generation = record.log_generation
            log_path = record.log_path
            truncated = record.log_truncated
            if not record.log_file.closed:
                record.log_file.flush()
        try:
            size = log_path.stat().st_size
        except OSError as error:
            raise ProcessOperationError("log_unavailable", str(error)) from error
        offset = 0
        if cursor is not None:
            try:
                offset = self.cursor_codec.decode_log(
                    cursor,
                    session_id=self.session_id,
                    process_id=process_id,
                    generation=generation,
                )
            except CursorError as error:
                raise ProcessOperationError(
                    "invalid_cursor", str(error)
                ) from error
        if offset > size:
            raise ProcessOperationError(
                "invalid_cursor", "cursor offset exceeds current log size"
            )
        raw = await asyncio.to_thread(self._read_log_page, log_path, offset, limit, size)
        next_offset = offset + len(raw)
        return {
            "process_id": process_id,
            "content": raw.decode("utf-8", errors="replace"),
            "has_more": next_offset < size,
            "next_cursor": self.cursor_codec.encode_log(
                self.session_id, process_id, generation, next_offset
            ),
            "log_path": str(log_path),
            "log_truncated": truncated,
        }

    async def stop(
        self, process_id: str, *, reason: str = "requested"
    ) -> ProcessSnapshot:
        async with self._lock:
            record = self._get_record(process_id)
            if record.status != "running":
                return record.snapshot()
            if not isinstance(reason, str) or not reason.strip():
                raise ProcessOperationError(
                    "invalid_arguments", "stop reason must be a non-empty string"
                )
            waiter = self._request_stop_locked(
                record, reason.strip(), create_waiter=True
            )
            assert waiter is not None
        return await asyncio.shield(waiter)

    async def quiesce(self) -> None:
        async with self._lock:
            self._accepting = False
            for record in self._records.values():
                if record.status == "running":
                    self._request_stop_locked(
                        record, "session_shutdown", create_waiter=False
                    )

    async def drain(self) -> None:
        await self._starts_idle.wait()
        async with self._lock:
            tasks = tuple(
                record.supervisor_task
                for record in self._records.values()
                if record.supervisor_task is not None
            )
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=False)

    async def shutdown(self) -> None:
        async with self._lock:
            self._accepting = False
        await self._starts_idle.wait()
        async with self._lock:
            records = tuple(self._records.values())
        errors: list[str] = []
        for record in records:
            if record.status == "running":
                record.stop_requested = True
                record.stop_reason = record.stop_reason or "session_shutdown"
                try:
                    cleaned = await self._terminate_group(record)
                    if cleaned:
                        await self._finalize_terminal(record)
                    else:
                        errors.append(f"{record.process_id}: termination_failed")
                except BaseException as error:
                    errors.append(f"{record.process_id}: {error}")
        tasks = tuple(
            record.supervisor_task
            for record in records
            if record.supervisor_task is not None
            and not record.supervisor_task.done()
        )
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for record in records:
            self._close_log(record.log_file)
        if errors:
            raise RuntimeError("; ".join(errors))

    async def _supervise(self, record: ProcessRecord) -> None:
        reader = asyncio.create_task(
            self._drain_output(record), name=f"process-log:{record.process_id}"
        )
        try:
            await record.committed.wait()
            if record.discarded:
                return
            backoff = self.GROUP_POLL_INITIAL_SECONDS
            while True:
                if reader.done():
                    reader.result()
                if (
                    record.termination_generation
                    > record.handled_termination_generation
                ):
                    generation = record.termination_generation
                    try:
                        cleaned = await self._terminate_group(record)
                    except ProcessOperationError as error:
                        record.handled_termination_generation = generation
                        self._resolve_termination_waiters(
                            record, through=generation, error=error
                        )
                        continue
                    record.handled_termination_generation = generation
                    if cleaned:
                        await self._finish_reader(reader)
                        snapshot = await self._finalize_terminal(record)
                        self._resolve_termination_waiters(record, snapshot=snapshot)
                        return
                    self._resolve_termination_waiters(
                        record,
                        through=generation,
                        error=ProcessOperationError(
                            "termination_failed",
                            "process group could not be confirmed stopped",
                        ),
                    )
                if record.process.returncode is None:
                    wake_task = asyncio.create_task(record.wake.wait())
                    poll_task = asyncio.create_task(asyncio.sleep(backoff))
                    monitored = {wake_task, poll_task}
                    if not reader.done():
                        monitored.add(reader)
                    done, _ = await asyncio.wait(
                        monitored,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if reader in done:
                        reader.result()
                    pending_controls = (
                        task
                        for task in (wake_task, poll_task)
                        if task not in done
                    )
                    for task in pending_controls:
                        task.cancel()
                    await asyncio.gather(
                        wake_task, poll_task, return_exceptions=True
                    )
                    if wake_task in done:
                        record.wake.clear()
                        backoff = self.GROUP_POLL_INITIAL_SECONDS
                    elif poll_task in done:
                        backoff = min(backoff * 2, self.GROUP_POLL_MAX_SECONDS)
                    continue
                record.exit_code = record.process.returncode
                if not self._group_exists(record.process_group_id):
                    await self._finish_reader(reader)
                    snapshot = await self._finalize_terminal(record)
                    self._resolve_termination_waiters(record, snapshot=snapshot)
                    return
                record.wake.clear()
                try:
                    await asyncio.wait_for(record.wake.wait(), timeout=backoff)
                except TimeoutError:
                    backoff = min(backoff * 2, self.GROUP_POLL_MAX_SECONDS)
                else:
                    backoff = self.GROUP_POLL_INITIAL_SECONDS
        except asyncio.CancelledError:
            await asyncio.shield(self._cleanup_supervisor(record))
            raise
        except BaseException as error:
            record.supervisor_error = str(error)
            await asyncio.shield(self._cleanup_supervisor(record))
            raise
        finally:
            if reader is not None:
                if not reader.done():
                    reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)
            self._close_log(record.log_file)

    async def _drain_output(self, record: ProcessRecord) -> None:
        stream = record.process.stdout
        if stream is None:
            raise RuntimeError("process stdout pipe was not created")
        while True:
            chunk = await stream.read(self.READ_CHUNK_BYTES)
            if not chunk:
                return
            publish = False
            async with self._lock:
                remaining = max(0, self.config.max_log_bytes - record.log_bytes)
                if remaining:
                    written = chunk[:remaining]
                    record.log_file.write(written)
                    record.log_file.flush()
                    record.log_bytes += len(written)
                if len(chunk) > remaining and not record.log_truncated:
                    record.log_truncated = True
                    publish = True
                snapshot = record.snapshot()
            if publish:
                await record.committed.wait()
                if not record.discarded:
                    await self._publish_running_snapshot(record)

    async def _finish_reader(self, reader: asyncio.Task[None]) -> None:
        try:
            await asyncio.wait_for(
                asyncio.shield(reader),
                timeout=self.config.terminate_grace_seconds,
            )
        except TimeoutError:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)

    async def _terminate_group(self, record: ProcessRecord) -> bool:
        if not hasattr(os, "killpg"):
            raise ProcessOperationError(
                "unsupported", "process groups are unavailable on this platform"
            )
        term_sent = self._signal_group(record.process_group_id, signal.SIGTERM)
        if term_sent and await self._wait_group_gone(
            record, self.config.terminate_grace_seconds
        ):
            await self._wait_leader(record)
            return True
        if not term_sent and not self._group_exists(record.process_group_id):
            await self._wait_leader(record)
            return True
        kill_sent = self._signal_group(record.process_group_id, signal.SIGKILL)
        if (
            kill_sent or not self._group_exists(record.process_group_id)
        ) and await self._wait_group_gone(
            record, self.config.terminate_grace_seconds
        ):
            await self._wait_leader(record)
            return True
        return False

    async def _wait_group_gone(
        self, record: ProcessRecord, timeout: float
    ) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        delay = self.GROUP_POLL_INITIAL_SECONDS
        while self._group_exists(record.process_group_id):
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            try:
                await asyncio.wait_for(
                    record.wake.wait(), timeout=min(delay, remaining)
                )
                record.wake.clear()
            except TimeoutError:
                pass
            delay = min(delay * 2, self.GROUP_POLL_MAX_SECONDS)
        return True

    async def _wait_leader(self, record: ProcessRecord) -> None:
        if record.process.returncode is None:
            try:
                record.exit_code = await asyncio.wait_for(
                    asyncio.shield(record.process.wait()),
                    timeout=self.config.terminate_grace_seconds,
                )
            except TimeoutError:
                return
        else:
            record.exit_code = record.process.returncode

    async def _finalize_terminal(
        self, record: ProcessRecord
    ) -> ProcessSnapshot:
        async with self._publication_lock:
            async with self._lock:
                if record.status != "running":
                    return record.snapshot()
                if record.process.returncode is not None:
                    record.exit_code = record.process.returncode
                record.status = (
                    "stopped"
                    if record.stop_requested
                    else "completed"
                    if record.exit_code == 0
                    else "failed"
                )
                record.ended_at = datetime.now(UTC)
                snapshot = record.snapshot()
                self._trim_terminal_records_locked()
            await self._publish_snapshot(snapshot)
            return snapshot

    async def _publish_serialized(self, snapshot: ProcessSnapshot) -> None:
        async with self._publication_lock:
            await self._publish_snapshot(snapshot)

    async def _publish_running_snapshot(self, record: ProcessRecord) -> None:
        async with self._publication_lock:
            async with self._lock:
                if record.discarded or record.status != "running":
                    return
                snapshot = record.snapshot()
            await self._publish_snapshot(snapshot)

    async def _cleanup_supervisor(self, record: ProcessRecord) -> None:
        if record.discarded:
            return
        record.stop_requested = True
        record.stop_reason = record.stop_reason or "supervisor_cleanup"
        try:
            cleaned = await self._terminate_group(record)
            if cleaned:
                snapshot = await self._finalize_terminal(record)
                self._resolve_termination_waiters(record, snapshot=snapshot)
            else:
                self._resolve_termination_waiters(
                    record,
                    error=ProcessOperationError(
                        "termination_failed",
                        "supervisor cleanup could not stop process group",
                    ),
                )
        except BaseException as error:
            self._resolve_termination_waiters(record, error=error)

    async def _rollback_start(self, record: ProcessRecord) -> None:
        record.discarded = True
        try:
            await self._terminate_group(record)
        finally:
            record.committed.set()
            task = record.supervisor_task
            if task is not None and task is not asyncio.current_task():
                await asyncio.gather(task, return_exceptions=True)
            self._close_log(record.log_file)
            async with self._lock:
                self._records.pop(record.process_id, None)
            self._unlink_quietly(record.log_path)

    def _create_log(self) -> tuple[str, Path, BinaryIO]:
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        for _ in range(16):
            process_id = f"proc_{secrets.token_hex(16)}"
            log_path = self.processes_dir / f"{process_id}.log"
            try:
                descriptor = os.open(log_path, flags, 0o600)
            except FileExistsError:
                continue
            try:
                os.chmod(log_path, 0o600)
                return (
                    process_id,
                    log_path,
                    os.fdopen(descriptor, "wb", buffering=0),
                )
            except BaseException:
                os.close(descriptor)
                self._unlink_quietly(log_path)
                raise
        raise ProcessOperationError(
            "spawn_failed", "could not allocate a unique process log"
        )

    def _resolve_workdir(self, value: str | None) -> Path:
        if value is None:
            return self.workspace_path
        if not isinstance(value, str) or not value.strip():
            raise ProcessOperationError(
                "invalid_arguments", "workdir must be a non-empty string"
            )
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = self.workspace_path / candidate
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as error:
            raise ProcessOperationError("invalid_workdir", str(error)) from error
        if not resolved.is_dir() or not resolved.is_relative_to(self.workspace_path):
            raise ProcessOperationError(
                "invalid_workdir",
                "workdir must be a directory inside the workspace",
            )
        return resolved

    @classmethod
    def _validate_command(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ProcessOperationError(
                "invalid_arguments", "command must be a non-empty string"
            )
        if len(value.encode("utf-8")) > cls.COMMAND_MAX_BYTES:
            raise ProcessOperationError(
                "invalid_arguments", "command exceeds 32768 UTF-8 bytes"
            )
        return value

    def _validate_page_size(self, value: int | None) -> int:
        size = self.config.default_page_size if value is None else value
        if (
            isinstance(size, bool)
            or not isinstance(size, int)
            or not 1 <= size <= self.config.max_page_size
        ):
            raise ProcessOperationError(
                "invalid_arguments",
                f"page_size must be an integer from 1 to {self.config.max_page_size}",
            )
        return size

    def _validate_log_limit(self, value: int | None) -> int:
        limit = self.config.default_log_page_bytes if value is None else value
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= self.config.max_log_page_bytes
        ):
            raise ProcessOperationError(
                "invalid_arguments",
                f"limit_bytes must be an integer from 1 to {self.config.max_log_page_bytes}",
            )
        return limit

    def _get_record(self, process_id: str) -> ProcessRecord:
        if not isinstance(process_id, str):
            raise ProcessOperationError(
                "invalid_arguments", "process_id must be a string"
            )
        try:
            return self._records[process_id]
        except KeyError as error:
            raise ProcessOperationError(
                "process_not_found", "unknown process_id"
            ) from error

    @staticmethod
    def _request_stop_locked(
        record: ProcessRecord, reason: str, *, create_waiter: bool
    ) -> asyncio.Future[ProcessSnapshot] | None:
        record.stop_requested = True
        if record.stop_reason is None:
            record.stop_reason = reason
        record.termination_generation += 1
        generation = record.termination_generation
        waiter = (
            asyncio.get_running_loop().create_future()
            if create_waiter
            else None
        )
        if waiter is not None:
            record.termination_waiters[generation] = waiter
        record.wake.set()
        return waiter

    @staticmethod
    async def _wait_preserving_cancellation(task: asyncio.Task) -> bool:
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
            except BaseException:
                pass
        return cancelled

    async def _rollback_start_uninterruptibly(
        self, record: ProcessRecord
    ) -> None:
        cleanup = asyncio.create_task(self._rollback_start(record))
        await self._wait_preserving_cancellation(cleanup)
        cleanup.result()

    def _trim_terminal_records_locked(self) -> None:
        terminal = sorted(
            (
                record
                for record in self._records.values()
                if record.status != "running"
            ),
            key=lambda item: (item.ended_at or item.started_at, item.process_id),
        )
        excess = len(terminal) - self.config.max_terminal_records
        for record in terminal[: max(0, excess)]:
            self._records.pop(record.process_id, None)

    @staticmethod
    def _cursor_time(value: datetime) -> str:
        return value.isoformat().replace("+00:00", "Z")

    @staticmethod
    def _read_log_page(path: Path, offset: int, limit: int, size: int) -> bytes:
        with path.open("rb") as handle:
            handle.seek(offset)
            raw = handle.read(min(size - offset, limit + 3))
            if not raw:
                return b""
            maximum = min(limit, len(raw))
            for end in range(maximum, 0, -1):
                candidate = raw[:end]
                try:
                    candidate.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                else:
                    return candidate
            # A single valid UTF-8 codepoint can be four bytes. Make progress
            # without splitting it even when the requested byte limit is smaller.
            for end in range(maximum + 1, min(4, len(raw)) + 1):
                candidate = raw[:end]
                try:
                    candidate.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                else:
                    return candidate
            # Malformed input has no safe character boundary; consume one byte
            # and let the public UTF-8 decoder render its replacement marker.
            return raw[:1]

    @staticmethod
    def _signal_group(process_group_id: int, sig: signal.Signals) -> bool:
        try:
            os.killpg(process_group_id, sig)
            return True
        except ProcessLookupError:
            return False
        except PermissionError as error:
            raise ProcessOperationError(
                "termination_failed",
                "permission denied signaling process group",
            ) from error

    @staticmethod
    def _group_exists(process_group_id: int) -> bool:
        try:
            os.killpg(process_group_id, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    @staticmethod
    def _resolve_termination_waiters(
        record: ProcessRecord,
        *,
        through: int | None = None,
        snapshot: ProcessSnapshot | None = None,
        error: BaseException | None = None,
    ) -> None:
        upper = record.termination_generation if through is None else through
        for generation in sorted(tuple(record.termination_waiters)):
            if generation > upper:
                continue
            waiter = record.termination_waiters.pop(generation)
            if waiter.done():
                continue
            if error is not None:
                waiter.set_exception(error)
            elif snapshot is not None:
                waiter.set_result(snapshot)

    @staticmethod
    def _close_log(log_file: BinaryIO) -> None:
        if log_file.closed:
            return
        try:
            log_file.flush()
        finally:
            log_file.close()

    @staticmethod
    def _unlink_quietly(path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
