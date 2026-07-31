from __future__ import annotations

import argparse
import math
import sqlite3
from pathlib import Path


TABLES = (
    "users",
    "app_meta",
    "food_entries",
    "user_events",
    "user_achievements",
    "daily_missions",
    "water_entries",
    "reminder_logs",
    "broadcast_logs",
    "daily_push_stickers",
    "lifecycle_pushes",
    "referrals",
    "streak_freezes",
)
SERIAL_TABLES = tuple(table for table in TABLES if table not in {"app_meta", "daily_push_stickers"})
BATCH_SIZE = 250


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a guarded PostgreSQL staging import from a Njammetr SQLite snapshot."
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    return parser.parse_args()


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def sql_literal(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bytes):
        return f"decode('{value.hex()}', 'hex')"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Non-finite numbers cannot be exported")
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }


def table_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")]


def write_insert_batches(
    output,
    connection: sqlite3.Connection,
    table: str,
    columns: list[str],
) -> int:
    rows = connection.execute(f"SELECT * FROM {table}")
    count = 0
    batch: list[str] = []
    column_sql = ", ".join(quote_identifier(column) for column in columns)
    for row in rows:
        batch.append("(" + ", ".join(sql_literal(value) for value in row) + ")")
        count += 1
        if len(batch) == BATCH_SIZE:
            output.write(
                f"INSERT INTO {quote_identifier(table)} ({column_sql}) VALUES\n"
                + ",\n".join(batch)
                + ";\n"
            )
            batch.clear()
    if batch:
        output.write(
            f"INSERT INTO {quote_identifier(table)} ({column_sql}) VALUES\n"
            + ",\n".join(batch)
            + ";\n"
        )
    return count


def main() -> int:
    args = parse_args()
    if not args.source.is_file():
        raise SystemExit(f"SQLite snapshot not found: {args.source}")

    source = sqlite3.connect(f"file:{args.source.resolve()}?mode=ro", uri=True)
    try:
        integrity = source.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise SystemExit(f"SQLite integrity check failed: {integrity}")
        missing = sorted(set(TABLES) - table_names(source))
        if missing:
            raise SystemExit("SQLite snapshot is missing tables: " + ", ".join(missing))

        args.output.parent.mkdir(parents=True, exist_ok=True)
        counts: dict[str, int] = {}
        with args.output.open("w", encoding="utf-8", newline="\n") as output:
            output.write(
                "DO $guard$\n"
                "BEGIN\n"
                "  IF current_database() NOT ILIKE '%staging%' THEN\n"
                "    RAISE EXCEPTION 'Refusing to import outside a staging database';\n"
                "  END IF;\n"
                "END\n"
                "$guard$;\n"
                "BEGIN;\n"
                "TRUNCATE TABLE "
                + ", ".join(quote_identifier(table) for table in reversed(TABLES))
                + " RESTART IDENTITY CASCADE;\n"
            )

            for table in TABLES:
                columns = table_columns(source, table)
                counts[table] = write_insert_batches(output, source, table, columns)

            for table in SERIAL_TABLES:
                table_literal = sql_literal(table)
                table_identifier = quote_identifier(table)
                output.write(
                    "SELECT setval(pg_get_serial_sequence("
                    f"{table_literal}, 'id'), COALESCE((SELECT MAX(id) FROM {table_identifier}), 1), "
                    f"EXISTS(SELECT 1 FROM {table_identifier}));\n"
                )

            for table, count in counts.items():
                output.write(
                    "DO $verify$ BEGIN "
                    f"IF (SELECT COUNT(*) FROM {quote_identifier(table)}) <> {count} THEN "
                    f"RAISE EXCEPTION 'Count verification failed for {table}'; "
                    "END IF; END $verify$;\n"
                )

            food_totals = source.execute(
                "SELECT COALESCE(SUM(calories), 0), COALESCE(SUM(protein), 0) FROM food_entries"
            ).fetchone()
            output.write(
                "DO $verify$ BEGIN "
                f"IF ABS((SELECT COALESCE(SUM(calories), 0) FROM food_entries) - {sql_literal(food_totals[0])}) > 0.01 "
                "THEN RAISE EXCEPTION 'Calorie aggregate verification failed'; END IF; "
                f"IF ABS((SELECT COALESCE(SUM(protein), 0) FROM food_entries) - {sql_literal(food_totals[1])}) > 0.01 "
                "THEN RAISE EXCEPTION 'Protein aggregate verification failed'; END IF; "
                "END $verify$;\n"
                "COMMIT;\n"
            )

        print(f"Created: {args.output}")
        print(f"Size: {args.output.stat().st_size}")
        for table in TABLES:
            print(f"{table}: {counts[table]}")
        return 0
    finally:
        source.close()


if __name__ == "__main__":
    raise SystemExit(main())
