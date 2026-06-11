import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from app.models import FoodEntry, FoodEstimate, User


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER NOT NULL UNIQUE,
                    sex TEXT,
                    age INTEGER,
                    height INTEGER,
                    weight REAL,
                    goal TEXT,
                    activity TEXT,
                    calorie_target INTEGER,
                    protein_target INTEGER,
                    reminder_time TEXT,
                    reminder_last_sent_date TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            self._ensure_column(conn, "users", "reminder_time", "TEXT")
            self._ensure_column(conn, "users", "reminder_last_sent_date", "TEXT")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS food_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    calories REAL NOT NULL,
                    protein REAL NOT NULL,
                    fat REAL NOT NULL,
                    carbs REAL NOT NULL,
                    confidence TEXT NOT NULL,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_food_entries_user_date ON food_entries(user_id, created_at)")

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, column_type: str) -> None:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")

    def get_or_create_user(self, telegram_id: int) -> User:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
            if row is None:
                conn.execute("INSERT INTO users (telegram_id) VALUES (?)", (telegram_id,))
                row = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
            return self._user_from_row(row)

    def update_user_goal(self, telegram_id: int, data: dict[str, Any]) -> User:
        self.get_or_create_user(telegram_id)
        fields = [
            "sex",
            "age",
            "height",
            "weight",
            "goal",
            "activity",
            "calorie_target",
            "protein_target",
        ]
        values = [data.get(field) for field in fields]
        assignments = ", ".join(f"{field} = ?" for field in fields)
        with self.connect() as conn:
            conn.execute(f"UPDATE users SET {assignments} WHERE telegram_id = ?", (*values, telegram_id))
            row = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
            return self._user_from_row(row)

    def set_reminder_time(self, telegram_id: int, reminder_time: str | None) -> User:
        self.get_or_create_user(telegram_id)
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE users
                SET reminder_time = ?,
                    reminder_last_sent_date = NULL
                WHERE telegram_id = ?
                """,
                (reminder_time, telegram_id),
            )
            row = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
            return self._user_from_row(row)

    def get_users_for_reminder(self, reminder_time: str, today: str) -> list[User]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM users
                WHERE reminder_time = ?
                  AND (reminder_last_sent_date IS NULL OR reminder_last_sent_date != ?)
                """,
                (reminder_time, today),
            ).fetchall()
            return [self._user_from_row(row) for row in rows]

    def mark_reminder_sent(self, telegram_id: int, today: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET reminder_last_sent_date = ? WHERE telegram_id = ?",
                (today, telegram_id),
            )

    def add_food_entry(self, telegram_id: int, estimate: FoodEstimate, source: str) -> FoodEntry:
        user = self.get_or_create_user(telegram_id)
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO food_entries
                    (user_id, title, description, calories, protein, fat, carbs, confidence, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user.id,
                    estimate.title,
                    estimate.description,
                    estimate.calories,
                    estimate.protein,
                    estimate.fat,
                    estimate.carbs,
                    estimate.confidence,
                    source,
                ),
            )
            row = conn.execute("SELECT * FROM food_entries WHERE id = ?", (cursor.lastrowid,)).fetchone()
            return self._entry_from_row(row)

    def get_food_entry(self, entry_id: int, telegram_id: int) -> FoodEntry | None:
        user = self.get_or_create_user(telegram_id)
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM food_entries WHERE id = ? AND user_id = ?",
                (entry_id, user.id),
            ).fetchone()
            return self._entry_from_row(row) if row else None

    def scale_food_entry(self, entry_id: int, telegram_id: int, factor: float) -> FoodEntry | None:
        entry = self.get_food_entry(entry_id, telegram_id)
        if entry is None:
            return None
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE food_entries
                SET calories = calories * ?,
                    protein = protein * ?,
                    fat = fat * ?,
                    carbs = carbs * ?
                WHERE id = ?
                """,
                (factor, factor, factor, factor, entry_id),
            )
        return self.get_food_entry(entry_id, telegram_id)

    def replace_food_entry_estimate(self, entry_id: int, telegram_id: int, estimate: FoodEstimate) -> FoodEntry | None:
        entry = self.get_food_entry(entry_id, telegram_id)
        if entry is None:
            return None
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE food_entries
                SET title = ?, description = ?, calories = ?, protein = ?, fat = ?, carbs = ?, confidence = ?
                WHERE id = ?
                """,
                (
                    estimate.title,
                    estimate.description,
                    estimate.calories,
                    estimate.protein,
                    estimate.fat,
                    estimate.carbs,
                    estimate.confidence,
                    entry_id,
                ),
            )
        return self.get_food_entry(entry_id, telegram_id)

    def delete_food_entry(self, entry_id: int, telegram_id: int) -> bool:
        entry = self.get_food_entry(entry_id, telegram_id)
        if entry is None:
            return False
        with self.connect() as conn:
            conn.execute("DELETE FROM food_entries WHERE id = ?", (entry_id,))
        return True

    def get_today_entries(self, telegram_id: int) -> list[FoodEntry]:
        user = self.get_or_create_user(telegram_id)
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM food_entries
                WHERE user_id = ? AND date(created_at, 'localtime') = date('now', 'localtime')
                ORDER BY created_at ASC
                """,
                (user.id,),
            ).fetchall()
            return [self._entry_from_row(row) for row in rows]

    def get_daily_history(self, telegram_id: int, days: int = 7) -> list[sqlite3.Row]:
        user = self.get_or_create_user(telegram_id)
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT date(created_at, 'localtime') AS day,
                       SUM(calories) AS calories,
                       SUM(protein) AS protein
                FROM food_entries
                WHERE user_id = ? AND date(created_at, 'localtime') >= date('now', 'localtime', ?)
                GROUP BY day
                ORDER BY day DESC
                """,
                (user.id, f"-{days - 1} days"),
            ).fetchall()

    def stats(self) -> dict[str, int]:
        with self.connect() as conn:
            users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            today = conn.execute(
                "SELECT COUNT(*) FROM food_entries WHERE date(created_at, 'localtime') = date('now', 'localtime')"
            ).fetchone()[0]
            total = conn.execute("SELECT COUNT(*) FROM food_entries").fetchone()[0]
            return {"users": users, "today_entries": today, "total_entries": total}

    @staticmethod
    def _user_from_row(row: sqlite3.Row) -> User:
        return User(
            id=row["id"],
            telegram_id=row["telegram_id"],
            sex=row["sex"],
            age=row["age"],
            height=row["height"],
            weight=row["weight"],
            goal=row["goal"],
            activity=row["activity"],
            calorie_target=row["calorie_target"],
            protein_target=row["protein_target"],
            reminder_time=row["reminder_time"],
            reminder_last_sent_date=row["reminder_last_sent_date"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @staticmethod
    def _entry_from_row(row: sqlite3.Row) -> FoodEntry:
        return FoodEntry(
            id=row["id"],
            user_id=row["user_id"],
            title=row["title"],
            description=row["description"],
            calories=row["calories"],
            protein=row["protein"],
            fat=row["fat"],
            carbs=row["carbs"],
            confidence=row["confidence"],
            source=row["source"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )
