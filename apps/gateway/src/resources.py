"""Image transport for the existing Runtime inbox and session assets."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import re
import tempfile
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from agent_app.application import SessionNotFoundError
from agent_app.config import get_config
from icarus.core.identity import SessionIdentity
from icarus.runtime.assets import MAX_IMAGE_BYTES
from icarus.runtime.paths import DataPathResolver

from apps.gateway.src.protocol.models import ResourceRefModel


_INCOMING = re.compile(r"webui/[a-f0-9]{32}\.(png|jpg|gif|webp)\Z")
_ASSET = re.compile(r"assets/[a-f0-9]{64}\.(png|jpg|gif|webp)\Z")
_TYPES = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
}


def image_type(data: bytes) -> str:
    # Match the formats accepted by PersistenceSession.import_image_bytes.
    for signature, media_type in (
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"\xff\xd8\xff", "image/jpeg"),
        (b"GIF87a", "image/gif"),
        (b"GIF89a", "image/gif"),
    ):
        if data.startswith(signature):
            return media_type
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    raise HTTPException(415, "Unsupported image format")


class ImageResources:
    def __init__(self, data_dir: str | Path | None):
        self.data_dir = data_dir

    def resolver(self) -> DataPathResolver:
        data_dir = (
            self.data_dir
            if self.data_dir is not None
            else get_config().icarus_data_dir
        )
        if data_dir is None:
            raise HTTPException(503, "Image storage is unavailable")
        return DataPathResolver(data_dir)

    def stage(self, data: bytes, media_type: str) -> ResourceRefModel:
        resolver = self.resolver()
        directory = resolver.incoming_dir / "webui"
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._contained(resolver, directory)
        resource_id = f"webui/{uuid4().hex}.{_TYPES[media_type]}"
        target = resolver.incoming_dir / resource_id
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=directory, prefix=".upload-", delete=False
            ) as file:
                temporary = Path(file.name)
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            temporary.chmod(0o600)
            temporary.replace(target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return ResourceRefModel(resource_id=resource_id, media_type=media_type)

    @staticmethod
    def _contained(resolver: DataPathResolver, target: Path) -> Path:
        try:
            resolved = target.resolve(strict=True)
            resolved.relative_to(resolver.data_dir)
        except (ValueError, OSError) as error:
            raise HTTPException(404, "Image not found") from error
        return resolved

    def read(self, resource_id: str, workspace_path: str | None, session_id: str | None):
        resolver = self.resolver()
        if _INCOMING.fullmatch(resource_id):
            root = resolver.incoming_dir
        elif _ASSET.fullmatch(resource_id) and workspace_path and session_id:
            try:
                identity = SessionIdentity.create(workspace_path, session_id)
                root = resolver.session_dir(identity)
            except ValueError as error:
                raise HTTPException(400, "Invalid session") from error
        else:
            raise HTTPException(400, "Invalid image reference")
        target = root / resource_id
        resolved = self._contained(resolver, target)
        try:
            resolved.relative_to(root.resolve())
            if target.is_symlink() or not resolved.is_file():
                raise HTTPException(404, "Image not found")
            with resolved.open("rb") as file:
                data = file.read(MAX_IMAGE_BYTES + 1)
        except (ValueError, OSError) as error:
            raise HTTPException(404, "Image not found") from error
        if len(data) > MAX_IMAGE_BYTES:
            raise HTTPException(413, "Image exceeds 20 MiB")
        return data, image_type(data)


def image_routes(runtime, *, data_dir: str | Path | None = None) -> APIRouter:
    router = APIRouter()
    resources = ImageResources(data_dir)

    @router.post("/resources", response_model=ResourceRefModel)
    async def upload(request: Request):
        media_type = (
            request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        )
        if media_type not in _TYPES:
            raise HTTPException(415, "Use PNG, JPEG, GIF or WebP")
        length = request.headers.get("content-length")
        if length is not None:
            try:
                declared = int(length)
            except ValueError as error:
                raise HTTPException(400, "Invalid Content-Length") from error
            if declared < 0:
                raise HTTPException(400, "Invalid Content-Length")
            if declared > MAX_IMAGE_BYTES:
                raise HTTPException(413, "Image exceeds 20 MiB")
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > MAX_IMAGE_BYTES:
                raise HTTPException(413, "Image exceeds 20 MiB")
            data.extend(chunk)
        payload = bytes(data)
        if image_type(payload) != media_type:
            raise HTTPException(415, "Image media type does not match file content")
        try:
            return await asyncio.to_thread(resources.stage, payload, media_type)
        except OSError as error:
            raise HTTPException(503, "Image storage is unavailable") from error

    @router.get("/resources")
    async def read(
        resource_id: str,
        workspace_path: str | None = None,
        session_id: str | None = None,
    ):
        if _ASSET.fullmatch(resource_id):
            if (
                not workspace_path
                or not session_id
                or not re.fullmatch(r"[A-Za-z0-9._-]+", session_id)
                or session_id in {".", ".."}
            ):
                raise HTTPException(400, "Session is required for saved images")
            try:
                await runtime.get_session_status(workspace_path, session_id)
            except SessionNotFoundError as error:
                raise HTTPException(404, "Session not found") from error
            except ValueError as error:
                raise HTTPException(400, "Invalid session") from error
        data, media_type = await asyncio.to_thread(
            resources.read, resource_id, workspace_path, session_id
        )
        return Response(
            data,
            media_type=media_type,
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Disposition": "inline",
            },
        )

    return router
