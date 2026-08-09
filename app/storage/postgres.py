"""PostgreSQL 连接、建表与兼容迁移。"""

import psycopg
from loguru import logger
from psycopg.rows import dict_row

from .base import BaseStorage, load_schema


_SCHEMA = load_schema('schema_postgres.sql')


class PostgresStorage(BaseStorage):
    """Supabase pooler 或任意 PostgreSQL 的同步存储适配器。"""

    placeholder = '%s'

    def __init__(self, dsn: str):
        self.dsn = dsn

    def _conn(self) -> psycopg.Connection:
        return psycopg.connect(self.dsn, row_factory=dict_row)

    def init(self) -> None:
        logger.info("数据库后端: PostgreSQL")
        with self._conn() as conn:
            for statement in _SCHEMA.split(';'):
                statement = statement.strip()
                if statement:
                    conn.execute(statement)
            self._migrate(conn)

    @staticmethod
    def _migrate(conn: psycopg.Connection) -> None:
        row = conn.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'room_seats' AND column_name = 'player_id'"
        ).fetchone()
        if row is None:
            conn.execute('ALTER TABLE room_seats ADD COLUMN player_id TEXT')
