"""Task-local stable citations and independent output snapshots."""

from copy import deepcopy

from agent_app.plugins.web_search.pipeline import normalize_url


class SourceTable:
    def __init__(self):
        self.rows: dict[str, dict] = {}

    def add(self, source: dict) -> dict:
        key = normalize_url(source["url"])
        previous = self.rows.get(key)
        if previous is None:
            previous = {**deepcopy(source), "canonicalUrl": key, "citeIndex": len(self.rows) + 1}
            self.rows[key] = previous
        else:
            for name, value in source.items():
                if name not in {"url", "canonicalUrl", "citeIndex"} and value:
                    if not previous.get(name) or len(str(value)) > len(str(previous[name])):
                        previous[name] = deepcopy(value)
        return deepcopy(previous)

    def snapshot(self) -> tuple[dict, ...]:
        return tuple(deepcopy(list(self.rows.values())))
