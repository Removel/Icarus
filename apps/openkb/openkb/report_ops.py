"""Delete generated reports without rewriting knowledge pages or sources."""

from pathlib import Path

from openkb.locks import kb_ingest_lock
from openkb.page_ops import validate_page_ref


def delete_report(kb_dir: Path, path: str) -> dict:
    section, stem = validate_page_ref(path.removesuffix(".md"), allowed=("reports",))
    target = f"{section}/{stem}"
    wiki = (kb_dir / "wiki").resolve()
    with kb_ingest_lock(kb_dir / ".openkb"):
        reports = wiki / "reports"
        page = reports / f"{stem}.md"
        if reports.is_symlink() or page.is_symlink() or not page.resolve().is_relative_to(wiki):
            raise ValueError("Report path must stay inside the knowledge base.")
        if not page.is_file():
            return {"status": "not_found", "target": target}
        page.unlink()
    return {"status": "deleted", "target": target, "files_changed": 1}
