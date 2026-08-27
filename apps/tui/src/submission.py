"""TUI-owned draft image and pending submission models."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DraftImage:
    reference: str
    path: Path

    @property
    def marker(self) -> str:
        return f"[#{self.reference}]"


@dataclass(frozen=True)
class PendingMessage:
    text: str
    images: tuple[DraftImage, ...] = ()

    @property
    def image_paths(self) -> tuple[Path, ...]:
        return tuple(image.path for image in self.images)

    def model_prompt(self) -> str:
        if not self.images:
            return self.text
        visible_text = self.text
        if not _without_markers(visible_text, self.images).strip():
            visible_text = "请分析所附图片。"
        mapping = "\n".join(
            f"{image.marker} 对应第 {index} 张附件图片"
            for index, image in enumerate(self.images, start=1)
        )
        return (
            f"{visible_text.rstrip()}\n\n"
            f"<attached_images>\n{mapping}\n</attached_images>"
        )


def referenced_images(
    text: str,
    images: tuple[DraftImage, ...] | list[DraftImage],
) -> tuple[DraftImage, ...]:
    positioned = []
    for image in images:
        position = text.find(image.marker)
        if position >= 0:
            positioned.append((position, image))
    positioned.sort(key=lambda item: item[0])
    return tuple(image for _, image in positioned)


def _without_markers(text: str, images: tuple[DraftImage, ...]) -> str:
    result = text
    for image in images:
        result = result.replace(image.marker, "")
    return result
