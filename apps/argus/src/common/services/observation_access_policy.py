"""One content-exit policy for observation bodies and visible text metadata."""

import json
import unicodedata

from ..schemas.observation import item_text


def normalized(value):
    return unicodedata.normalize('NFKC', value).casefold()


class ObservationAccessPolicy:
    def __init__(self, sensitive_keywords=(), *, permission_for=lambda _: 2):
        self.keywords = tuple(normalized(value) for value in sensitive_keywords if value.strip())
        self.permission_for = permission_for

    def sensitive(self, value):
        text = normalized(value)
        return any(keyword in text for keyword in self.keywords)

    def allows(self, item):
        text = '\n'.join((item_text(item), item.sender_name or '', json.dumps(item.locator, ensure_ascii=False)))
        return self.permission_for(item.chat_id) >= 1 and not self.sensitive(text)
