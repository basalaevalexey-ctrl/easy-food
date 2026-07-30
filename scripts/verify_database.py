from __future__ import annotations

import argparse
import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path


TABLES = (
    "users",
    "food_entries",
    "user_events",
    "user_achievements",
    "daily_missions",
    "reminder_logs",
    "broadcast_logs",
    "lifecycle_pushes",
    "referrals",
    "water_entries",
    "streak_freezes",
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Проверка SQLite-базы Нямметра перед переносом")
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    path = args.database.resolve()
    if not path.is_file():
        raise SystemExit(f"База не найдена: {path}")

    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as conn:
        integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        existing_tables = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        counts = {
            table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in TABLES
            if table in existing_tables
        }

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    print(f"path: {path}")
    print(f"size: {path.stat().st_size}")
    print(f"sha256: {digest}")
    print(f"integrity: {integrity}")
    for table, count in counts.items():
        print(f"{table}: {count}")

    if integrity != "ok":
        raise SystemExit("Проверка целостности не пройдена")
    for required in ("users", "food_entries", "user_events"):
        if required not in counts:
            raise SystemExit(f"Нет обязательной таблицы: {required}")


if __name__ == "__main__":
    main()
