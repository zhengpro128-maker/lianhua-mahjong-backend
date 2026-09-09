"""存储后端兼容门面与运行时选择。

业务调用继续从本模块导入 ``Storage``、``PostgresStorage`` 或共享
``storage``；公共 CRUD 位于 :mod:`app.storage.base`，数据库方言差异分别
位于 SQLite/PostgreSQL 适配器中。
"""

import os
from typing import Optional
from urllib.parse import quote

from dotenv import load_dotenv

from .base import _new_id, _now
from .postgres import PostgresStorage
from .sqlite import DEFAULT_DB_PATH, SQLiteStorage, Storage


_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 环境变量优先，backend/.env 仅作回退。
load_dotenv(os.path.join(_BASE_DIR, '.env'))


_PG_DEFAULTS = {
    'PG_HOST': '127.0.0.1',
    'PG_PORT': '6543',
    'PG_USER': '******',
    'PG_DATABASE': '******',
}


def _enabled(name: str) -> bool:
    return os.environ.get(name, '').strip().lower() in {'1', 'true', 'yes', 'on'}


def postgres_dsn() -> Optional[str]:
    """Resolve PostgreSQL configuration; local development may fall back to SQLite."""
    database_url = os.environ.get('DATABASE_URL', '').strip()
    if database_url:
        return database_url
    password = os.environ.get('PG_PASSWORD')
    if not password:
        if _enabled('REQUIRE_POSTGRES'):
            raise RuntimeError(
                'REQUIRE_POSTGRES is enabled but DATABASE_URL/PG_PASSWORD is missing')
        return None
    host = os.environ.get('PG_HOST', _PG_DEFAULTS['PG_HOST'])
    port = os.environ.get('PG_PORT', _PG_DEFAULTS['PG_PORT'])
    user = os.environ.get('PG_USER', _PG_DEFAULTS['PG_USER'])
    database = os.environ.get('PG_DATABASE', _PG_DEFAULTS['PG_DATABASE'])
    user_encoded = quote(user, safe='')
    password_encoded = quote(password, safe='')
    dsn = (
        f'postgresql://{user_encoded}:{password_encoded}'
        f'@{host}:{port}/{database}'
    )
    sslmode = os.environ.get('PG_SSLMODE', '').strip()
    return f'{dsn}?sslmode={quote(sslmode, safe="")}' if sslmode else dsn


def _resolve_storage():
    dsn = postgres_dsn()
    return PostgresStorage(dsn) if dsn else Storage()


storage = _resolve_storage()


__all__ = [
    'DEFAULT_DB_PATH',
    'PostgresStorage',
    'SQLiteStorage',
    'Storage',
    'postgres_dsn',
    'storage',
]
