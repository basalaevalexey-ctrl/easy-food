from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

from app.database import Database


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Copy a Njammetr SQLite snapshot into an empty staging PostgreSQL database."
    )
    parser.add_argument("source", type=Path, help="Path to the SQLite snapshot")
    parser.add_argument("--database-url", default="", help="Defaults to DATABASE_URL")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Perform the import. Without it the script only validates both databases.",
    )
    return parser.parse_args()


def source_tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }


def table_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()]


def table_counts_sqlite(connection: sqlite3.Connection) -> dict[str, int]:
    present = source_tables(connection)
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in TABLES
        if table in present
    }


def table_counts_postgres(connection) -> dict[str, int]:
    result: dict[str, int] = {}
    for table in TABLES:
        result[table] = int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    return result


def print_counts(title: str, counts: dict[str, int]) -> None:
    print(title)
    for table in TABLES:
        if table in counts:
            print(f"  {table}: {counts[table]}")


def main() -> int:
    args = parse_args()
    load_dotenv()
    database_url = (args.database_url or os.getenv("DATABASE_URL", "")).strip()
    if not database_url:
        raise SystemExit("DATABASE_URL is required")
    if not args.source.is_file():
        raise SystemExit(f"SQLite snapshot not found: {args.source}")

    parsed = urlsplit(database_url)
    database_name = parsed.path.lstrip("/")
    if "staging" not in database_name.lower():
        raise SystemExit(
            f"Refusing to import into non-staging database {database_name!r}. "
            "Production cutover must use a separately reviewed procedure."
        )

    try:
        import psycopg
        from psycopg import sql
    except ImportError as exc:
        raise SystemExit("Install dependencies from requirements.txt first") from exc

    source = sqlite3.connect(f"file:{args.source.resolve()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    try:
        integrity = source.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise SystemExit(f"SQLite integrity check failed: {integrity}")
        source_counts = table_counts_sqlite(source)
        missing = [table for table in TABLES if table not in source_counts]
        if missing:
            raise SystemExit("SQLite snapshot is missing tables: " + ", ".join(missing))

        Database(Path("unused.sqlite3"), database_url=database_url).init()
        with psycopg.connect(database_url) as target:
            target_counts = table_counts_postgres(target)
            print_counts("SQLite source:", source_counts)
            print_counts("PostgreSQL target before import:", target_counts)
            if not args.apply:
                print("Dry run complete. Add --apply to import into the staging database.")
                return 0

            nonempty = {
                table: count
                for table, count in target_counts.items()
                if count and table != "app_meta"
            }
            if nonempty:
                raise SystemExit(
                    "Target is not empty; import aborted: "
                    + ", ".join(f"{table}={count}" for table, count in nonempty.items())
                )

            truncate_tables = sql.SQL(", ").join(sql.Identifier(table) for table in reversed(TABLES))
            target.execute(
                sql.SQL("TRUNCATE TABLE {} RESTART IDENTITY CASCADE").format(truncate_tables)
            )

            for table in TABLES:
                columns = table_columns(source, table)
                rows = source.execute(f"SELECT * FROM {table}").fetchall()
                if not rows:
                    continue
                insert = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                    sql.Identifier(table),
                    sql.SQL(", ").join(sql.Identifier(column) for column in columns),
                    sql.SQL(", ").join(sql.Placeholder() for _ in columns),
                )
                target.executemany(insert, [tuple(row[column] for column in columns) for row in rows])

            for table in SERIAL_TABLES:
                target.execute(
                    "SELECT setval(pg_get_serial_sequence(%s, 'id'), "
                    "COALESCE((SELECT MAX(id) FROM " + table + "), 1), "
                    "EXISTS(SELECT 1 FROM " + table + "))",
                    (table,),
                )

            imported_counts = table_counts_postgres(target)
            if imported_counts != source_counts:
                differences = [
                    f"{table}: sqlite={source_counts.get(table)} postgres={imported_counts.get(table)}"
                    for table in TABLES
                    if source_counts.get(table) != imported_counts.get(table)
                ]
                raise RuntimeError("Count verification failed: " + "; ".join(differences))

            source_totals = source.execute(
                "SELECT COUNT(*), COALESCE(SUM(calories), 0), COALESCE(SUM(protein), 0) "
                "FROM food_entries"
            ).fetchone()
            target_totals = target.execute(
                "SELECT COUNT(*), COALESCE(SUM(calories), 0), COALESCE(SUM(protein), 0) "
                "FROM food_entries"
            ).fetchone()
            for index, label in enumerate(("entries", "calories", "protein")):
                if abs(float(source_totals[index]) - float(target_totals[index])) > 0.01:
                    raise RuntimeError(f"Aggregate verification failed for {label}")

        print_counts("PostgreSQL target after import:", imported_counts)
        print("Import and verification completed successfully.")
        return 0
    finally:
        source.close()


if __name__ == "__main__":
    sys.exit(main())
