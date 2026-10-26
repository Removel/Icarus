"""Pure search-result parsing, normalization and ranking; no query orchestration."""

from __future__ import annotations

from datetime import datetime, timezone
import math
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


PAGE_BUDGET = 6000
TOTAL_BUDGET = 24000


def normalize_url(raw: str) -> str:
    try:
        url = urlsplit(raw.strip())
        host = (url.hostname or "").lower().removeprefix("www.")
        if ":" in host:
            host = f"[{host}]"
        port = url.port
        if port and (url.scheme, port) not in {("http", 80), ("https", 443)}:
            host += f":{port}"
        query = urlencode([(k, v) for k, v in parse_qsl(url.query, keep_blank_values=True)
                           if not re.match(r"^(utm_|spm$|from$|ref$|share_|scene$|src$|source$)", k, re.I)])
        return urlunsplit((url.scheme.lower(), host, url.path.rstrip("/") or "/", query, ""))
    except ValueError:
        return raw.strip()


def valid_url(raw: object) -> bool:
    if not isinstance(raw, str) or len(raw) > 8192:
        return False
    try:
        url = urlsplit(raw)
        return url.scheme in {"http", "https"} and bool(url.hostname) and not (url.username or url.password)
    except ValueError:
        return False


def parse_sources(rows: object) -> list[dict]:
    if not isinstance(rows, list):
        raise ValueError("Search response has no results array")
    sources = []
    for row in rows[:50]:
        if not isinstance(row, dict) or not valid_url(row.get("url")):
            continue
        source = {"url": row["url"], "title": str(row.get("title") or row["url"])[:500]}
        for key, aliases in {
            "snippet": ("snippet", "summary"), "siteName": ("site_name", "siteName"),
            "publishedAt": ("published_at", "publishedAt", "date"),
            "provider": ("provider",), "siteIcon": ("site_icon", "siteIcon"),
        }.items():
            value = next((row.get(k) for k in aliases if isinstance(row.get(k), str) and row[k].strip()), None)
            if value:
                source[key] = value[:1000]
        # Search content never counts as a successful Fetch.
        for key in ("rerank_score", "authority"):
            value = row.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                source[key] = value
        sources.append(source)
    return sources


def rank_sources(sources: list[dict], question: str) -> list[dict]:
    terms = [s for s in re.split(r"[\s,，。？?]+", question) if len(s) > 1]
    def score(s):
        n = min(3, len(s.get("snippet", "")) / 120)
        n += bool(s.get("siteName")) + bool(s.get("provider"))
        n += 2 * any(term in s["title"] for term in terms)
        try:
            stamp = datetime.fromisoformat(s["publishedAt"].replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            days = (datetime.now(timezone.utc) - stamp).total_seconds() / 86400
            n += 6 if 0 <= days < 30 else 3 if 0 <= days < 180 else 1 if 0 <= days < 730 else 0
        except (KeyError, TypeError, ValueError):
            pass
        return n
    return sorted(sources, key=score, reverse=True)
