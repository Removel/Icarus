"""QQ receive path: persist first, notify only after successful insertion."""


class QQObservationService:
    def __init__(self, repository, parser, *, notify):
        self.repository, self.parser, self.notify = repository, parser, notify

    async def receive(self, raw):
        draft = self.parser.parse(raw)
        if draft is None:
            return None
        item, inserted = await self.repository.upsert_revision(draft)
        if inserted:
            self.notify(item.chat_id, immediate=draft.strong_signal)
        return item, inserted
