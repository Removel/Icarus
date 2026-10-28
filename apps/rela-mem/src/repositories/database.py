from sqlalchemy import BigInteger, CheckConstraint, Column, Index, MetaData, Table, Text, TIMESTAMP, inspect, text
from errors import ServiceError
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class Database:
    def __init__(self, settings):
        config = settings.postgres
        url = URL.create("postgresql+asyncpg", username=config.user,
                         password=settings.pg_password.get_secret_value(), host=config.host,
                         port=config.port, database=config.database)
        self.engine = create_async_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.schema = config.schema_name
        self.metadata = MetaData(schema=self.schema)
        self.entries = Table("profile_entries", self.metadata,
            Column("id", BigInteger, primary_key=True, autoincrement=True),
            Column("person_id", Text, nullable=False),
            Column("attr_key", Text, nullable=False),
            Column("attr_value", JSONB(none_as_null=False), nullable=False),
            Column("source", Text, nullable=False),
            Column("source_ref", Text),
            Column("status", Text, nullable=False, server_default="active"),
            Column("recorded_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("clock_timestamp()")),
            CheckConstraint("source IN ('user','agent','import','observer')"),
            CheckConstraint("status IN ('active','deleted')"))
        Index("idx_profile_current", self.entries.c.person_id, self.entries.c.attr_key,
              self.entries.c.recorded_at.desc(), self.entries.c.id.desc())

    async def initialize(self):
        async with self.engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{self.schema}"'))
            await connection.run_sync(self.metadata.create_all)
            await connection.run_sync(self._validate_table)

    def _validate_table(self, connection):
        inspector = inspect(connection)
        columns = {c["name"]: c for c in inspector.get_columns("profile_entries", schema=self.schema)}
        expected_types = {"id": "BIGINT", "person_id": "TEXT", "attr_key": "TEXT", "attr_value": "JSONB",
                          "source": "TEXT", "source_ref": "TEXT", "status": "TEXT", "recorded_at": "TIMESTAMP"}
        valid = set(columns) == set(expected_types)
        for name, expected in expected_types.items():
            column = columns.get(name, {})
            valid &= str(column.get("type")) == expected
            valid &= column.get("nullable") == (name == "source_ref")
        valid &= bool(columns.get("id", {}).get("default") or columns.get("id", {}).get("identity"))
        valid &= bool(columns.get("recorded_at", {}).get("default"))
        valid &= bool(getattr(columns.get("recorded_at", {}).get("type"), "timezone", False))
        valid &= inspector.get_pk_constraint("profile_entries", schema=self.schema).get("constrained_columns") == ["id"]
        indexes = inspector.get_indexes("profile_entries", schema=self.schema)
        valid &= any(index["name"] == "idx_profile_current" and index["column_names"] == ["person_id", "attr_key", "recorded_at", "id"] for index in indexes)
        checks = " ".join(item["sqltext"] for item in inspector.get_check_constraints("profile_entries", schema=self.schema))
        valid &= all(value in checks for value in ("source", "observer", "import", "status", "deleted", "active"))
        if not valid:
            raise ServiceError("CONFLICT", "existing profile_entries schema is incompatible; explicit migration required", 409)

    async def ping(self):
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def close(self):
        await self.engine.dispose()
