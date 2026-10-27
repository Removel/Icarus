"""Immutable configuration for the Icarus terminal client."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TuiConfig:
    """Runtime configuration shared by the TUI entry point and application."""

    workspace_path: Path
    gateway_url: str = "ws://127.0.0.1:8765/rpc"
    session_id: str | None = None
    resource_root: Path | None = None
    app_version: str | None = None
    reduced_motion: bool = False


def build_config(
    *,
    cwd: Path,
    gateway_url: str,
    session_id: str | None,
    data_dir: Path,
) -> TuiConfig:
    """Build a config from the launch directory and Icarus data directory."""

    workspace_path = Path(cwd).expanduser().resolve()
    resolved_data_dir = Path(data_dir).expanduser().resolve()
    return TuiConfig(
        workspace_path=workspace_path,
        gateway_url=gateway_url,
        session_id=session_id,
        resource_root=resolved_data_dir / "incoming",
    )
