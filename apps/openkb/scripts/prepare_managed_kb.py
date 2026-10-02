"""Create the configured Icarus knowledge base before serving requests."""

from __future__ import annotations

import os
from pathlib import Path

from openkb.api_helpers import _is_kb_dir
from openkb.cli import initialize_kb
from openkb.config import (
    kb_root_dir,
    load_config,
    register_kb_alias,
    save_config,
    validate_kb_name,
)


def configure_template(model: str) -> None:
    """Keep REST-created KBs on the model selected for this deployment."""
    template = Path.cwd() / "config.yaml"
    config = load_config(template)
    config["model"] = model
    save_config(template, config)


def main() -> None:
    knowledge_base = validate_kb_name(
        os.environ.get("OPENKB_ICARUS_DEFAULT_KB", "icarus-project")
    )
    model = os.environ.get(
        "OPENKB_ICARUS_MODEL", "deepseek/deepseek-v4-flash"
    ).strip()
    if not model:
        raise SystemExit("OPENKB_ICARUS_MODEL must be a non-empty model name.")
    configure_template(model)
    target = (kb_root_dir() / knowledge_base).resolve()
    if not _is_kb_dir(target):
        initialize_kb(target, model=model)
    register_kb_alias(knowledge_base, target)
    Path("/data/backups").mkdir(parents=True, exist_ok=True)
    print("openkb-managed-kb-ready")


if __name__ == "__main__":
    main()
