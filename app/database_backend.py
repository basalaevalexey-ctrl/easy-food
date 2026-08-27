from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from typing import Any


AUTO_ID_TABLES = {
    "users",
    "food_entries",
    "user_events",
    "user_achievements",
    "daily_missions",
    "water_entries",
    "reminder_logs",
    "broadcast_logs",
    "lifecycle_pushes",
    "referrals",
    "streak_freezes",
    "external_identities",
    "email_credentials",
    "password_reset_tokens",
    "competitions",
    "competition_participants",
    "competition_daily_scores",
}


class CompatRow(Mapping[str, Any]):
    """Row supporting both sqlite-style numeric and mapping access."""

    def __init__(self, columns: tuple[str, ...], values: tuple[Any, ...]) -> None:
        self._columns = columns
        self._values = values
        self._mapping = dict(zip(columns, values, strict=True))

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return self._mapping[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._columns)

    def __len__(self) -> int:
        return len(self._columns)

    def keys(self):
        return self._mapping.keys()


def compat_row_factory(cursor):
    columns = tuple(column.name for column in (cursor.description or ()))

    def make_row(values: tuple[Any, ...]) -> CompatRow:
        return CompatRow(columns, values)

    return make_row


POSTGRES_COMPAT_FUNCTIONS = """
CREATE OR REPLACE FUNCTION nyam_sqlite_timestamp(value TEXT, modifiers TEXT[])
RETURNS TIMESTAMP
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    result TIMESTAMP;
    modifier TEXT;
BEGIN
    IF lower(value) = 'now' THEN
        result := timezone('UTC', CURRENT_TIMESTAMP);
    ELSE
        result := value::timestamp;
    END IF;

    FOREACH modifier IN ARRAY modifiers LOOP
        IF lower(modifier) = 'localtime' THEN
            result := result + INTERVAL '3 hours';
        ELSE
            result := result + modifier::interval;
        END IF;
    END LOOP;
    RETURN result;
EXCEPTION WHEN OTHERS THEN
    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION nyam_date(value TEXT, VARIADIC modifiers TEXT[])
RETURNS TEXT
LANGUAGE sql
STABLE
AS $$
    SELECT to_char(nyam_sqlite_timestamp(value, modifiers), 'YYYY-MM-DD')
$$;

CREATE OR REPLACE FUNCTION nyam_date(value TEXT)
RETURNS TEXT
LANGUAGE sql
STABLE
AS $$
    SELECT to_char(nyam_sqlite_timestamp(value, ARRAY[]::TEXT[]), 'YYYY-MM-DD')
$$;

CREATE OR REPLACE FUNCTION nyam_datetime(value TEXT, VARIADIC modifiers TEXT[])
RETURNS TEXT
LANGUAGE sql
STABLE
AS $$
    SELECT to_char(nyam_sqlite_timestamp(value, modifiers), 'YYYY-MM-DD HH24:MI:SS')
$$;

CREATE OR REPLACE FUNCTION nyam_datetime(value TEXT)
RETURNS TEXT
LANGUAGE sql
STABLE
AS $$
    SELECT to_char(nyam_sqlite_timestamp(value, ARRAY[]::TEXT[]), 'YYYY-MM-DD HH24:MI:SS')
$$;

CREATE OR REPLACE FUNCTION nyam_time(value TEXT, VARIADIC modifiers TEXT[])
RETURNS TEXT
LANGUAGE sql
STABLE
AS $$
    SELECT to_char(nyam_sqlite_timestamp(value, modifiers), 'HH24:MI:SS')
$$;

CREATE OR REPLACE FUNCTION nyam_time(value TEXT)
RETURNS TEXT
LANGUAGE sql
STABLE
AS $$
    SELECT to_char(nyam_sqlite_timestamp(value, ARRAY[]::TEXT[]), 'HH24:MI:SS')
$$;

CREATE OR REPLACE FUNCTION nyam_julianday(value TEXT)
RETURNS DOUBLE PRECISION
LANGUAGE sql
STABLE
AS $$
    SELECT extract(epoch FROM nyam_sqlite_timestamp(value, ARRAY[]::TEXT[])) / 86400.0 + 2440587.5
$$;

CREATE OR REPLACE FUNCTION nyam_strftime(format_value TEXT, value TEXT, VARIADIC modifiers TEXT[])
RETURNS TEXT
LANGUAGE sql
STABLE
AS $$
    SELECT to_char(
        nyam_sqlite_timestamp(value, modifiers),
        replace(
            replace(
                replace(
                    replace(
                        replace(
                            replace(format_value, '%Y', 'YYYY'),
                            '%m', 'MM'
                        ),
                        '%d', 'DD'
                    ),
                    '%H', 'HH24'
                ),
                '%M', 'MI'
            ),
            '%S', 'SS'
        )
    )
$$;

CREATE OR REPLACE FUNCTION normalize_food_title(value TEXT)
RETURNS TEXT
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT btrim(lower(regexp_replace(COALESCE(value, ''), '\\s+', ' ', 'g')), ' .,!?:;-')
$$;
"""


