"""后台 Trace JSONL Writer。"""

from dataclasses import dataclass
import logging
from pathlib import Path
from queue import Empty, Queue
import threading
from typing import TextIO

from apps.agent.src.agent_orchestration.plugins.persistence.path_resolver import (
    DataPathResolver,
)
from apps.agent.src.agent_orchestration.plugins.persistence.session_identity import (
    SessionIdentity,
)
from apps.agent.src.agent_orchestration.plugins.persistence.trace_record import (
    TraceRecord,
)


logger = logging.getLogger(__name__)
_STOP = object()


@dataclass(frozen=True)
class TraceWriteRequest:
    identity: SessionIdentity
    record: TraceRecord


class FileTraceWriter:
    def __init__(
        self,
        resolver: DataPathResolver,
        flush_every: int = 1,
        warning_file_size_bytes: int | None = None,
    ) -> None:
        if flush_every < 1:
            raise ValueError("flush_every must be positive")
        self.resolver = resolver
        self.flush_every = flush_every
        self.warning_file_size_bytes = warning_file_size_bytes
        self._queue: Queue[TraceWriteRequest | object] = Queue()
        self._thread: threading.Thread | None = None
        self._accepting = False
        self._state_lock = threading.Lock()
        self._written_count = 0
        self._written_bytes = 0
        self._failure_count = 0
        self._file_sizes: dict[Path, int] = {}

    @property
    def written_count(self) -> int:
        return self._written_count

    @property
    def written_bytes(self) -> int:
        return self._written_bytes

    @property
    def failure_count(self) -> int:
        return self._failure_count

    @property
    def pending_count(self) -> int:
        return self._queue.unfinished_tasks

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        with self._state_lock:
            if self.is_running:
                return
            self._accepting = True
            self._thread = threading.Thread(
                target=self._run,
                name="icarus-trace-writer",
                daemon=True,
            )
            self._thread.start()

    def offer(self, request: TraceWriteRequest) -> bool:
        if not self._accepting:
            self._failure_count += 1
            logger.warning("Trace writer is not accepting records")
            return False
        self._queue.put(request)
        return True

    def drain(self) -> None:
        self._queue.join()

    def stop(self, drain: bool = True) -> None:
        with self._state_lock:
            if self._thread is None:
                self._accepting = False
                return
            self._accepting = False
            if drain:
                self._queue.join()
            else:
                self._discard_pending()
            self._queue.put(_STOP)
            self._thread.join()
            self._thread = None

    def _run(self) -> None:
        handles: dict[Path, TextIO] = {}
        writes_since_flush: dict[Path, int] = {}
        try:
            while True:
                item = self._queue.get()
                try:
                    if item is _STOP:
                        return
                    if not isinstance(item, TraceWriteRequest):
                        continue
                    try:
                        self._write(item, handles, writes_since_flush)
                    except Exception:
                        self._failure_count += 1
                        logger.exception("Trace record write failed")
                finally:
                    self._queue.task_done()
        finally:
            for handle in handles.values():
                try:
                    handle.flush()
                    handle.close()
                except OSError:
                    logger.exception("Trace file close failed")

    def _write(
        self,
        request: TraceWriteRequest,
        handles: dict[Path, TextIO],
        writes_since_flush: dict[Path, int],
    ) -> None:
        path = self.resolver.trace_file(request.identity)
        self.resolver.ensure_session(request.identity)
        handle = handles.get(path)
        if handle is None:
            initial_size = path.stat().st_size if path.exists() else 0
            handle = path.open("a", encoding="utf-8")
            try:
                path.chmod(0o600)
            except OSError:
                pass
            handles[path] = handle
            writes_since_flush[path] = 0
            self._file_sizes[path] = initial_size

        line = request.record.to_json_line() + "\n"
        encoded_size = len(line.encode("utf-8"))
        handle.write(line)
        writes_since_flush[path] += 1
        self._written_count += 1
        self._written_bytes += encoded_size
        self._file_sizes[path] += encoded_size

        if writes_since_flush[path] >= self.flush_every:
            handle.flush()
            writes_since_flush[path] = 0

        if (
            self.warning_file_size_bytes is not None
            and self._file_sizes[path] >= self.warning_file_size_bytes
        ):
            logger.warning(
                "Trace file exceeds warning threshold: path=%s size=%s",
                path,
                self._file_sizes[path],
            )

    def _discard_pending(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except Empty:
                return
            else:
                self._queue.task_done()
