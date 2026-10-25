"""QQ facts constructed from budgeted excerpts, not unbounded originals."""


import re
import unicodedata


def readable_text(item):
    """Text eligibility is source-owned; media placeholders and standalone @ are not text."""
    text = '\n'.join(part.value for part in item.content if part.kind == 'text')
    text = re.sub(r'(?<!\w)@(?:[0-9]+|all)\b', '', text).strip()
    return text if any(unicodedata.category(char)[0] in ('L', 'N') for char in text) else ''


class QQFactService:
    source = 'qq'
    known_facts = frozenset({'source', 'chat_id', 'scope_kind', 'item_count', 'mentions_bot',
        'is_self_only', 'text', 'sender_ids', 'sender_names', 'permission_level',
        'newest_sent_at', 'oldest_observed_at', 'has_readable_text'})

    text = staticmethod(readable_text)

    def build_facts(self, items, *, permission_level):
        if not items:
            raise ValueError('Cannot build facts for an empty selection')
        return {'source': 'qq', 'chat_id': items[0].item.chat_id,
            'scope_kind': items[0].item.locator['chat_kind'], 'item_count': len(items),
            'mentions_bot': any(value.item.detail.get('mentions_bot') or value.item.detail.get('bot_name_hit')
                                for value in items),
            'is_self_only': all(value.item.is_self for value in items),
            'has_readable_text': any(readable_text(value.item) for value in items),
            'text': '\n'.join(value.excerpt for value in items),
            'sender_ids': tuple(dict.fromkeys(value.item.sender_id for value in items)),
            'sender_names': tuple(dict.fromkeys(value.item.sender_name for value in items if value.item.sender_name)),
            'permission_level': permission_level,
            'newest_sent_at': max(value.item.sent_at for value in items),
            'oldest_observed_at': min(value.item.observed_at for value in items)}