def translate_sqlite_sql(statement: str) -> tuple[str, bool]:
    sql = statement.strip()
    sql = re.sub(r"\bdatetime\s*\(", "nyam_datetime(", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\bjulianday\s*\(", "nyam_julianday(", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\bstrftime\s*\(", "nyam_strftime(", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\bdate\s*\(", "nyam_date(", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\btime\s*\(", "nyam_time(", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\s+COLLATE\s+NOCASE\b", "", sql, flags=re.IGNORECASE)

    sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
    sql = re.sub(r"\btelegram_id\s+INTEGER\b", "telegram_id BIGINT", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\bREAL\b", "DOUBLE PRECISION", sql, flags=re.IGNORECASE)
    sql = sql.replace(
        "DEFAULT CURRENT_TIMESTAMP",
        "DEFAULT (timezone('UTC', CURRENT_TIMESTAMP)::text)",
    )
    sql = sql.replace(
        "MIN(3000, MAX(1500, ROUND(weight * 30.0 / 50.0) * 50))",
        "LEAST(3000, GREATEST(1500, ROUND(weight * 30.0 / 50.0) * 50))",
    )
    sql = sql.replace(
        "MIN(bot.first_at, miniapp.first_at)",
        "LEAST(bot.first_at, miniapp.first_at)",
    )

    replace_insert = bool(re.match(r"INSERT\s+OR\s+REPLACE\s+INTO", sql, flags=re.IGNORECASE))
    ignore_insert = bool(re.match(r"INSERT\s+OR\s+IGNORE\s+INTO", sql, flags=re.IGNORECASE))
    sql = re.sub(r"^INSERT\s+OR\s+(?:IGNORE|REPLACE)\s+INTO", "INSERT INTO", sql, flags=re.IGNORECASE)
    # psycopg treats percent signs as placeholder syntax even in SQL literals.
    # Escape SQLite format strings before adding the real %s parameters.
    sql = sql.replace("%", "%%")
    sql = sql.replace("?", "%s")

    sql = re.sub(
        r"\bCURRENT_TIMESTAMP\b",
        "(timezone('UTC', CURRENT_TIMESTAMP)::text)",
        sql,
        flags=re.IGNORECASE,
    )
    sql = sql.replace(
        "DEFAULT (timezone('UTC', (timezone('UTC', CURRENT_TIMESTAMP)::text))::text)",
        "DEFAULT (timezone('UTC', CURRENT_TIMESTAMP)::text)",
    )

    sql = sql.rstrip("; ")
    if ignore_insert:
        sql += " ON CONFLICT DO NOTHING"
    elif replace_insert:
        sql += (
            " ON CONFLICT (user_id, segment, step) DO UPDATE SET "
            "sent_at = EXCLUDED.sent_at, status = EXCLUDED.status, error = EXCLUDED.error"
        )

    insert_match = re.match(r"INSERT\s+INTO\s+(\w+)", sql, flags=re.IGNORECASE)
    returns_id = bool(insert_match and insert_match.group(1).lower() in AUTO_ID_TABLES)
    if returns_id:
        sql += " RETURNING id"
    return sql, returns_id


class PostgresCursor:
    def __init__(self, cursor, *, lastrowid: int | None = None) -> None:
        self._cursor = cursor
        self.lastrowid = lastrowid

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()


class PostgresConnection:
    def __init__(self, connection) -> None:
        self._connection = connection

    def execute(self, statement: str, params: tuple[Any, ...] = ()) -> PostgresCursor:
        pragma_match = re.fullmatch(
            r"\s*PRAGMA\s+table_info\((\w+)\)\s*",
            statement,
            flags=re.IGNORECASE,
        )
        if pragma_match:
            sql = (
                "SELECT column_name AS name FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = %s "
                "ORDER BY ordinal_position"
            )
            cursor = self._connection.execute(sql, (pragma_match.group(1),))
            return PostgresCursor(cursor)

        sql, returns_id = translate_sqlite_sql(statement)
        cursor = self._connection.execute(sql, params)
        lastrowid = None
        if returns_id:
            inserted = cursor.fetchone()
            if inserted is not None:
                lastrowid = int(inserted["id"])
        return PostgresCursor(cursor, lastrowid=lastrowid)

    def create_function(self, name: str, argument_count: int, function) -> None:
        # Compatibility functions used by shared queries are installed in PostgreSQL during init.
        return None

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        self._connection.close()


def connect_postgres(database_url: str) -> PostgresConnection:
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError(
            "PostgreSQL requires psycopg. Install dependencies from requirements.txt."
        ) from exc

    connection = psycopg.connect(
        database_url,
        row_factory=compat_row_factory,
        connect_timeout=8,
        options="-c statement_timeout=30000 -c lock_timeout=5000",
    )
    return PostgresConnection(connection)


def initialize_postgres_compatibility(database_url: str) -> None:
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError(
            "PostgreSQL requires psycopg. Install dependencies from requirements.txt."
        ) from exc

    with psycopg.connect(
        database_url,
        connect_timeout=8,
        options="-c statement_timeout=30000 -c lock_timeout=5000",
    ) as connection:
        connection.execute(POSTGRES_COMPAT_FUNCTIONS)
