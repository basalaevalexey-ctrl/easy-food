import tempfile
import unittest
from pathlib import Path

from app.database import Database
from app.database_backend import compat_row_factory, translate_sqlite_sql
from app.models import FoodEstimate


class PostgresTranslationTests(unittest.TestCase):
    def test_row_factory_accepts_commands_without_result_columns(self) -> None:
        class CursorWithoutDescription:
            description = None

        make_row = compat_row_factory(CursorWithoutDescription())
        self.assertEqual(dict(make_row(())), {})

    def test_translates_placeholders_and_date_helpers(self) -> None:
        sql, returns_id = translate_sqlite_sql(
            "SELECT date(created_at, '+3 hours') FROM food_entries WHERE user_id = ?"
        )
        self.assertIn("nyam_date(created_at, '+3 hours')", sql)
        self.assertIn("user_id = %s", sql)
        self.assertFalse(returns_id)

    def test_translates_insert_or_ignore(self) -> None:
        sql, returns_id = translate_sqlite_sql(
            "INSERT OR IGNORE INTO referrals (inviter_user_id, invited_user_id) VALUES (?, ?)"
        )
        self.assertIn("ON CONFLICT DO NOTHING", sql)
        self.assertTrue(sql.endswith("RETURNING id"))
        self.assertTrue(returns_id)

    def test_translates_lifecycle_upsert(self) -> None:
        sql, _ = translate_sqlite_sql(
            """
            INSERT OR REPLACE INTO lifecycle_pushes
                (user_id, segment, step, sent_at, status, error)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?, ?)
            """
        )
        self.assertIn("ON CONFLICT (user_id, segment, step) DO UPDATE", sql)
        self.assertIn("timezone('UTC', CURRENT_TIMESTAMP)::text", sql)

    def test_translates_schema_types(self) -> None:
        sql, _ = translate_sqlite_sql(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                weight REAL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.assertIn("id BIGSERIAL PRIMARY KEY", sql)
        self.assertIn("telegram_id BIGINT", sql)
        self.assertIn("weight DOUBLE PRECISION", sql)
        self.assertIn("DEFAULT (timezone('UTC', CURRENT_TIMESTAMP)::text)", sql)
        self.assertNotIn("timezone('UTC', (timezone", sql)


class SQLiteRegressionTests(unittest.TestCase):
    def test_core_flow_still_works_without_database_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(Path(temp_dir) / "test.sqlite3")
            database.init()
            user = database.get_or_create_user(1234567890)
            self.assertEqual(user.telegram_id, 1234567890)

            estimate = FoodEstimate(
                is_food=True,
                title="Омлет",
                description="Два яйца",
                calories=180,
                protein=14,
                fat=12,
                carbs=2,
                water_ml=80,
                confidence="medium",
                comment="",
                not_food_reason="",
            )
            entry = database.add_food_entry(user.telegram_id, estimate, source="text")
            self.assertGreater(entry.id, 0)
            self.assertEqual(len(database.get_today_entries(user.telegram_id)), 1)
            self.assertEqual(database.integrity_check(), "ok")


if __name__ == "__main__":
    unittest.main()
