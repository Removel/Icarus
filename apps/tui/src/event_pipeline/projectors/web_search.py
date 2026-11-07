"""Project validated web sources into task-local terminal display facts."""

from urllib.parse import urlsplit

from apps.tui.src.event_pipeline.actions import UpdateWebSources


class WebSearchProjector:
    def project(self, update, *, historical=False):
        if update.task_id is None:
            return ()
        if update.payload.get("status") not in ("searching", "complete", "empty", "error", "stopped"):
            return ()
        sources = update.payload.get("sources")
        if not isinstance(sources, list):
            return ()
        indices = set()
        for row in sources:
            if not isinstance(row, dict):
                continue
            index, url = row.get("citeIndex"), row.get("url")
            if type(index) is not int or index < 1 or not isinstance(url, str) or not isinstance(row.get("title"), str):
                continue
            try:
                parsed = urlsplit(url)
                valid = parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password
            except ValueError:
                valid = False
            if valid:
                indices.add(index)
        return (UpdateWebSources(update.task_id, tuple(sorted(indices))),)
