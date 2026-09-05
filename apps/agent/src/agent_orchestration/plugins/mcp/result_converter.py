"""Convert MCP Tool results into Icarus Tool results."""

from __future__ import annotations

import base64
import binascii
from typing import Any

from apps.agent.src.agent_orchestration.plugins.mcp.models import MCPCallResult
from apps.agent.src.agent_orchestration.plugins.persistence.runtime import (
    MAX_IMAGE_BYTES,
    PersistenceSession,
)
from apps.agent.src.agent_orchestration.plugins.persistence.redactor import Redactor
from apps.agent.src.agent_orchestration.tools import ToolExecutionResult


MAX_MCP_IMAGES = 16


class MCPResultConverter:
    def __init__(
        self, persistence: PersistenceSession | None = None,
        redactor: Redactor | None = None,
    ) -> None:
        self.persistence = persistence
        self.redactor = redactor or Redactor()

    def convert(self, result: MCPCallResult) -> ToolExecutionResult:
        contents: list[dict[str, Any]] = []
        error_texts: list[str] = []
        images = []
        image_bytes = 0
        for content in result.content:
            if content.type == "text":
                text = self.redactor.redact_text(str(content.data))
                contents.append({"type": "text", "text": text})
                if text.strip():
                    error_texts.append(text)
                continue
            if content.type == "image":
                self._check_image_count(images)
                image, size = self._import_image(
                    content.data, content.media_type, image_bytes
                )
                image_bytes += size
                images.append(image)
                contents.append(
                    {
                        "type": "image",
                        "source": image.source,
                        "media_type": image.media_type,
                    }
                )
                continue
            if content.type == "resource" and isinstance(content.data, dict):
                blob = content.data.get("blob")
                media_type = content.data.get("mimeType", content.data.get("mime_type"))
                if (
                    isinstance(blob, str)
                    and isinstance(media_type, str)
                    and media_type.startswith("image/")
                ):
                    self._check_image_count(images)
                    image, size = self._import_image(
                        blob, media_type, image_bytes
                    )
                    image_bytes += size
                    images.append(image)
                    contents.append(
                        {
                            "type": "image",
                            "source": image.source,
                            "media_type": image.media_type,
                            "resource_uri": self._safe_uri(
                                content.data.get("uri")
                            ),
                        }
                    )
                    continue
                if isinstance(blob, str):
                    contents.append(
                        {
                            "type": "resource",
                            "uri": self._safe_uri(content.data.get("uri")),
                            "media_type": media_type,
                            "binary_unsupported": True,
                        }
                    )
                    continue
            if content.type == "audio":
                contents.append(
                    {
                        "type": "audio",
                        "media_type": content.media_type,
                        "unsupported": True,
                    }
                )
                continue
            if content.type == "unknown":
                contents.append(
                    {
                        "type": str(
                            content.metadata.get("original_type", "unknown")
                        ),
                        "unsupported": True,
                    }
                )
                continue
            contents.append(
                {
                    "type": content.type,
                    "data": self.redactor.redact(content.data),
                    "media_type": content.media_type,
                    "metadata": self.redactor.redact(dict(content.metadata)),
                }
            )
        output: dict[str, Any] = {
            "content": self.redactor.redact(contents)
        }
        if result.structured_content is not None:
            output["structured_content"] = self.redactor.redact(
                result.structured_content
            )
        if result.metadata:
            output["metadata"] = self.redactor.redact(dict(result.metadata))
        if result.is_error:
            return ToolExecutionResult(
                success=False,
                output=output,
                error="\n".join(error_texts) or "MCP Tool returned an error",
                images=tuple(images),
            )
        return ToolExecutionResult(
            success=True, output=output, images=tuple(images)
        )

    def _import_image(
        self, data: Any, media_type: str | None, current_bytes: int
    ):
        if self.persistence is None:
            raise RuntimeError("MCP image persistence is unavailable")
        if not isinstance(data, str):
            raise ValueError("MCP image data must be base64 text")
        remaining = MAX_IMAGE_BYTES - current_bytes
        if remaining <= 0:
            raise ValueError("MCP images exceed the maximum supported size")
        max_encoded_size = ((remaining + 2) // 3) * 4
        if len(data) > max_encoded_size:
            raise ValueError("MCP images exceed the maximum supported size")
        try:
            decoded = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError("MCP image data is not valid base64") from error
        if current_bytes + len(decoded) > MAX_IMAGE_BYTES:
            raise ValueError("MCP images exceed the maximum supported size")
        return self.persistence.import_image_bytes(decoded, media_type), len(decoded)

    def _safe_uri(self, value: Any) -> Any:
        return self.redactor.redact_text(value) if isinstance(value, str) else value

    @staticmethod
    def _check_image_count(images: list) -> None:
        if len(images) >= MAX_MCP_IMAGES:
            raise ValueError("MCP result contains too many images")
