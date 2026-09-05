"""持久化与监测组件统一组装。"""

from contextlib import contextmanager
from hashlib import sha256
import logging
import os
from pathlib import Path
import tempfile
from typing import Iterator

from apps.agent.src.agent_orchestration.hooks.hook_context import hook_context
from apps.agent.src.agent_orchestration.hooks.hook_registry import HookRegistry
from apps.agent.src.agent_orchestration.plugins.persistence.log_handler import (
    WorkspaceSessionFileHandler,
)
from apps.agent.src.agent_orchestration.plugins.persistence.json_state_store import (
    JsonStateStore,
)
from apps.agent.src.agent_orchestration.plugins.persistence.path_resolver import (
    DataPathResolver,
)
from apps.agent.src.agent_orchestration.plugins.persistence.redactor import Redactor
from apps.agent.src.agent_orchestration.plugins.persistence.session_identity import (
    SessionIdentity,
)
from apps.agent.src.agent_orchestration.plugins.persistence.trace_hook import (
    FileTraceHook,
)
from apps.agent.src.agent_orchestration.plugins.persistence.trace_writer import (
    FileTraceWriter,
)
from apps.agent.src.model_provider.types import (
    ImageAssetUnavailableError,
    ImagePart,
)


class ImageAssetError(ImageAssetUnavailableError):
    """本地图片无法安全导入或解析。"""


_IMAGE_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image/png", "png"),
    (b"\xff\xd8\xff", "image/jpeg", "jpg"),
    (b"GIF87a", "image/gif", "gif"),
    (b"GIF89a", "image/gif", "gif"),
)
MAX_IMAGE_BYTES = 20 * 1024 * 1024


class PersistenceRuntime:
    def __init__(
        self,
        data_dir: str | Path,
        workspace_path: str | Path,
        *,
        flush_every: int = 1,
        warning_file_size_bytes: int | None = None,
    ) -> None:
        self.resolver = DataPathResolver(data_dir)
        self.workspace_identity = SessionIdentity.create(
            workspace_path=workspace_path,
            session_id="workspace",
        )
        self.state_store = JsonStateStore()
        self.redactor = Redactor()
        self.trace_writer = FileTraceWriter(
            self.resolver,
            flush_every=flush_every,
            warning_file_size_bytes=warning_file_size_bytes,
        )
        self.trace_hook = FileTraceHook(self.trace_writer, self.redactor)
        self.log_handler = WorkspaceSessionFileHandler(
            self.resolver,
            self.workspace_identity,
        )
        self._hook_registry: HookRegistry | None = None
        self._logger: logging.Logger | None = None
        self._started = False

    @classmethod
    def from_env(
        cls,
        workspace_path: str | Path,
        **kwargs,
    ) -> "PersistenceRuntime":
        data_dir = os.getenv("ICARUS_DATA_DIR")
        if not data_dir:
            raise RuntimeError("ICARUS_DATA_DIR is required")
        return cls(
            data_dir=data_dir,
            workspace_path=workspace_path,
            **kwargs,
        )

    @property
    def is_running(self) -> bool:
        return self._started

    def start(
        self,
        hook_registry: HookRegistry | None = None,
        logger: logging.Logger | None = None,
        session_identity: SessionIdentity | None = None,
    ) -> None:
        if self._started:
            return
        self.resolver.ensure_workspace(self.workspace_identity)
        self.trace_writer.start()
        if hook_registry is not None:
            hook_registry.register("*", self.trace_hook)
            self._hook_registry = hook_registry
        self._logger = logger or logging.getLogger()
        if session_identity is not None:
            self.log_handler.bind_session(session_identity)
        self._logger.addHandler(self.log_handler)
        self._started = True

    def stop(
        self,
        drain: bool = True,
        logger: logging.Logger | None = None,
    ) -> None:
        if not self._started:
            return
        target_logger = logger or self._logger or logging.getLogger()
        target_logger.removeHandler(self.log_handler)
        if self._hook_registry is not None:
            self._hook_registry.unregister("*", self.trace_hook)
            self._hook_registry = None
        self.trace_writer.stop(drain=drain)
        self.log_handler.close()
        self._logger = None
        self._started = False

    @contextmanager
    def open_session(
        self,
        *,
        session_id: str | None = None,
    ) -> Iterator["PersistenceSession"]:
        identity = SessionIdentity.create(
            workspace_path=self.workspace_identity.workspace_path,
            session_id=session_id,
        )
        session = PersistenceSession(self, identity)
        yield session

    @contextmanager
    def session_scope(
        self,
        *,
        session_id: str | None = None,
        task_id: str | None = None,
    ) -> Iterator[SessionIdentity]:
        identity = SessionIdentity.create(
            workspace_path=self.workspace_identity.workspace_path,
            session_id=session_id,
        )
        with hook_context(
            {
                "workspace_path": str(identity.workspace_path),
                "workspace_key": identity.workspace_key,
                "session_id": identity.session_id,
                "task_id": task_id,
            },
            run_id=None,
        ):
            yield identity


