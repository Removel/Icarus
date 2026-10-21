from datetime import datetime

from sqlalchemy import Text, and_, cast, func, insert, or_, select


class ProfileRepository:
    def __init__(self, database):
        self.database = database
        self.table = database.entries

    async def append(self, person_id: str, attr_key: str, value, source, source_ref, recorded_at, status):
        async with self.database.sessions.begin() as session:
            query = insert(self.table).values(person_id=person_id, attr_key=attr_key, attr_value=value,
                source=source, source_ref=source_ref, recorded_at=recorded_at, status=status).returning(self.table)
            return dict((await session.execute(query)).mappings().one())

    async def current(self, person_id: str, q: str, keys: list[str], after: str, limit: int):
        table = self.table
        latest = select(table).where(table.c.person_id == person_id).distinct(table.c.attr_key).order_by(
            table.c.attr_key, table.c.recorded_at.desc(), table.c.id.desc()).subquery()
        query = select(latest).where(latest.c.status == "active", latest.c.attr_key > after)
        if keys:
            query = query.where(latest.c.attr_key.in_(keys))
        if q:
            pattern = "%" + q.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_") + "%"
            query = query.where(or_(latest.c.attr_key.ilike(pattern, escape="\\"),
                                   cast(latest.c.attr_value, Text).ilike(pattern, escape="\\")))
        async with self.database.sessions() as session:
            result = await session.execute(query.order_by(latest.c.attr_key).limit(limit))
            return [dict(row) for row in result.mappings()]

    async def history(self, person_id: str, key: str, before: tuple[datetime, int] | None, limit: int):
        t = self.table
        query = select(t).where(t.c.person_id == person_id, t.c.attr_key == key)
        if before:
            time, row_id = before
            query = query.where(or_(t.c.recorded_at < time, and_(t.c.recorded_at == time, t.c.id < row_id)))
        async with self.database.sessions() as session:
            result = await session.execute(query.order_by(t.c.recorded_at.desc(), t.c.id.desc()).limit(limit))
            return [dict(row) for row in result.mappings()]