class PersistenceSession:
    def __init__(
        self,
        runtime: PersistenceRuntime,
        identity: SessionIdentity,
    ) -> None:
        self.runtime = runtime
        self.identity = identity

    @contextmanager
    def context_scope(self) -> Iterator[SessionIdentity]:
        with hook_context(
            {
                "workspace_path": str(self.identity.workspace_path),
                "workspace_key": self.identity.workspace_key,
                "session_id": self.identity.session_id,
            },
            run_id=None,
        ):
            yield self.identity

    @contextmanager
    def task_scope(self, task_id: str) -> Iterator[SessionIdentity]:
        with hook_context(
            {
                "workspace_path": str(self.identity.workspace_path),
                "workspace_key": self.identity.workspace_key,
                "session_id": self.identity.session_id,
                "task_id": task_id,
            },
            run_id=None,
        ):
            yield self.identity

    def import_image(self, path: str | Path) -> ImagePart:
        source = Path(path).expanduser()
        try:
            with source.open("rb") as file:
                data = file.read(MAX_IMAGE_BYTES + 1)
        except OSError as error:
            raise ImageAssetError("image file is unavailable") from error
        return self.import_image_bytes(data)

    def import_image_bytes(
        self, data: bytes, media_type: str | None = None
    ) -> ImagePart:
        if len(data) > MAX_IMAGE_BYTES:
            raise ImageAssetError("image exceeds the maximum supported size")
        detected_media_type, extension = _detect_image_type(data)
        if media_type is not None and media_type != detected_media_type:
            raise ImageAssetError("image media type does not match file content")
        assets_dir = self.runtime.resolver.assets_dir(self.identity)
        assets_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        filename = f"{sha256(data).hexdigest()}.{extension}"
        target = assets_dir / filename
        if not target.exists():
            temporary_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    dir=assets_dir,
                    prefix=".image-",
                    delete=False,
                ) as temporary:
                    temporary.write(data)
                    temporary.flush()
                    os.fsync(temporary.fileno())
                    temporary_path = Path(temporary.name)
                temporary_path.replace(target)
                target.chmod(0o600)
            except OSError as error:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)
                raise ImageAssetError("image asset could not be stored") from error
        return ImagePart(
            source=f"assets/{filename}",
            source_type="asset",
            media_type=detected_media_type,
        )

    def resolve_image(self, image: ImagePart) -> Path:
        if image.source_type != "asset":
            raise ImageAssetError("image is not a session asset")
        relative = Path(image.source)
        if relative.is_absolute() or not relative.parts or relative.parts[0] != "assets":
            raise ImageAssetError("image asset reference is invalid")
        assets_dir = self.runtime.resolver.assets_dir(self.identity).resolve()
        target = (self.runtime.resolver.session_dir(self.identity) / relative).resolve()
        try:
            target.relative_to(assets_dir)
        except ValueError as error:
            raise ImageAssetError("image asset reference escapes session") from error
        if not target.is_file():
            raise ImageAssetError("image asset is unavailable")
        return target


def _detect_image_type(data: bytes) -> tuple[str, str]:
    for signature, media_type, extension in _IMAGE_SIGNATURES:
        if data.startswith(signature):
            return media_type, extension
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp", "webp"
    raise ImageAssetError("unsupported image format")
