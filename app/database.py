import hashlib
import sqlite3
import shutil
import random
import json
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit

from app.achievements import Achievement, available_achievements, daily_food_signals
from app.calorie_calculator import calculate_water_target
from app.competitions import (
    GROUP_CAPACITY,
    LEAGUE_TIER_BRONZE,
    LEAGUE_PROMOTION_PLACES,
    LEAGUE_TIERS,
    calculate_competition_daily_score,
    calculate_competition_task_scores,
    competition_tasks_for_day,
    promote_league_tier,
)
from app.database_backend import connect_postgres, initialize_postgres_compatibility
from app.missions import (
    MISSION_ORDER,
    MISSIONS,
    SIMPLE_MISSION_KEYS,
    DailyMission,
    mission_is_completed,
    mission_progress_text,
)
from app.models import FoodEntry, FoodEstimate, User

SERVICE_EVENT_TYPES = (
    "broadcast_received",
    "reminder_sent",
    "water_sent",
    "duolingo_push_sent",
    "lifecycle:one_food_no_return_sent",
    "lifecycle:goal_no_food_sent",
    "lifecycle:started_no_goal_sent",
    "broadcast_sent",
    "broadcast_segment_sent",
    "database_backup_requested",
)


def _normalize_food_title(value: str | None) -> str:
    if not value:
        return ""
    normalized = " ".join(value.strip().split()).casefold().replace("ё", "е")
    return normalized.strip(" .,!?:;-")


class Database:
    def __init__(
        self,
        path: Path,
        legacy_paths: tuple[Path, ...] = (),
        backup_paths: tuple[Path, ...] = (),
        database_url: str = "",
    ) -> None:
        self.path = path
        self.legacy_paths = legacy_paths
        self.backup_paths = backup_paths
        self.database_url = database_url.strip()

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith(("postgresql://", "postgres://"))

    @contextmanager
    def connect(self) -> Iterator[Any]:
        if self.is_postgres:
            conn = connect_postgres(self.database_url)
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
            return

        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()
            self._backup_database()

    def init(self) -> None:
        if self.is_postgres:
            initialize_postgres_compatibility(self.database_url)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._restore_best_database()
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
                    water_target INTEGER,
                    goal_set_at TEXT,
                    reminder_time TEXT,
                    reminder_last_sent_date TEXT,
                    water_reminders_enabled INTEGER NOT NULL DEFAULT 0,
                    water_reminder_skip_date TEXT,
                    current_streak INTEGER NOT NULL DEFAULT 0,
                    best_streak INTEGER NOT NULL DEFAULT 0,
                    last_active_date TEXT,
                    activation_step INTEGER NOT NULL DEFAULT 0,
                    last_activation_message_at TEXT,
                    activation_disabled INTEGER NOT NULL DEFAULT 0,
                    display_name TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            self._ensure_column(conn, "users", "goal_set_at", "TEXT")
            self._ensure_column(conn, "users", "water_target", "INTEGER")
            self._ensure_column(conn, "users", "reminder_time", "TEXT")
            self._ensure_column(conn, "users", "reminder_last_sent_date", "TEXT")
            self._ensure_column(conn, "users", "water_reminders_enabled", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "users", "water_reminder_skip_date", "TEXT")
            self._ensure_column(conn, "users", "current_streak", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "users", "best_streak", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "users", "last_active_date", "TEXT")
            self._ensure_column(conn, "users", "activation_step", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "users", "last_activation_message_at", "TEXT")
            self._ensure_column(conn, "users", "activation_disabled", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "users", "display_name", "TEXT")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS app_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            self._backfill_default_reminders(conn)
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
                    water_ml REAL NOT NULL DEFAULT 0,
                    confidence TEXT NOT NULL,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS external_identities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    provider TEXT NOT NULL,
                    provider_user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(provider, provider_user_id),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS email_credentials (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL UNIQUE,
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS password_reset_tokens (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    expires_at TEXT NOT NULL,
                    used_at TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_achievements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    achievement_key TEXT NOT NULL,
                    unlocked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, achievement_key),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_missions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    mission_key TEXT NOT NULL,
                    mission_date TEXT NOT NULL,
                    is_completed INTEGER NOT NULL DEFAULT 0,
                    completed_at TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, mission_date),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
                """
            )
            self._ensure_column(conn, "food_entries", "water_ml", "REAL NOT NULL DEFAULT 0")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS water_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    amount_ml INTEGER NOT NULL CHECK(amount_ml > 0),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_water_entries_user_created ON water_entries(user_id, created_at)"
            )
            conn.execute(
                """
                UPDATE users
                SET water_target = MIN(3000, MAX(1500, ROUND(weight * 30.0 / 50.0) * 50))
                WHERE water_target IS NULL AND weight IS NOT NULL
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reminder_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    sent_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    reminder_type TEXT NOT NULL,
                    slot TEXT,
                    status TEXT NOT NULL,
                    error TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS broadcast_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    campaign_id TEXT NOT NULL,
                    sent_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    status TEXT NOT NULL,
                    error TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_push_stickers (
                    user_id INTEGER NOT NULL,
                    push_date TEXT NOT NULL,
                    claimed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, push_date),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS lifecycle_pushes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    segment TEXT NOT NULL,
                    step INTEGER NOT NULL,
                    sent_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    status TEXT NOT NULL,
                    error TEXT,
                    UNIQUE(user_id, segment, step),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS referrals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    inviter_user_id INTEGER NOT NULL,
                    invited_user_id INTEGER NOT NULL UNIQUE,
                    activated_at TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CHECK(inviter_user_id != invited_user_id),
                    FOREIGN KEY (inviter_user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (invited_user_id) REFERENCES users(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS streak_freezes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    freeze_date TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, freeze_date),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS competitions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    league_tier TEXT NOT NULL DEFAULT 'bronze',
                    goal_type TEXT NOT NULL,
                    group_number INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(start_date, goal_type, league_tier, group_number)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS competition_participants (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    competition_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    score INTEGER NOT NULL DEFAULT 0,
                    final_rank INTEGER,
                    joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(competition_id, user_id),
                    FOREIGN KEY (competition_id) REFERENCES competitions(id) ON DELETE CASCADE,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS competition_daily_scores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    competition_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    score_date TEXT NOT NULL,
                    food_logged_score INTEGER NOT NULL DEFAULT 0,
                    calorie_target_score INTEGER NOT NULL DEFAULT 0,
                    water_score INTEGER NOT NULL DEFAULT 0,
                    perfect_day_score INTEGER NOT NULL DEFAULT 0,
                    task_scores_json TEXT NOT NULL DEFAULT '{}',
                    streak_score INTEGER NOT NULL DEFAULT 0,
                    total_score INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(competition_id, user_id, score_date),
                    FOREIGN KEY (competition_id) REFERENCES competitions(id) ON DELETE CASCADE,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
                """
            )
            self._ensure_column(conn, "competition_daily_scores", "task_scores_json", "TEXT NOT NULL DEFAULT '{}'")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_user_events_type_date ON user_events(event_type, created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_food_entries_user_date ON food_entries(user_id, created_at)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_achievements_user ON user_achievements(user_id, unlocked_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_external_identities_user ON external_identities(user_id)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_daily_missions_user_date ON daily_missions(user_id, mission_date)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_reminder_logs_sent_at ON reminder_logs(sent_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_reminder_logs_user_sent ON reminder_logs(user_id, sent_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_broadcast_logs_sent_at ON broadcast_logs(sent_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_broadcast_logs_user_sent ON broadcast_logs(user_id, sent_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_broadcast_logs_campaign ON broadcast_logs(campaign_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_lifecycle_pushes_user_segment ON lifecycle_pushes(user_id, segment, sent_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_referrals_inviter ON referrals(inviter_user_id, activated_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_streak_freezes_user_date ON streak_freezes(user_id, freeze_date)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_competitions_active ON competitions(status, start_date, end_date, goal_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_competition_participants_user ON competition_participants(user_id, competition_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_competition_participants_rank ON competition_participants(competition_id, score DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_competition_daily_scores_user_date ON competition_daily_scores(user_id, score_date)")
            self._rebuild_user_streaks(conn)

    def has_valid_database_candidate(self) -> bool:
        if self.is_postgres:
            try:
                with self.connect() as conn:
                    row = conn.execute(
                        """
                        SELECT COUNT(*) AS table_count
                        FROM information_schema.tables
                        WHERE table_schema = current_schema()
                          AND table_name IN ('users', 'food_entries', 'user_events')
                        """
                    ).fetchone()
                    return int(row["table_count"] or 0) == 3
            except Exception:
                return False
        candidates = self._unique_paths((self.path, *self.legacy_paths, *self.backup_paths))
        return any(
            candidate.exists() and self._database_score(candidate) != (-1, -1)
            for candidate in candidates
        )

    def integrity_check(self) -> str:
        if self.is_postgres:
            try:
                with self.connect() as conn:
                    row = conn.execute("SELECT 1 AS healthy").fetchone()
                    return "ok" if row and int(row["healthy"]) == 1 else "no_result"
            except Exception as exc:
                return f"error:{type(exc).__name__}"
        if not self.path.exists():
            return "missing"
        try:
            conn = sqlite3.connect(self.path)
            try:
                row = conn.execute("PRAGMA integrity_check").fetchone()
                return str(row[0]) if row else "no_result"
            finally:
                conn.close()
        except (OSError, sqlite3.Error) as exc:
            return f"error:{type(exc).__name__}"

    def _restore_best_database(self) -> None:
        candidates = self._unique_paths((self.path, *self.legacy_paths, *self.backup_paths))
        best_path = None
        best_score = (-1, -1)
        for candidate in candidates:
            if not candidate.exists():
                continue
            score = self._database_score(candidate)
            if score > best_score:
                best_path = candidate
                best_score = score

        current_score = self._database_score(self.path) if self.path.exists() else (-1, -1)
        if best_path and best_path != self.path and best_score > current_score:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(best_path, self.path)

    def _backup_database(self) -> None:
        if self.is_postgres:
            return
        if not self.path.exists():
            return
        for backup_path in self._unique_paths(self.backup_paths):
            if backup_path == self.path:
                continue
            try:
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(self.path, backup_path)
            except OSError:
                continue

    @staticmethod
    def _database_score(path: Path) -> tuple[int, int]:
        try:
            conn = sqlite3.connect(path)
            try:
                tables = {
                    row[0]
                    for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
                }
                total = 0
                for table in (
                    "users",
                    "food_entries",
                    "user_events",
                    "user_achievements",
                    "daily_missions",
                    "lifecycle_pushes",
                    "water_entries",
                    "streak_freezes",
                ):
                    if table in tables:
                        total += int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                size = path.stat().st_size
                return total, size
            finally:
                conn.close()
        except (OSError, sqlite3.Error):
            return -1, -1

    @staticmethod
    def _unique_paths(paths: tuple[Path, ...]) -> tuple[Path, ...]:
        result: list[Path] = []
        seen: set[str] = set()
        for path in paths:
            key = str(path)
            if key not in seen:
                seen.add(key)
                result.append(path)
        return tuple(result)

    @staticmethod
    def _backfill_default_reminders(conn: Any) -> None:
        migration_key = "backfill_existing_users_reminder_09_00"
        already_done = conn.execute(
            "SELECT 1 FROM app_meta WHERE key = ?",
            (migration_key,),
        ).fetchone()
        if already_done:
            return
        conn.execute(
            """
            UPDATE users
            SET reminder_time = '09:00'
            WHERE reminder_time IS NULL
            """
        )
        conn.execute(
            "INSERT INTO app_meta (key, value) VALUES (?, ?)",
            (migration_key, datetime.now().isoformat(timespec="seconds")),
        )

    @staticmethod
    def _ensure_column(conn: Any, table: str, column: str, column_type: str) -> None:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")

    def _rebuild_user_streaks(self, conn: Any) -> None:
        user_ids = [row["id"] for row in conn.execute("SELECT id FROM users").fetchall()]
        for user_id in user_ids:
            current_streak, best_streak, last_active_date = self._streaks_for_user(conn, user_id)
            conn.execute(
                """
                UPDATE users
                SET current_streak = ?,
                    best_streak = ?,
                    last_active_date = ?
                WHERE id = ?
                """,
                (current_streak, best_streak, last_active_date, user_id),
            )

    def _streaks_for_user(self, conn: Any, user_id: int) -> tuple[int, int, str | None]:
        rows = conn.execute(
            """
            SELECT day, MAX(is_active) AS is_active
            FROM (
                SELECT date(created_at, '+3 hours') AS day, 1 AS is_active
                FROM food_entries
                WHERE user_id = ?
                UNION ALL
                SELECT freeze_date AS day, 0 AS is_active
                FROM streak_freezes
                WHERE user_id = ?
            ) streak_days
            GROUP BY day
            ORDER BY day ASC
            """,
            (user_id, user_id),
        ).fetchall()
        streak_days = [
            (datetime.fromisoformat(row["day"]).date(), bool(row["is_active"]))
            for row in rows
        ]
        current_streak, best_streak, last_active_date = self._streaks_from_calendar_days(streak_days)
        if last_active_date:
            today = conn.execute("SELECT date('now', '+3 hours')").fetchone()[0]
            yesterday = conn.execute("SELECT date('now', '+3 hours', '-1 day')").fetchone()[0]
            if last_active_date not in (today, yesterday):
                current_streak = 0
        return current_streak, best_streak, last_active_date

    @staticmethod
    def _streaks_from_calendar_days(streak_days: list[tuple[Any, bool]]) -> tuple[int, int, str | None]:
        if not streak_days:
            return 0, 0, None

        best_streak = 0
        running_streak = 0
        previous_day = None
        current_streak = 0
        last_streak_date = None
        for streak_day, is_active in streak_days:
            is_consecutive = previous_day is not None and streak_day == previous_day + timedelta(days=1)
            if not is_consecutive:
                running_streak = 0
            if is_active:
                running_streak += 1
                best_streak = max(best_streak, running_streak)
            if running_streak > 0:
                current_streak = running_streak
                last_streak_date = streak_day
            previous_day = streak_day

        return current_streak, best_streak, last_streak_date.isoformat() if last_streak_date else None

    def get_or_create_user(self, telegram_id: int) -> User:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
            if row is None:
                conn.execute("INSERT INTO users (telegram_id) VALUES (?)", (telegram_id,))
                row = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
            return self._user_from_row(row)

    def get_or_create_external_user(self, provider: str, provider_user_id: str) -> User:
        normalized_provider = provider.strip().lower()
        normalized_id = provider_user_id.strip()
        if not normalized_provider or not normalized_id:
            raise ValueError("invalid_external_identity")

        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT u.*
                FROM external_identities ei
                JOIN users u ON u.id = ei.user_id
                WHERE ei.provider = ? AND ei.provider_user_id = ?
                """,
                (normalized_provider, normalized_id),
            ).fetchone()
            if row is not None:
                return self._user_from_row(row)

            digest = hashlib.sha256(
                f"{normalized_provider}:{normalized_id}".encode("utf-8")
            ).digest()
            external_login_id = 8_000_000_000_000_000_000 + (
                int.from_bytes(digest[:8], "big") % 900_000_000_000_000_000
            )
            while conn.execute(
                "SELECT 1 FROM users WHERE telegram_id = ?", (external_login_id,)
            ).fetchone():
                external_login_id += 1

            cursor = conn.execute(
                "INSERT INTO users (telegram_id) VALUES (?)", (external_login_id,)
            )
            user_id = int(cursor.lastrowid)
            conn.execute(
                """
                INSERT INTO external_identities (user_id, provider, provider_user_id)
                VALUES (?, ?, ?)
                """,
                (user_id, normalized_provider, normalized_id),
            )
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            return self._user_from_row(row)

    def create_email_user(self, email: str, password_hash: str) -> User | None:
        with self.connect() as conn:
            if conn.execute(
                "SELECT 1 FROM email_credentials WHERE email = ?", (email,)
            ).fetchone():
                return None

        user = self.get_or_create_external_user("email", email)
        with self.connect() as conn:
            if conn.execute(
                "SELECT 1 FROM email_credentials WHERE email = ?", (email,)
            ).fetchone():
                return None
            conn.execute(
                """
                INSERT INTO email_credentials (user_id, email, password_hash)
                VALUES (?, ?, ?)
                """,
                (user.id, email, password_hash),
            )
        return user

    def get_email_login(self, email: str) -> tuple[User, str] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT u.*, credentials.password_hash AS credential_password_hash
                FROM email_credentials credentials
                JOIN users u ON u.id = credentials.user_id
                WHERE credentials.email = ?
                """,
                (email,),
            ).fetchone()
            if row is None:
                return None
            return self._user_from_row(row), str(row["credential_password_hash"])

    def create_password_reset_token(
        self,
        user_id: int,
        token_hash: str,
        expires_at: datetime,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE password_reset_tokens
                SET used_at = ?
                WHERE user_id = ? AND used_at IS NULL
                """,
                (now, user_id),
            )
            conn.execute(
                """
                INSERT INTO password_reset_tokens (user_id, token_hash, expires_at)
                VALUES (?, ?, ?)
                """,
                (user_id, token_hash, expires_at.astimezone(timezone.utc).isoformat()),
            )

    def reset_password_with_token(self, token_hash: str, password_hash: str) -> User | None:
        now = datetime.now(timezone.utc)
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT tokens.id AS reset_token_id, tokens.expires_at, u.*
                FROM password_reset_tokens tokens
                JOIN users u ON u.id = tokens.user_id
                WHERE tokens.token_hash = ? AND tokens.used_at IS NULL
                """,
                (token_hash,),
            ).fetchone()
            if row is None:
                return None
            try:
                expires_at = datetime.fromisoformat(str(row["expires_at"]))
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
            except ValueError:
                return None
            if expires_at <= now:
                return None
            cursor = conn.execute(
                """
                UPDATE password_reset_tokens
                SET used_at = ?
                WHERE id = ? AND used_at IS NULL
                """,
                (now.isoformat(), int(row["reset_token_id"])),
            )
            if cursor.rowcount != 1:
                return None
            conn.execute(
                """
                UPDATE email_credentials
                SET password_hash = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (password_hash, now.isoformat(), int(row["id"])),
            )
            return self._user_from_row(row)

    def update_user_goal(self, telegram_id: int, data: dict[str, Any]) -> User:
        user = self.get_or_create_user(telegram_id)
        fields = [
            "sex",
            "age",
            "height",
            "weight",
            "goal",
            "activity",
            "calorie_target",
            "protein_target",
            "water_target",
            "goal_set_at",
        ]
        if data.get("weight") is not None:
            data["water_target"] = calculate_water_target(float(data["weight"]))
        data["goal_set_at"] = datetime.now().isoformat(timespec="seconds")
        values = [data.get(field) for field in fields]
        assignments = ", ".join(f"{field} = ?" for field in fields)
        with self.connect() as conn:
            conn.execute(f"UPDATE users SET {assignments} WHERE telegram_id = ?", (*values, telegram_id))
            conn.execute(
                "INSERT INTO user_events (user_id, event_type) VALUES (?, ?)",
                (user.id, "goal_set"),
            )
            row = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
            updated_user = self._user_from_row(row)
        self.recalculate_active_competition(telegram_id)
        return updated_user

    def set_user_display_name(self, telegram_id: int, display_name: str | None) -> None:
        normalized = " ".join(str(display_name or "").split())[:80]
        if not normalized:
            return
        user = self.get_or_create_user(telegram_id)
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET display_name = ? WHERE id = ?",
                (normalized, user.id),
            )

    @staticmethod
    def _competition_goal_type(goal: str | None) -> str | None:
        normalized = {"support": "maintain"}.get(str(goal or "").strip().lower(), str(goal or "").strip().lower())
        return normalized if normalized in {"lose", "maintain", "gain"} else None

    @staticmethod
    def _competition_today() -> date:
        return datetime.now(timezone(timedelta(hours=3))).date()

    @staticmethod
    def _competition_day_from_timestamp(value: str | datetime) -> date:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone(timedelta(hours=3))).date()

    def _league_tier_for_user(self, conn: Any, user_id: int, week_start: date) -> str:
        latest = conn.execute(
            """
            SELECT competitions.league_tier, competition_participants.final_rank
            FROM competition_participants
            JOIN competitions ON competitions.id = competition_participants.competition_id
            WHERE competition_participants.user_id = ?
              AND competitions.status = 'completed'
              AND competitions.end_date <= ?
            ORDER BY competitions.end_date DESC, competitions.id DESC
            LIMIT 1
            """,
            (user_id, week_start.isoformat()),
        ).fetchone()
        if latest is None:
            return LEAGUE_TIER_BRONZE

        tier = str(latest["league_tier"] or LEAGUE_TIER_BRONZE)
        if tier not in LEAGUE_TIERS:
            tier = LEAGUE_TIER_BRONZE
        rank = int(latest["final_rank"] or 0)
        return promote_league_tier(tier) if 0 < rank <= LEAGUE_PROMOTION_PLACES else tier

    def _finalize_expired_competitions(self, conn: Any, today: date) -> None:
        rows = conn.execute(
            "SELECT id FROM competitions WHERE status = 'active' AND end_date <= ?",
            (today.isoformat(),),
        ).fetchall()
        for row in rows:
            competition_id = int(row["id"])
            participants = conn.execute(
                """
                SELECT competition_participants.id
                FROM competition_participants
                JOIN competitions ON competitions.id = competition_participants.competition_id
                WHERE competition_participants.competition_id = ?
                ORDER BY competition_participants.score DESC,
                         (
                           SELECT COUNT(*) FROM food_entries
                           WHERE food_entries.user_id = competition_participants.user_id
                             AND date(food_entries.created_at, '+3 hours') >= competitions.start_date
                             AND date(food_entries.created_at, '+3 hours') < competitions.end_date
                         ) DESC,
                         competition_participants.joined_at ASC,
                         competition_participants.id ASC
                """,
                (competition_id,),
            ).fetchall()
            for position, participant in enumerate(participants, start=1):
                conn.execute(
                    "UPDATE competition_participants SET final_rank = ? WHERE id = ?",
                    (position, participant["id"]),
                )
            conn.execute(
                "UPDATE competitions SET status = 'completed' WHERE id = ?",
                (competition_id,),
            )

    def finalize_expired_competitions(self) -> None:
        with self.connect() as conn:
            self._finalize_expired_competitions(conn, self._competition_today())

    def _get_or_join_weekly_competition(self, conn: Any, user: User, today: date) -> Any | None:
        goal_type = self._competition_goal_type(user.goal)
        if not goal_type or not user.calorie_target:
            return None

        self._finalize_expired_competitions(conn, today)
        existing = conn.execute(
            """
            SELECT competitions.*, competition_participants.joined_at
            FROM competition_participants
            JOIN competitions ON competitions.id = competition_participants.competition_id
            WHERE competition_participants.user_id = ?
              AND competitions.status = 'active'
              AND competitions.start_date <= ?
              AND competitions.end_date > ?
            ORDER BY competitions.start_date DESC
            LIMIT 1
            """,
            (user.id, today.isoformat(), today.isoformat()),
        ).fetchone()
        if existing is not None:
            return existing

        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=7)
        league_tier = self._league_tier_for_user(conn, user.id, start)
        groups = conn.execute(
            """
            SELECT competitions.*, COUNT(competition_participants.id) AS participant_count
            FROM competitions
            LEFT JOIN competition_participants
              ON competition_participants.competition_id = competitions.id
            WHERE competitions.status = 'active'
              AND competitions.start_date = ?
              AND competitions.end_date = ?
              AND competitions.goal_type = ?
              AND competitions.league_tier = ?
            GROUP BY competitions.id
            HAVING COUNT(competition_participants.id) < ?
            ORDER BY competitions.group_number ASC
            """,
            (start.isoformat(), end.isoformat(), goal_type, league_tier, GROUP_CAPACITY),
        ).fetchall()
        if groups:
            competition_id = int(groups[0]["id"])
        else:
            next_group = conn.execute(
                """
                SELECT COALESCE(MAX(group_number), 0) + 1
                FROM competitions
                WHERE start_date = ? AND goal_type = ? AND league_tier = ?
                """,
                (start.isoformat(), goal_type, league_tier),
            ).fetchone()[0]
            cursor = conn.execute(
                """
                INSERT INTO competitions (start_date, end_date, league_tier, goal_type, group_number)
                VALUES (?, ?, ?, ?, ?)
                """,
                (start.isoformat(), end.isoformat(), league_tier, goal_type, int(next_group)),
            )
            competition_id = int(cursor.lastrowid)

        conn.execute(
            "INSERT OR IGNORE INTO competition_participants (competition_id, user_id) VALUES (?, ?)",
            (competition_id, user.id),
        )
        return conn.execute(
            """
            SELECT competitions.*, competition_participants.joined_at
            FROM competitions
            JOIN competition_participants ON competition_participants.competition_id = competitions.id
            WHERE competitions.id = ? AND competition_participants.user_id = ?
            """,
            (competition_id, user.id),
        ).fetchone()

    def _competition_food_streak_before(self, conn: Any, user_id: int, score_day: date, start_day: date) -> int:
        streak = 0
        cursor_day = score_day - timedelta(days=1)
        while cursor_day >= start_day:
            row = conn.execute(
                """
                SELECT COUNT(*) AS entries FROM food_entries
                WHERE user_id = ? AND date(created_at, '+3 hours') = ?
                """,
                (user_id, cursor_day.isoformat()),
            ).fetchone()
            if int(row["entries"] or 0) == 0:
                break
            streak += 1
            cursor_day -= timedelta(days=1)
        return streak

    def _recalculate_competition_day(self, conn: Any, user: User, competition: Any, score_day: date) -> None:
        start_day = date.fromisoformat(str(competition["start_date"]))
        end_day = date.fromisoformat(str(competition["end_date"]))
        joined_day = self._competition_day_from_timestamp(competition["joined_at"])
        if score_day < start_day or score_day >= end_day or score_day < joined_day:
            return

        food = conn.execute(
            """
            SELECT COUNT(*) AS entries, COALESCE(SUM(calories), 0) AS calories,
                   COALESCE(SUM(protein), 0) AS protein,
                   COALESCE(SUM(water_ml), 0) AS food_water,
                   COALESCE(SUM(CASE WHEN source = 'photo' THEN 1 ELSE 0 END), 0) AS photo_entries,
                   MIN(time(created_at, '+3 hours')) AS first_entry_time
            FROM food_entries
            WHERE user_id = ? AND date(created_at, '+3 hours') = ?
            """,
            (user.id, score_day.isoformat()),
        ).fetchone()
        manual_water = conn.execute(
            """
            SELECT COALESCE(SUM(amount_ml), 0) AS manual_water
            FROM water_entries
            WHERE user_id = ? AND date(created_at, '+3 hours') = ?
            """,
            (user.id, score_day.isoformat()),
        ).fetchone()
        task_set = competition_tasks_for_day(int(competition["id"]), score_day)
        task_scores = calculate_competition_task_scores(
            task_set,
            food_entries=int(food["entries"] or 0),
            calories=float(food["calories"] or 0),
            calorie_target=int(user.calorie_target) if user.calorie_target else None,
            water_ml=float(food["food_water"] or 0) + float(manual_water["manual_water"] or 0),
            water_target=int(user.water_target) if user.water_target else None,
            protein=float(food["protein"] or 0),
            protein_target=int(user.protein_target) if user.protein_target else None,
            photo_entries=int(food["photo_entries"] or 0),
            first_entry_before_noon=str(food["first_entry_time"] or "") < "12:00:00",
        )
        legacy_result = calculate_competition_daily_score(
            food_entries=int(food["entries"] or 0), calories=float(food["calories"] or 0),
            calorie_target=int(user.calorie_target) if user.calorie_target else None,
            water_ml=float(food["food_water"] or 0) + float(manual_water["manual_water"] or 0),
            water_target=int(user.water_target) if user.water_target else None,
            food_streak_days_before_today=self._competition_food_streak_before(
                conn, user.id, score_day, max(start_day, joined_day)
            ),
        )
        conn.execute(
            """
            INSERT INTO competition_daily_scores
                (competition_id, user_id, score_date, food_logged_score, calorie_target_score,
                 water_score, perfect_day_score, task_scores_json, streak_score, total_score, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(competition_id, user_id, score_date) DO UPDATE SET
                food_logged_score = excluded.food_logged_score,
                calorie_target_score = excluded.calorie_target_score,
                water_score = excluded.water_score,
                perfect_day_score = excluded.perfect_day_score,
                task_scores_json = excluded.task_scores_json,
                streak_score = excluded.streak_score,
                total_score = excluded.total_score,
                updated_at = excluded.updated_at
            """,
            (int(competition["id"]), user.id, score_day.isoformat(), task_scores.get("food", 0),
             task_scores.get("calories", task_scores.get("calories_light", 0)), task_scores.get("water", 0),
             task_scores.get("perfect", 0), json.dumps(task_scores), legacy_result.streak_score,
             sum(task_scores.values()) + legacy_result.streak_score),
        )

    def recalculate_competition_scores_for_day(self, telegram_id: int, score_day: str) -> None:
        try:
            parsed_day = date.fromisoformat(score_day)
        except ValueError:
            return
        user = self.get_or_create_user(telegram_id)
        today = self._competition_today()
        with self.connect() as conn:
            competition = self._get_or_join_weekly_competition(conn, user, today)
            if competition is None:
                return
            end_day = min(date.fromisoformat(str(competition["end_date"])) - timedelta(days=1), today)
            if parsed_day <= end_day:
                for offset in range((end_day - parsed_day).days + 1):
                    self._recalculate_competition_day(conn, user, competition, parsed_day + timedelta(days=offset))
            conn.execute(
                """
                UPDATE competition_participants
                SET score = COALESCE((
                    SELECT SUM(total_score) FROM competition_daily_scores
                    WHERE competition_id = ? AND user_id = ?
                ), 0)
                WHERE competition_id = ? AND user_id = ?
                """,
                (int(competition["id"]), user.id, int(competition["id"]), user.id),
            )

    def recalculate_active_competition(self, telegram_id: int) -> None:
        self.recalculate_competition_scores_for_day(telegram_id, self._competition_today().isoformat())

    def get_competition_state(self, telegram_id: int) -> dict[str, Any]:
        user = self.get_or_create_user(telegram_id)
        today = self._competition_today()
        current_week_start = today - timedelta(days=today.weekday())
        goal_type = self._competition_goal_type(user.goal)
        history: dict[str, Any] | None = None
        with self.connect() as conn:
            self._finalize_expired_competitions(conn, today)
            latest = conn.execute(
                """
                SELECT competitions.id AS competition_id, competitions.end_date,
                       competition_participants.final_rank, competition_participants.score
                FROM competition_participants
                JOIN competitions ON competitions.id = competition_participants.competition_id
                WHERE competition_participants.user_id = ?
                  AND competitions.status = 'completed'
                  AND competitions.end_date <= ?
                ORDER BY competitions.end_date DESC LIMIT 1
                """,
                (user.id, current_week_start.isoformat()),
            ).fetchone()
            if latest is not None:
                history = {"competition_id": int(latest["competition_id"]), "end_date": str(latest["end_date"]),
                           "final_rank": int(latest["final_rank"]) if latest["final_rank"] else None,
                           "score": int(latest["score"] or 0)}
        if not goal_type or not user.calorie_target:
            return {"eligible": False, "reason": "goal_required", "competition": None,
                    "participants": [], "today_score_breakdown": None, "last_competition": history}

        self.recalculate_competition_scores_for_day(telegram_id, today.isoformat())
        with self.connect() as conn:
            competition = self._get_or_join_weekly_competition(conn, user, today)
            if competition is None:
                return {"eligible": False, "reason": "goal_required", "competition": None,
                        "participants": [], "today_score_breakdown": None, "last_competition": history}
            participants = conn.execute(
                """
                SELECT competition_participants.user_id, competition_participants.score,
                       competition_participants.joined_at, users.display_name,
                       (
                         SELECT COUNT(*) FROM food_entries
                         WHERE food_entries.user_id = competition_participants.user_id
                           AND date(food_entries.created_at, '+3 hours') >= competitions.start_date
                           AND date(food_entries.created_at, '+3 hours') < competitions.end_date
                       ) AS meal_entries
                FROM competition_participants JOIN users ON users.id = competition_participants.user_id
                JOIN competitions ON competitions.id = competition_participants.competition_id
                WHERE competition_participants.competition_id = ?
                ORDER BY competition_participants.score DESC, meal_entries DESC,
                         competition_participants.joined_at ASC,
                         competition_participants.user_id ASC
                """,
                (int(competition["id"]),),
            ).fetchall()
            rendered_participants = []
            current_rank = 0
            current_score = 0
            for position, participant in enumerate(participants, start=1):
                is_current = int(participant["user_id"]) == user.id
                if is_current:
                    current_rank, current_score = position, int(participant["score"] or 0)
                rendered_participants.append({"rank": position,
                    "name": str(participant["display_name"] or f"Участник {position}"),
                    "score": int(participant["score"] or 0), "is_current_user": is_current})
            daily = conn.execute(
                """
                SELECT task_scores_json, streak_score, total_score
                FROM competition_daily_scores
                WHERE competition_id = ? AND user_id = ? AND score_date = ?
                """,
                (int(competition["id"]), user.id, today.isoformat()),
            ).fetchone()
            values = dict(daily) if daily is not None else {}
            try:
                task_scores = json.loads(str(values.get("task_scores_json") or "{}"))
            except json.JSONDecodeError:
                task_scores = {}
            today_tasks = competition_tasks_for_day(int(competition["id"]), today)
            rendered_tasks = [
                {**task, "score": int(task_scores.get(str(task["key"]), 0))}
                for task in today_tasks
            ]
            breakdown = {"food_logged": int(task_scores.get("food", 0)),
                         "calorie_target": int(task_scores.get("calories", task_scores.get("calories_light", 0))),
                         "water": int(task_scores.get("water", 0)),
                         "perfect_day": int(task_scores.get("perfect", 0)),
                         "streak": int(values.get("streak_score") or 0),
                         "total": int(values.get("total_score") or 0),
                         "base_max": sum(int(task["points"]) for task in today_tasks)}
            return {"eligible": True, "reason": None,
                    "competition": {"id": int(competition["id"]), "title": "Лига недели",
                        "start_date": str(competition["start_date"]), "end_date": str(competition["end_date"]),
                        "league_tier": str(competition["league_tier"]), "goal_type": str(competition["goal_type"]),
                        "days_left": max(0, (date.fromisoformat(str(competition["end_date"])) - today).days)},
                    "current_user_rank": current_rank, "current_user_score": current_score,
                    "participants": rendered_participants, "today_tasks": rendered_tasks,
                    "today_score_breakdown": breakdown,
                    "last_competition": history}

    def record_user_event(self, telegram_id: int, event_type: str) -> User:
        user = self.get_or_create_user(telegram_id)
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO user_events (user_id, event_type) VALUES (?, ?)",
                (user.id, event_type),
            )
        return user

    def record_start(self, telegram_id: int) -> User:
        return self.record_user_event(telegram_id, "start")

    def get_users_who_started(self) -> list[User]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT users.*
                FROM users
                JOIN user_events ON user_events.user_id = users.id
                WHERE user_events.event_type = 'start'
                ORDER BY users.id ASC
                """
            ).fetchall()
            return [self._user_from_row(row) for row in rows]

    def get_started_users_without_food(self) -> list[User]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT users.*
                FROM users
                JOIN user_events ON user_events.user_id = users.id
                WHERE user_events.event_type = 'start'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM food_entries
                      WHERE food_entries.user_id = users.id
                  )
                ORDER BY users.id ASC
                """
            ).fetchall()
            return [self._user_from_row(row) for row in rows]

    def get_started_users_without_goal(self) -> list[User]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT users.*
                FROM users
                JOIN user_events ON user_events.user_id = users.id
                WHERE user_events.event_type = 'start'
                  AND users.calorie_target IS NULL
                ORDER BY users.id ASC
                """
            ).fetchall()
            return [self._user_from_row(row) for row in rows]

    def get_users_for_goal_setup_nudge(self) -> list[User]:
        placeholders = ", ".join("?" for _ in SERVICE_EVENT_TYPES)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                WITH latest_start AS (
                    SELECT user_id, MAX(created_at) AS started_at
                    FROM user_events
                    WHERE event_type = 'start'
                    GROUP BY user_id
                )
                SELECT users.*
                FROM users
                JOIN latest_start ON latest_start.user_id = users.id
                WHERE users.activation_disabled = 0
                  AND users.calorie_target IS NULL
                  AND datetime('now', 'localtime') >= datetime(latest_start.started_at, 'localtime', '+6 hours')
                  AND EXISTS (
                      SELECT 1
                      FROM user_events activity
                      WHERE activity.user_id = users.id
                        AND activity.event_type != 'start'
                        AND activity.event_type NOT IN ({placeholders})
                  )
                ORDER BY users.id ASC
                """,
                SERVICE_EVENT_TYPES,
            ).fetchall()
            candidates = [self._user_from_row(row) for row in rows]

        one_food_no_return_ids = {user.id for user in self.get_users_with_one_food_no_return()}
        return [user for user in candidates if user.id not in one_food_no_return_ids]

    def get_users_with_one_food_no_return(self) -> list[User]:
        placeholders = ", ".join("?" for _ in SERVICE_EVENT_TYPES)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                WITH user_activity AS (
                    SELECT user_id, created_at
                    FROM food_entries
                    UNION ALL
                    SELECT user_id, created_at
                    FROM user_events
                    WHERE event_type NOT IN ({placeholders})
                )
                SELECT users.*
                FROM users
                JOIN food_entries ON food_entries.user_id = users.id
                LEFT JOIN user_activity ON user_activity.user_id = users.id
                GROUP BY users.id
                HAVING COUNT(DISTINCT food_entries.id) = 1
                   AND datetime(MAX(user_activity.created_at), 'localtime') <= datetime('now', 'localtime', '-2 days')
                ORDER BY users.id ASC
                """,
                SERVICE_EVENT_TYPES,
            ).fetchall()
            return [self._user_from_row(row) for row in rows]

    def get_users_with_goal_no_food_no_return(self) -> list[User]:
        placeholders = ", ".join("?" for _ in SERVICE_EVENT_TYPES)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                WITH user_activity AS (
                    SELECT user_id, created_at
                    FROM user_events
                    WHERE event_type NOT IN ({placeholders})
                )
                SELECT users.*
                FROM users
                LEFT JOIN food_entries ON food_entries.user_id = users.id
                LEFT JOIN user_activity ON user_activity.user_id = users.id
                WHERE users.calorie_target IS NOT NULL
                  AND users.goal_set_at IS NOT NULL
                GROUP BY users.id
                HAVING COUNT(DISTINCT food_entries.id) = 0
                   AND datetime(MAX(user_activity.created_at), 'localtime') <= datetime('now', 'localtime', '-2 days')
                ORDER BY users.id ASC
                """,
                SERVICE_EVENT_TYPES,
            ).fetchall()
            return [self._user_from_row(row) for row in rows]

    def get_loyal_users(self) -> list[User]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT users.*
                FROM users
                JOIN food_entries ON food_entries.user_id = users.id
                GROUP BY users.id
                HAVING COUNT(food_entries.id) >= 5
                   AND COUNT(DISTINCT date(food_entries.created_at, '+3 hours')) >= 3
                   AND datetime(MAX(food_entries.created_at), '+3 hours') >= datetime('now', '+3 hours', '-7 days')
                ORDER BY users.id ASC
                """
            ).fetchall()
            return [self._user_from_row(row) for row in rows]

    def record_useful_action(self, telegram_id: int, event_type: str) -> User:
        return self.record_user_event(telegram_id, event_type)

    def disable_activation(self, telegram_id: int) -> None:
        self.get_or_create_user(telegram_id)
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET activation_disabled = 1 WHERE telegram_id = ?",
                (telegram_id,),
            )

    def get_users_for_activation(self) -> list[tuple[User, int]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                WITH latest_start AS (
                    SELECT user_id, MAX(created_at) AS started_at
                    FROM user_events
                    WHERE event_type = 'start'
                    GROUP BY user_id
                )
                SELECT users.*, users.activation_step + 1 AS next_activation_step
                FROM users
                JOIN latest_start ON latest_start.user_id = users.id
                WHERE users.activation_disabled = 0
                  AND users.activation_step < 3
                  AND NOT EXISTS (
                      SELECT 1 FROM user_events actions
                      WHERE actions.user_id = users.id
                        AND actions.event_type != 'start'
                  )
                  AND (
                      (
                          users.activation_step = 0
                          AND datetime('now', 'localtime') >= datetime(latest_start.started_at, 'localtime', '+4 hours')
                      )
                      OR (
                          users.activation_step = 1
                          AND users.last_activation_message_at IS NOT NULL
                          AND datetime('now', 'localtime') >= datetime(users.last_activation_message_at, '+24 hours')
                      )
                      OR (
                          users.activation_step = 2
                          AND users.last_activation_message_at IS NOT NULL
                          AND datetime('now', 'localtime') >= datetime(users.last_activation_message_at, '+3 days')
                      )
                  )
                ORDER BY users.id ASC
                """,
            ).fetchall()
            return [(self._user_from_row(row), int(row["next_activation_step"])) for row in rows]

    def mark_activation_sent(self, telegram_id: int, step: int) -> None:
        self.get_or_create_user(telegram_id)
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE users
                SET activation_step = ?,
                    last_activation_message_at = datetime('now', 'localtime')
                WHERE telegram_id = ?
                """,
                (step, telegram_id),
            )

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
                  AND NOT EXISTS (
                      SELECT 1 FROM external_identities ei WHERE ei.user_id = users.id
                  )
                """,
                (reminder_time, today),
            ).fetchall()
            return [self._user_from_row(row) for row in rows]

    def set_water_reminders_enabled(self, telegram_id: int, enabled: bool) -> User:
        self.get_or_create_user(telegram_id)
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE users
                SET water_reminders_enabled = ?,
                    water_reminder_skip_date = NULL
                WHERE telegram_id = ?
                """,
                (int(enabled), telegram_id),
            )
            row = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
            return self._user_from_row(row)

    def skip_water_reminders_today(self, telegram_id: int, today: str) -> None:
        self.get_or_create_user(telegram_id)
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET water_reminder_skip_date = ? WHERE telegram_id = ?",
                (today, telegram_id),
            )

    def get_users_for_water_reminder(self, slot: str, today: str) -> list[User]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT users.*
                FROM users
                WHERE users.water_reminders_enabled = 1
                  AND NOT EXISTS (
                      SELECT 1 FROM external_identities ei WHERE ei.user_id = users.id
                  )
                  AND (users.water_reminder_skip_date IS NULL OR users.water_reminder_skip_date != ?)
                  AND (
                      SELECT COUNT(*) FROM reminder_logs
                      WHERE reminder_logs.user_id = users.id
                        AND reminder_logs.reminder_type = 'water'
                        AND reminder_logs.status = 'sent'
                        AND date(reminder_logs.sent_at, '+3 hours') = ?
                  ) < 2
                  AND NOT EXISTS (
                      SELECT 1 FROM reminder_logs
                      WHERE reminder_logs.user_id = users.id
                        AND reminder_logs.reminder_type = 'water'
                        AND reminder_logs.slot = ?
                        AND date(reminder_logs.sent_at, '+3 hours') = ?
                  )
                ORDER BY users.id ASC
                """,
                (today, today, slot, today),
            ).fetchall()
            return [self._user_from_row(row) for row in rows]

    def get_users_for_duolingo_push(self, today: str, yesterday: str, day_before_yesterday: str) -> list[User]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                WITH active_days AS (
                    SELECT user_id, date(created_at, 'localtime') AS day
                    FROM user_events
                    WHERE event_type != 'duolingo_push_sent'
                    UNION
                    SELECT user_id, date(created_at, 'localtime') AS day
                    FROM food_entries
                )
                SELECT users.*
                FROM users
                WHERE users.calorie_target IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM external_identities ei WHERE ei.user_id = users.id
                  )
                  AND EXISTS (
                      SELECT 1 FROM active_days
                      WHERE active_days.user_id = users.id AND active_days.day = ?
                  )
                  AND EXISTS (
                      SELECT 1 FROM active_days
                      WHERE active_days.user_id = users.id AND active_days.day = ?
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM active_days
                      WHERE active_days.user_id = users.id AND active_days.day = ?
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM user_events
                      WHERE user_events.user_id = users.id
                        AND user_events.event_type = 'duolingo_push_sent'
                        AND date(user_events.created_at, 'localtime') = ?
                  )
                """,
                (yesterday, day_before_yesterday, today, today),
            ).fetchall()
            return [self._user_from_row(row) for row in rows]

    def mark_duolingo_push_sent(self, telegram_id: int) -> None:
        self.record_user_event(telegram_id, "duolingo_push_sent")

    def mark_reminder_sent(self, telegram_id: int, today: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET reminder_last_sent_date = ? WHERE telegram_id = ?",
                (today, telegram_id),
            )

    def log_reminder(
        self,
        telegram_id: int,
        reminder_type: str,
        slot: str | None,
        status: str,
        error: str | None = None,
    ) -> None:
        user = self.get_or_create_user(telegram_id)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO reminder_logs (user_id, sent_at, reminder_type, slot, status, error)
                VALUES (?, CURRENT_TIMESTAMP, ?, ?, ?, ?)
                """,
                (user.id, reminder_type, slot, status, error[:500] if error else None),
            )
            if status == "sent":
                conn.execute(
                    "INSERT INTO user_events (user_id, event_type) VALUES (?, ?)",
                    (user.id, "reminder_sent" if reminder_type == "daily" else f"{reminder_type}_sent"),
                )

    def has_reactivation_push_today(self, telegram_id: int) -> bool:
        user = self.get_or_create_user(telegram_id)
        with self.connect() as conn:
            return bool(conn.execute(
                """
                SELECT 1
                FROM reminder_logs
                WHERE user_id = ?
                  AND status = 'sent'
                  AND date(sent_at, '+3 hours') = date('now', '+3 hours')
                  AND (
                      reminder_type = 'activation'
                      OR reminder_type = 'duolingo'
                      OR reminder_type = 'streak_rescue'
                      OR reminder_type LIKE 'lifecycle:%'
                  )
                LIMIT 1
                """,
                (user.id,),
            ).fetchone())

    def claim_daily_push_sticker(self, telegram_id: int, push_date: str) -> bool:
        user = self.get_or_create_user(telegram_id)
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO daily_push_stickers (user_id, push_date)
                VALUES (?, ?)
                """,
                (user.id, push_date),
            )
            return cursor.rowcount == 1

    def get_users_for_weekly_report(
        self,
        period_start: str,
        period_end: str,
        report_key: str,
    ) -> list[User]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT users.*
                FROM users
                JOIN food_entries ON food_entries.user_id = users.id
                WHERE date(food_entries.created_at, '+3 hours') BETWEEN ? AND ?
                  AND NOT EXISTS (
                      SELECT 1 FROM external_identities ei WHERE ei.user_id = users.id
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM reminder_logs
                      WHERE reminder_logs.user_id = users.id
                        AND reminder_logs.reminder_type = 'weekly_report'
                        AND reminder_logs.slot = ?
                  )
                GROUP BY users.id
                HAVING COUNT(DISTINCT date(food_entries.created_at, '+3 hours')) >= 3
                ORDER BY users.id ASC
                """,
                (period_start, period_end, report_key),
            ).fetchall()
            return [self._user_from_row(row) for row in rows]

    def get_weekly_report_summary(
        self,
        telegram_id: int,
        period_start: str,
        period_end: str,
    ) -> dict[str, Any]:
        user = self.get_or_create_user(telegram_id)
        with self.connect() as conn:
            row = conn.execute(
                """
                WITH daily AS (
                    SELECT
                        date(created_at, '+3 hours') AS day,
                        COUNT(*) AS entries,
                        SUM(calories) AS calories
                    FROM food_entries
                    WHERE user_id = ?
                      AND date(created_at, '+3 hours') BETWEEN ? AND ?
                    GROUP BY day
                )
                SELECT
                    COUNT(*) AS active_days,
                    COALESCE(SUM(entries), 0) AS entries,
                    COALESCE(AVG(calories), 0) AS average_calories,
                    COALESCE(SUM(CASE WHEN entries >= 3 THEN 1 ELSE 0 END), 0) AS full_days,
                    COALESCE(SUM(
                        CASE
                            WHEN ? IS NOT NULL
                             AND calories BETWEEN ? * 0.9 AND ? * 1.1
                            THEN 1 ELSE 0
                        END
                    ), 0) AS target_days
                FROM daily
                """,
                (
                    user.id,
                    period_start,
                    period_end,
                    user.calorie_target,
                    user.calorie_target,
                    user.calorie_target,
                ),
            ).fetchone()
            top_food = conn.execute(
                """
                SELECT MIN(title) AS title, COUNT(*) AS uses
                FROM food_entries
                WHERE user_id = ?
                  AND date(created_at, '+3 hours') BETWEEN ? AND ?
                  AND trim(title) != ''
                GROUP BY lower(trim(title))
                ORDER BY uses DESC, MAX(created_at) DESC
                LIMIT 1
                """,
                (user.id, period_start, period_end),
            ).fetchone()

        progress = self.get_user_progress_stats(telegram_id)
        return {
            "active_days": int(row["active_days"] or 0),
            "entries": int(row["entries"] or 0),
            "average_calories": int(round(float(row["average_calories"] or 0))),
            "full_days": int(row["full_days"] or 0),
            "target_days": int(row["target_days"] or 0) if user.calorie_target else None,
            "top_food": str(top_food["title"]) if top_food else None,
            "current_streak": int(progress["current_streak"] or 0),
            "best_streak": int(progress["best_streak"] or 0),
        }

    def log_broadcast(
        self,
        telegram_id: int,
        campaign_id: str,
        status: str,
        error: str | None = None,
    ) -> None:
        user = self.get_or_create_user(telegram_id)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO broadcast_logs (user_id, campaign_id, sent_at, status, error)
                VALUES (?, ?, CURRENT_TIMESTAMP, ?, ?)
                """,
                (user.id, campaign_id, status, error[:500] if error else None),
            )
            if status == "sent":
                conn.execute(
                    "INSERT INTO user_events (user_id, event_type) VALUES (?, ?)",
                    (user.id, "broadcast_received"),
                )

    def get_users_for_lifecycle_push(self, segment: str, max_steps: int = 3) -> list[tuple[User, int]]:
        if segment == "one_food_no_return":
            candidates = self.get_users_with_one_food_no_return()
        elif segment == "goal_no_food":
            candidates = self.get_users_with_goal_no_food_no_return()
        elif segment == "started_no_goal":
            candidates = self.get_users_for_goal_setup_nudge()
        else:
            return []

        result: list[tuple[User, int]] = []
        with self.connect() as conn:
            external_user_ids = {
                int(row["user_id"])
                for row in conn.execute("SELECT user_id FROM external_identities").fetchall()
            }
            for user in candidates:
                if user.id in external_user_ids:
                    continue
                row = conn.execute(
                    """
                    SELECT
                        COALESCE(MAX(CASE WHEN status = 'sent' THEN step END), 0) AS last_sent_step,
                        MAX(CASE WHEN status = 'sent' THEN sent_at END) AS last_sent_at,
                        (
                            SELECT sent_at
                            FROM lifecycle_pushes latest
                            WHERE latest.user_id = lifecycle_pushes.user_id
                              AND latest.segment = lifecycle_pushes.segment
                            ORDER BY latest.sent_at DESC
                            LIMIT 1
                        ) AS last_attempt_at,
                        (
                            SELECT status
                            FROM lifecycle_pushes latest
                            WHERE latest.user_id = lifecycle_pushes.user_id
                              AND latest.segment = lifecycle_pushes.segment
                            ORDER BY latest.sent_at DESC
                            LIMIT 1
                        ) AS last_attempt_status
                    FROM lifecycle_pushes
                    WHERE user_id = ? AND segment = ?
                    """,
                    (user.id, segment),
                ).fetchone()
                last_step = int(row["last_sent_step"] or 0)
                if last_step >= max_steps:
                    continue
                if row["last_attempt_status"] == "blocked":
                    continue
                last_attempt_at = row["last_attempt_at"]
                if last_step == 0 and last_attempt_at:
                    retry_is_over = conn.execute(
                        """
                        SELECT datetime(?, 'localtime', '+2 days') <= datetime('now', 'localtime')
                        """,
                        (last_attempt_at,),
                    ).fetchone()[0]
                    if not retry_is_over:
                        continue
                last_sent_at = row["last_sent_at"]
                if last_step > 0 and last_sent_at:
                    wait_is_over = conn.execute(
                        """
                        SELECT datetime(?, 'localtime', '+2 days') <= datetime('now', 'localtime')
                        """,
                        (last_sent_at,),
                    ).fetchone()[0]
                    if not wait_is_over:
                        continue
                result.append((user, last_step + 1))
        return result

    def log_lifecycle_push(
        self,
        telegram_id: int,
        segment: str,
        step: int,
        status: str,
        error: str | None = None,
    ) -> None:
        user = self.get_or_create_user(telegram_id)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO lifecycle_pushes (user_id, segment, step, sent_at, status, error)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?, ?)
                """,
                (user.id, segment, step, status, error[:500] if error else None),
            )

    def add_food_entry(
        self,
        telegram_id: int,
        estimate: FoodEstimate,
        source: str,
        created_at: datetime | None = None,
    ) -> FoodEntry:
        user = self.get_or_create_user(telegram_id)
        with self.connect() as conn:
            values = (
                user.id,
                estimate.title,
                estimate.description,
                estimate.calories,
                estimate.protein,
                estimate.fat,
                estimate.carbs,
                estimate.water_ml,
                estimate.confidence,
                source,
            )
            if created_at is None:
                cursor = conn.execute(
                    """
                    INSERT INTO food_entries
                        (user_id, title, description, calories, protein, fat, carbs, water_ml, confidence, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
            else:
                stored_created_at = created_at
                if stored_created_at.tzinfo is not None:
                    stored_created_at = stored_created_at.astimezone(timezone.utc).replace(tzinfo=None)
                cursor = conn.execute(
                    """
                    INSERT INTO food_entries
                        (user_id, title, description, calories, protein, fat, carbs, water_ml, confidence, source, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (*values, stored_created_at.isoformat(sep=" ", timespec="seconds")),
                )
            row = conn.execute("SELECT * FROM food_entries WHERE id = ?", (cursor.lastrowid,)).fetchone()
            entry = self._entry_from_row(row)
        self.recalculate_competition_scores_for_day(
            telegram_id, self._competition_day_from_timestamp(entry.created_at).isoformat()
        )
        return entry

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
                    carbs = carbs * ?,
                    water_ml = water_ml * ?
                WHERE id = ?
                """,
                (factor, factor, factor, factor, factor, entry_id),
            )
        updated = self.get_food_entry(entry_id, telegram_id)
        self.recalculate_competition_scores_for_day(
            telegram_id, self._competition_day_from_timestamp(entry.created_at).isoformat()
        )
        return updated

    def replace_food_entry_estimate(self, entry_id: int, telegram_id: int, estimate: FoodEstimate) -> FoodEntry | None:
        entry = self.get_food_entry(entry_id, telegram_id)
        if entry is None:
            return None
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE food_entries
                SET title = ?, description = ?, calories = ?, protein = ?, fat = ?, carbs = ?, water_ml = ?, confidence = ?
                WHERE id = ?
                """,
                (
                    estimate.title,
                    estimate.description,
                    estimate.calories,
                    estimate.protein,
                    estimate.fat,
                    estimate.carbs,
                    estimate.water_ml,
                    estimate.confidence,
                    entry_id,
                ),
            )
        updated = self.get_food_entry(entry_id, telegram_id)
        self.recalculate_competition_scores_for_day(
            telegram_id, self._competition_day_from_timestamp(entry.created_at).isoformat()
        )
        return updated

    def delete_food_entry(self, entry_id: int, telegram_id: int) -> bool:
        entry = self.get_food_entry(entry_id, telegram_id)
        if entry is None:
            return False
        with self.connect() as conn:
            conn.execute("DELETE FROM food_entries WHERE id = ?", (entry_id,))
        self.recalculate_competition_scores_for_day(
            telegram_id, self._competition_day_from_timestamp(entry.created_at).isoformat()
        )
        return True

    def get_today_entries(self, telegram_id: int) -> list[FoodEntry]:
        user = self.get_or_create_user(telegram_id)
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM food_entries
                WHERE user_id = ? AND date(created_at, '+3 hours') = date('now', '+3 hours')
                ORDER BY created_at ASC
                """,
                (user.id,),
            ).fetchall()
            return [self._entry_from_row(row) for row in rows]

    def get_entries_for_day(self, telegram_id: int, day: str) -> list[FoodEntry]:
        user = self.get_or_create_user(telegram_id)
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM food_entries
                WHERE user_id = ? AND date(created_at, '+3 hours') = ?
                ORDER BY created_at ASC
                """,
                (user.id, day),
            ).fetchall()
            return [self._entry_from_row(row) for row in rows]

    def get_popular_foods(self, telegram_id: int, limit: int = 3) -> list[dict[str, Any]]:
        user = self.get_or_create_user(telegram_id)
        with self.connect() as conn:
            conn.create_function("normalize_food_title", 1, _normalize_food_title)
            rows = conn.execute(
                """
                SELECT MIN(TRIM(title)) AS title,
                       COUNT(*) AS entries,
                       MAX(created_at) AS last_used_at
                FROM food_entries
                WHERE user_id = ?
                  AND title IS NOT NULL
                  AND normalize_food_title(title) != ''
                GROUP BY normalize_food_title(title)
                ORDER BY entries DESC, last_used_at DESC
                LIMIT ?
                """,
                (user.id, limit),
            ).fetchall()
            return [
                {
                    "title": " ".join(str(row["title"]).split()).capitalize(),
                    "entries": int(row["entries"]),
                }
                for row in rows
            ]

    def get_food_entry_days(self, telegram_id: int) -> list[str]:
        user = self.get_or_create_user(telegram_id)
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT date(created_at, '+3 hours') AS day
                FROM food_entries
                WHERE user_id = ?
                ORDER BY day ASC
                """,
                (user.id,),
            ).fetchall()
            return [row["day"] for row in rows if row["day"]]

    def add_water_entry(self, telegram_id: int, amount_ml: int = 200) -> dict[str, int]:
        amount_ml = max(50, min(500, int(amount_ml)))
        user = self.get_or_create_user(telegram_id)
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO water_entries (user_id, amount_ml) VALUES (?, ?)",
                (user.id, amount_ml),
            )
            conn.execute(
                "INSERT INTO user_events (user_id, event_type) VALUES (?, 'miniapp_water_added')",
                (user.id,),
            )
        self.recalculate_active_competition(telegram_id)
        return self.get_water_summary(telegram_id)

    def remove_last_water_entry(self, telegram_id: int) -> dict[str, int]:
        user = self.get_or_create_user(telegram_id)
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT id FROM water_entries
                WHERE user_id = ? AND date(created_at, '+3 hours') = date('now', '+3 hours')
                ORDER BY id DESC LIMIT 1
                """,
                (user.id,),
            ).fetchone()
            if row:
                conn.execute("DELETE FROM water_entries WHERE id = ?", (row["id"],))
        self.recalculate_active_competition(telegram_id)
        return self.get_water_summary(telegram_id)

    def get_water_summary(self, telegram_id: int, day: str | None = None) -> dict[str, int]:
        user = self.get_or_create_user(telegram_id)
        target = user.water_target or (calculate_water_target(user.weight) if user.weight else 2000)
        with self.connect() as conn:
            day_filter = day or conn.execute("SELECT date('now', '+3 hours')").fetchone()[0]
            manual_ml = conn.execute(
                """
                SELECT COALESCE(SUM(amount_ml), 0) FROM water_entries
                WHERE user_id = ? AND date(created_at, '+3 hours') = ?
                """,
                (user.id, day_filter),
            ).fetchone()[0]
            food_ml = conn.execute(
                """
                SELECT COALESCE(SUM(water_ml), 0) FROM food_entries
                WHERE user_id = ? AND date(created_at, '+3 hours') = ?
                """,
                (user.id, day_filter),
            ).fetchone()[0]
        manual = round(float(manual_ml or 0))
        food = round(float(food_ml or 0))
        total = manual + food
        return {
            "manual_ml": manual,
            "food_ml": food,
            "total_ml": total,
            "target_ml": int(target),
            "remaining_ml": max(0, int(target) - total),
            "percent": min(100, round(total / int(target) * 100)) if target else 0,
        }

    def get_entries_between(self, telegram_id: int, start: str, end: str) -> list[FoodEntry]:
        user = self.get_or_create_user(telegram_id)
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM food_entries
                WHERE user_id = ? AND created_at >= ? AND created_at < ?
                ORDER BY created_at ASC
                """,
                (user.id, start, end),
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

    def get_users_for_streak_rescue(self, today: str, yesterday: str) -> list[User]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT users.*
                FROM users
                WHERE users.current_streak >= 3
                  AND users.last_active_date = ?
                  AND NOT EXISTS (
                      SELECT 1 FROM external_identities ei WHERE ei.user_id = users.id
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM food_entries
                      WHERE food_entries.user_id = users.id
                        AND date(food_entries.created_at, '+3 hours') = ?
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM streak_freezes
                      WHERE streak_freezes.user_id = users.id
                        AND streak_freezes.freeze_date >= date(?, '-6 days')
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM reminder_logs
                      WHERE reminder_logs.user_id = users.id
                        AND reminder_logs.reminder_type = 'streak_rescue'
                        AND reminder_logs.status = 'sent'
                        AND date(reminder_logs.sent_at, '+3 hours') = ?
                  )
                ORDER BY users.id ASC
                """,
                (yesterday, today, today, today),
            ).fetchall()
            return [self._user_from_row(row) for row in rows]

    def get_streak_freeze_days(self, telegram_id: int) -> list[str]:
        user = self.get_or_create_user(telegram_id)
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT freeze_date
                FROM streak_freezes
                WHERE user_id = ?
                ORDER BY freeze_date ASC
                """,
                (user.id,),
            ).fetchall()
            return [str(row["freeze_date"]) for row in rows]

    def get_streak_freeze_status(self, telegram_id: int) -> dict[str, Any]:
        user = self.get_or_create_user(telegram_id)
        with self.connect() as conn:
            today = conn.execute("SELECT date('now', '+3 hours')").fetchone()[0]
            yesterday = conn.execute("SELECT date('now', '+3 hours', '-1 day')").fetchone()[0]
            moscow_hour = int(conn.execute("SELECT strftime('%H', 'now', '+3 hours')").fetchone()[0])
            current_streak, _, last_streak_date = self._streaks_for_user(conn, user.id)
            has_food_today = bool(conn.execute(
                """
                SELECT 1 FROM food_entries
                WHERE user_id = ? AND date(created_at, '+3 hours') = ?
                LIMIT 1
                """,
                (user.id, today),
            ).fetchone())
            latest_freeze = conn.execute(
                """
                SELECT freeze_date
                FROM streak_freezes
                WHERE user_id = ? AND freeze_date >= date(?, '-6 days')
                ORDER BY freeze_date DESC
                LIMIT 1
                """,
                (user.id, today),
            ).fetchone()

            frozen_today = bool(latest_freeze and latest_freeze["freeze_date"] == today)
            eligible = current_streak >= 3
            at_risk = eligible and last_streak_date == yesterday and not has_food_today and not frozen_today
            next_available_date = None
            if latest_freeze:
                next_available_date = (
                    datetime.fromisoformat(str(latest_freeze["freeze_date"])).date() + timedelta(days=7)
                ).isoformat()

            return {
                "eligible": eligible,
                "available": eligible and latest_freeze is None,
                "frozen_today": frozen_today,
                "at_risk": at_risk,
                "show_rescue": frozen_today or (at_risk and moscow_hour >= 18),
                "next_available_date": next_available_date,
                "current_streak": current_streak,
            }

    def freeze_streak(self, telegram_id: int) -> dict[str, int | str]:
        user = self.get_or_create_user(telegram_id)
        with self.connect() as conn:
            today = conn.execute("SELECT date('now', '+3 hours')").fetchone()[0]
            yesterday = conn.execute("SELECT date('now', '+3 hours', '-1 day')").fetchone()[0]
            active_today = conn.execute(
                """
                SELECT 1 FROM food_entries
                WHERE user_id = ? AND date(created_at, '+3 hours') = ?
                LIMIT 1
                """,
                (user.id, today),
            ).fetchone()
            if active_today:
                return {"status": "active_today", "current_streak": int(user.current_streak or 0)}

            existing = conn.execute(
                "SELECT 1 FROM streak_freezes WHERE user_id = ? AND freeze_date = ?",
                (user.id, today),
            ).fetchone()
            if existing:
                return {"status": "already_frozen", "current_streak": int(user.current_streak or 0)}

            current_streak, best_streak, last_streak_date = self._streaks_for_user(conn, user.id)
            if current_streak < 3 or last_streak_date != yesterday:
                return {"status": "not_at_risk", "current_streak": int(current_streak or 0)}

            recent_freeze = conn.execute(
                """
                SELECT 1 FROM streak_freezes
                WHERE user_id = ? AND freeze_date >= date(?, '-6 days')
                LIMIT 1
                """,
                (user.id, today),
            ).fetchone()
            if recent_freeze:
                return {"status": "cooldown", "current_streak": int(current_streak)}

            conn.execute(
                "INSERT INTO streak_freezes (user_id, freeze_date) VALUES (?, ?)",
                (user.id, today),
            )
            conn.execute(
                """
                UPDATE users
                SET current_streak = ?, best_streak = ?, last_active_date = ?
                WHERE id = ?
                """,
                (current_streak, best_streak, today, user.id),
            )
            conn.execute(
                "INSERT INTO user_events (user_id, event_type) VALUES (?, 'nyam_streak_frozen')",
                (user.id,),
            )
            return {"status": "frozen", "current_streak": int(current_streak)}

    def mark_nyam_streak_if_first_today(self, telegram_id: int) -> dict[str, int | bool] | None:
        user = self.get_or_create_user(telegram_id)
        with self.connect() as conn:
            today = conn.execute("SELECT date('now', '+3 hours')").fetchone()[0]
            entries_today = int(conn.execute(
                """
                SELECT COUNT(*) FROM food_entries
                WHERE user_id = ?
                  AND date(created_at, '+3 hours') = ?
                """,
                (user.id, today),
            ).fetchone()[0])
            if entries_today == 0 or entries_today > 1:
                return None

            row = conn.execute(
                """
                SELECT best_streak
                FROM users
                WHERE id = ?
                """,
                (user.id,),
            ).fetchone()
            previous_best_streak = int(row["best_streak"] or 0)
            conn.execute(
                "DELETE FROM streak_freezes WHERE user_id = ? AND freeze_date = ?",
                (user.id, today),
            )
            current_streak, calculated_best, last_active_date = self._streaks_for_user(conn, user.id)
            best_streak = max(previous_best_streak, calculated_best, current_streak)
            best_updated = best_streak > previous_best_streak
            conn.execute(
                """
                UPDATE users
                SET current_streak = ?,
                    best_streak = ?,
                    last_active_date = ?
                WHERE id = ?
                """,
                (current_streak, best_streak, last_active_date, user.id),
            )
            conn.execute(
                "INSERT INTO user_events (user_id, event_type) VALUES (?, ?)",
                (user.id, "nyam_streak_day_counted"),
            )
            return {
                "current_streak": current_streak,
                "best_streak": best_streak,
                "best_updated": best_updated,
            }

    def get_user_progress_stats(self, telegram_id: int) -> dict[str, int]:
        user = self.get_or_create_user(telegram_id)
        with self.connect() as conn:
            current_streak, best_streak, last_active_date = self._streaks_for_user(conn, user.id)
            if (
                current_streak != int(user.current_streak or 0)
                or best_streak != int(user.best_streak or 0)
                or last_active_date != user.last_active_date
            ):
                conn.execute(
                    """
                    UPDATE users
                    SET current_streak = ?,
                        best_streak = ?,
                        last_active_date = ?
                    WHERE id = ?
                    """,
                    (current_streak, best_streak, last_active_date, user.id),
                )
            total_entries = conn.execute(
                "SELECT COUNT(*) FROM food_entries WHERE user_id = ?",
                (user.id,),
            ).fetchone()[0]
            active_days = conn.execute(
                """
                SELECT COUNT(DISTINCT date(created_at, '+3 hours'))
                FROM food_entries
                WHERE user_id = ?
                """,
                (user.id,),
            ).fetchone()[0]
            days_with_nyammetr = conn.execute(
                """
                SELECT CAST(julianday(date('now', '+3 hours')) - julianday(date(created_at, '+3 hours')) AS INTEGER) + 1
                FROM users
                WHERE id = ?
                """,
                (user.id,),
            ).fetchone()[0]

        return {
            "current_streak": int(current_streak or 0),
            "best_streak": int(best_streak or 0),
            "total_entries": int(total_entries),
            "active_days": int(active_days or 0),
            "days_with_nyammetr": max(1, int(days_with_nyammetr or 1)),
        }

    def achievement_context(self, telegram_id: int) -> dict[str, int | float | None]:
        user = self.get_or_create_user(telegram_id)
        with self.connect() as conn:
            current_streak, best_streak, last_active_date = self._streaks_for_user(conn, user.id)
            row = conn.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM food_entries WHERE user_id = ?) AS total_entries,
                    (SELECT COUNT(*) FROM food_entries WHERE user_id = ? AND source = 'photo') AS photo_entries,
                    (
                        SELECT COUNT(*) FROM food_entries
                        WHERE user_id = ? AND date(created_at, '+3 hours') = date('now', '+3 hours')
                    ) AS today_entries,
                    (
                        SELECT COALESCE(SUM(calories), 0) FROM food_entries
                        WHERE user_id = ? AND date(created_at, '+3 hours') = date('now', '+3 hours')
                    ) AS today_calories,
                    (
                        SELECT COUNT(DISTINCT date(created_at, '+3 hours')) FROM food_entries
                        WHERE user_id = ?
                    ) AS active_days,
                    (
                        SELECT COUNT(*) FROM food_entries
                        WHERE user_id = ? AND time(created_at, '+3 hours') < '12:00:00'
                    ) AS breakfast_entries
                """,
                (user.id, user.id, user.id, user.id, user.id, user.id),
            ).fetchone()
            today_food_rows = conn.execute(
                """
                SELECT title, description
                FROM food_entries
                WHERE user_id = ? AND date(created_at, '+3 hours') = date('now', '+3 hours')
                """,
                (user.id,),
            ).fetchall()
        vegetable_entries_today, sweet_entries_today = daily_food_signals(
            [(entry["title"], entry["description"]) for entry in today_food_rows]
        )
        return {
            "total_entries": int(row["total_entries"] or 0),
            "photo_entries": int(row["photo_entries"] or 0),
            "today_entries": int(row["today_entries"] or 0),
            "today_calories": float(row["today_calories"] or 0),
            "current_streak": int(current_streak or 0),
            "best_streak": int(best_streak or 0),
            "active_days": int(row["active_days"] or 0),
            "breakfast_entries": int(row["breakfast_entries"] or 0),
            "vegetable_entries_today": vegetable_entries_today,
            "sweet_entries_today": sweet_entries_today,
            "referral_count": self.get_referral_progress(telegram_id)["activated"],
            "calorie_target": user.calorie_target,
        }

    def register_referral(self, invited_telegram_id: int, inviter_telegram_id: int) -> bool:
        if invited_telegram_id == inviter_telegram_id:
            return False
        invited = self.get_or_create_user(invited_telegram_id)
        inviter = self.get_or_create_user(inviter_telegram_id)
        with self.connect() as conn:
            already_active = conn.execute(
                """
                SELECT EXISTS(SELECT 1 FROM food_entries WHERE user_id = ?)
                       OR EXISTS(SELECT 1 FROM users WHERE id = ? AND calorie_target IS NOT NULL)
                """,
                (invited.id, invited.id),
            ).fetchone()[0]
            if already_active:
                return False
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO referrals (inviter_user_id, invited_user_id)
                VALUES (?, ?)
                """,
                (inviter.id, invited.id),
            )
            return bool(cursor.rowcount)

    def activate_referral(self, invited_telegram_id: int) -> bool:
        invited = self.get_or_create_user(invited_telegram_id)
        inviter_telegram_id: int | None = None
        with self.connect() as conn:
            referral = conn.execute(
                """
                SELECT inviter.telegram_id
                FROM referrals
                JOIN users AS inviter ON inviter.id = referrals.inviter_user_id
                WHERE referrals.invited_user_id = ? AND referrals.activated_at IS NULL
                """,
                (invited.id,),
            ).fetchone()
            if referral is None:
                return False
            inviter_telegram_id = int(referral["telegram_id"])
            cursor = conn.execute(
                """
                UPDATE referrals
                SET activated_at = CURRENT_TIMESTAMP
                WHERE invited_user_id = ? AND activated_at IS NULL
                """,
                (invited.id,),
            )
            activated = bool(cursor.rowcount)
        if activated and inviter_telegram_id is not None:
            self.unlock_available_achievements(inviter_telegram_id)
        return activated

    def get_referral_progress(self, telegram_id: int) -> dict[str, int]:
        user = self.get_or_create_user(telegram_id)
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS invited,
                       SUM(CASE WHEN activated_at IS NOT NULL THEN 1 ELSE 0 END) AS activated
                FROM referrals
                WHERE inviter_user_id = ?
                """,
                (user.id,),
            ).fetchone()
        return {
            "invited": int(row["invited"] or 0),
            "activated": int(row["activated"] or 0),
        }

    def unlock_available_achievements(self, telegram_id: int) -> list[Achievement]:
        user = self.get_or_create_user(telegram_id)
        achievements = available_achievements(self.achievement_context(telegram_id))
        unlocked: list[Achievement] = []
        with self.connect() as conn:
            for achievement in achievements:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO user_achievements (user_id, achievement_key)
                    VALUES (?, ?)
                    """,
                    (user.id, achievement.key),
                )
                if cursor.rowcount:
                    unlocked.append(achievement)
        return unlocked

    def get_user_achievements(self, telegram_id: int) -> list[sqlite3.Row]:
        user = self.get_or_create_user(telegram_id)
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT achievement_key, unlocked_at
                FROM user_achievements
                WHERE user_id = ?
                ORDER BY unlocked_at ASC, id ASC
                """,
                (user.id,),
            ).fetchall()

    def get_or_create_daily_mission(self, telegram_id: int) -> sqlite3.Row:
        user = self.get_or_create_user(telegram_id)
        with self.connect() as conn:
            today = conn.execute("SELECT date('now', 'localtime')").fetchone()[0]
            row = conn.execute(
                """
                SELECT * FROM daily_missions
                WHERE user_id = ? AND mission_date = ?
                """,
                (user.id, today),
            ).fetchone()
            if row:
                return row

            mission_key = self._choose_daily_mission(conn, user, today)
            conn.execute(
                """
                INSERT INTO daily_missions (user_id, mission_key, mission_date)
                VALUES (?, ?, ?)
                """,
                (user.id, mission_key, today),
            )
            return conn.execute(
                """
                SELECT * FROM daily_missions
                WHERE user_id = ? AND mission_date = ?
                """,
                (user.id, today),
            ).fetchone()

    def _choose_daily_mission(self, conn: sqlite3.Connection, user: User, today: str) -> str:
        days_with_nyammetr = int(
            conn.execute(
                """
                SELECT CAST(julianday(?) - julianday(date(created_at, 'localtime')) AS INTEGER) + 1
                FROM users
                WHERE id = ?
                """,
                (today, user.id),
            ).fetchone()[0]
            or 1
        )
        if days_with_nyammetr <= 3:
            candidates = list(SIMPLE_MISSION_KEYS)
        else:
            candidates = list(MISSION_ORDER)

        if not user.calorie_target:
            candidates = [key for key in candidates if key != "calorie_range"]
        if not user.protein_target:
            candidates = [key for key in candidates if key != "protein_day"]

        recent_keys = {
            row["mission_key"]
            for row in conn.execute(
                """
                SELECT mission_key FROM daily_missions
                WHERE user_id = ?
                  AND mission_date < ?
                  AND mission_date >= date(?, '-5 days')
                """,
                (user.id, today, today),
            ).fetchall()
        }
        fresh_candidates = [key for key in candidates if key not in recent_keys]
        if fresh_candidates:
            candidates = fresh_candidates

        return random.choice(candidates)

    def daily_mission_context(self, telegram_id: int) -> dict[str, int | float | str | None]:
        user = self.get_or_create_user(telegram_id)
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM food_entries WHERE user_id = ? AND date(created_at, 'localtime') = date('now', 'localtime')) AS food_entries_today,
                    (SELECT COUNT(*) FROM food_entries WHERE user_id = ? AND source = 'photo' AND date(created_at, 'localtime') = date('now', 'localtime')) AS photo_entries_today,
                    (SELECT COUNT(*) FROM food_entries WHERE user_id = ? AND source = 'text' AND date(created_at, 'localtime') = date('now', 'localtime')) AS text_entries_today,
                    (SELECT COALESCE(SUM(calories), 0) FROM food_entries WHERE user_id = ? AND date(created_at, 'localtime') = date('now', 'localtime')) AS calories_today,
                    (SELECT COALESCE(SUM(protein), 0) FROM food_entries WHERE user_id = ? AND date(created_at, 'localtime') = date('now', 'localtime')) AS protein_today,
                    (SELECT time(MIN(created_at), 'localtime') FROM food_entries WHERE user_id = ? AND date(created_at, 'localtime') = date('now', 'localtime')) AS first_entry_time,
                    (SELECT COUNT(*) FROM user_events WHERE user_id = ? AND event_type = 'today_opened_evening' AND date(created_at, 'localtime') = date('now', 'localtime')) AS evening_today_opened,
                    (SELECT COUNT(*) FROM user_events WHERE user_id = ? AND event_type = 'portion_adjustment' AND date(created_at, 'localtime') = date('now', 'localtime')) AS portion_adjustments_today
                """,
                (user.id, user.id, user.id, user.id, user.id, user.id, user.id, user.id),
            ).fetchone()
        return {
            "food_entries_today": int(row["food_entries_today"] or 0),
            "photo_entries_today": int(row["photo_entries_today"] or 0),
            "text_entries_today": int(row["text_entries_today"] or 0),
            "calories_today": float(row["calories_today"] or 0),
            "protein_today": float(row["protein_today"] or 0),
            "first_entry_time": row["first_entry_time"],
            "evening_today_opened": int(row["evening_today_opened"] or 0),
            "portion_adjustments_today": int(row["portion_adjustments_today"] or 0),
            "calorie_target": user.calorie_target,
            "protein_target": user.protein_target,
        }

    def get_daily_mission_status(self, telegram_id: int) -> dict[str, Any]:
        row = self.get_or_create_daily_mission(telegram_id)
        mission = MISSIONS[row["mission_key"]]
        context = self.daily_mission_context(telegram_id)
        return {
            "mission": mission,
            "is_completed": bool(row["is_completed"]),
            "progress_text": mission_progress_text(mission.key, context),
            "mission_date": row["mission_date"],
        }

    def complete_daily_mission_if_ready(self, telegram_id: int) -> DailyMission | None:
        user = self.get_or_create_user(telegram_id)
        row = self.get_or_create_daily_mission(telegram_id)
        if row["is_completed"]:
            return None

        mission = MISSIONS[row["mission_key"]]
        if not mission_is_completed(mission.key, self.daily_mission_context(telegram_id)):
            return None

        with self.connect() as conn:
            conn.execute(
                """
                UPDATE daily_missions
                SET is_completed = 1,
                    completed_at = CURRENT_TIMESTAMP
                WHERE id = ? AND user_id = ? AND is_completed = 0
                """,
                (row["id"], user.id),
            )
        return mission

    def stats(self) -> dict[str, int]:
        with self.connect() as conn:
            users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            today = conn.execute(
                "SELECT COUNT(*) FROM food_entries WHERE date(created_at, 'localtime') = date('now', 'localtime')"
            ).fetchone()[0]
            total = conn.execute("SELECT COUNT(*) FROM food_entries").fetchone()[0]
            return {"users": users, "today_entries": today, "total_entries": total}

    def database_info(self) -> dict[str, int | str | bool]:
        if self.is_postgres:
            parsed = urlsplit(self.database_url)
            database_label = f"postgresql://{parsed.hostname or 'managed'}/{parsed.path.lstrip('/')}"
            exists = True
            size = 0
        else:
            database_label = str(self.path)
            exists = self.path.exists()
            size = self.path.stat().st_size if exists else 0
        with self.connect() as conn:
            users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            entries = conn.execute("SELECT COUNT(*) FROM food_entries").fetchone()[0]
            events = conn.execute("SELECT COUNT(*) FROM user_events").fetchone()[0]
            achievements = conn.execute("SELECT COUNT(*) FROM user_achievements").fetchone()[0]
            missions = conn.execute("SELECT COUNT(*) FROM daily_missions").fetchone()[0]
        backup_info = []
        if self.is_postgres:
            backup_info.append("managed by PostgreSQL provider")
        else:
            for backup_path in self._unique_paths(self.backup_paths):
                score = self._database_score(backup_path)
                backup_info.append(f"{backup_path}: {score[0]} rows, {score[1]} bytes")
        return {
            "path": database_label,
            "exists": exists,
            "size": size,
            "users": users,
            "entries": entries,
            "events": events,
            "achievements": achievements,
            "missions": missions,
            "backups": "\n".join(backup_info),
        }

    def export_snapshot(self, destination: Path) -> Path:
        if self.is_postgres:
            raise RuntimeError(
                "PostgreSQL snapshots are managed in RelaxDev; use the database backup panel."
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as source:
            target = sqlite3.connect(destination)
            try:
                source.backup(target)
            finally:
                target.close()
        return destination

    def admin_stats(self) -> dict[str, int | float]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM users) AS users_total,
                    (SELECT COUNT(*) FROM users WHERE calorie_target IS NOT NULL) AS users_with_goal,
                    (SELECT COUNT(*) FROM users WHERE reminder_time IS NOT NULL) AS users_with_reminders,
                    (SELECT COUNT(*) FROM food_entries) AS entries_total,
                    (SELECT COUNT(*) FROM food_entries WHERE source = 'photo') AS photo_entries,
                    (SELECT COUNT(*) FROM food_entries WHERE source = 'text') AS text_entries,
                    (SELECT COUNT(*) FROM food_entries WHERE date(created_at, 'localtime') = date('now', 'localtime')) AS entries_today,
                    (SELECT COUNT(DISTINCT user_id) FROM food_entries WHERE date(created_at, 'localtime') = date('now', 'localtime')) AS active_today,
                    (SELECT COUNT(*) FROM food_entries WHERE date(created_at, 'localtime') >= date('now', 'localtime', '-6 days')) AS entries_week,
                    (SELECT COUNT(DISTINCT user_id) FROM food_entries WHERE date(created_at, 'localtime') >= date('now', 'localtime', '-6 days')) AS active_week,
                    (SELECT COALESCE(SUM(calories), 0) FROM food_entries WHERE date(created_at, 'localtime') = date('now', 'localtime')) AS calories_today,
                    (SELECT COALESCE(SUM(calories), 0) FROM food_entries WHERE date(created_at, 'localtime') >= date('now', 'localtime', '-6 days')) AS calories_week
                """
            ).fetchone()
            return dict(row)

    def admin_period_stats(self, days: int | None = None) -> dict[str, int | float]:
        if days is None:
            date_filter = "1 = 1"
            user_date_filter = "1 = 1"
            event_date_filter = "1 = 1"
        elif days == 1:
            date_filter = "date(created_at, 'localtime') = date('now', 'localtime')"
            user_date_filter = "date(created_at, 'localtime') = date('now', 'localtime')"
            event_date_filter = "date(created_at, 'localtime') = date('now', 'localtime')"
        else:
            date_filter = f"date(created_at, 'localtime') >= date('now', 'localtime', '-{days - 1} days')"
            user_date_filter = f"date(created_at, 'localtime') >= date('now', 'localtime', '-{days - 1} days')"
            event_date_filter = f"date(created_at, 'localtime') >= date('now', 'localtime', '-{days - 1} days')"

        with self.connect() as conn:
            row = conn.execute(
                f"""
                SELECT
                    (SELECT COUNT(*) FROM users) AS users_total,
                    (SELECT COUNT(*) FROM users WHERE {user_date_filter}) AS users_new,
                    (SELECT COUNT(DISTINCT user_id) FROM user_events WHERE event_type = 'start' AND {event_date_filter}) AS users_started,
                    (SELECT COUNT(DISTINCT user_id) FROM user_events WHERE event_type = 'goal_set' AND {event_date_filter}) AS users_goal_set,
                    (SELECT COUNT(*) FROM users WHERE calorie_target IS NOT NULL) AS users_with_goal_total,
                    (SELECT COUNT(*) FROM users WHERE reminder_time IS NOT NULL) AS users_with_reminders_total,
                    (SELECT COUNT(*) FROM food_entries WHERE {date_filter}) AS food_entries,
                    (SELECT COUNT(*) FROM user_events WHERE event_type = 'photo_recognition' AND {event_date_filter}) AS photo_recognitions,
                    (SELECT COUNT(DISTINCT user_id) FROM user_events WHERE event_type = 'text_input' AND {event_date_filter}) AS users_wrote_text,
                    (SELECT COUNT(*) FROM food_entries WHERE source = 'text' AND {date_filter}) AS text_entries,
                    (SELECT COUNT(DISTINCT user_id) FROM food_entries WHERE {date_filter}) AS active_users,
                    (SELECT COALESCE(SUM(calories), 0) FROM food_entries WHERE {date_filter}) AS calories,
                    (
                        WITH active_days AS (
                            SELECT user_id, date(created_at, 'localtime') AS day FROM user_events
                            UNION
                            SELECT user_id, date(created_at, 'localtime') AS day FROM food_entries
                        )
                        SELECT COUNT(DISTINCT current_day.user_id)
                        FROM active_days current_day
                        JOIN active_days next_day
                          ON next_day.user_id = current_day.user_id
                         AND julianday(next_day.day) = julianday(current_day.day) + 1
                    ) AS users_two_day_streak
                """
            ).fetchone()
            return dict(row)

    def admin_today_food(self, limit: int = 10) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT u.telegram_id,
                       COUNT(f.id) AS entries,
                       SUM(f.calories) AS calories,
                       SUM(f.protein) AS protein
                FROM food_entries f
                JOIN users u ON u.id = f.user_id
                WHERE date(f.created_at, 'localtime') = date('now', 'localtime')
                GROUP BY f.user_id, u.telegram_id
                ORDER BY calories DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

    def admin_week_food(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT date(created_at, 'localtime') AS day,
                       COUNT(*) AS entries,
                       COUNT(DISTINCT user_id) AS users,
                       SUM(calories) AS calories
                FROM food_entries
                WHERE date(created_at, 'localtime') >= date('now', 'localtime', '-6 days')
                GROUP BY day
                ORDER BY day DESC
                """
            ).fetchall()

    def admin_popular_food(self, days: int | None = None, limit: int = 10) -> list[sqlite3.Row]:
        date_filter = ""
        params: list[Any] = []
        if days is not None:
            date_filter = "AND date(created_at, '+3 hours') >= date('now', '+3 hours', ?)"
            params.append(f"-{max(days - 1, 0)} days")
        params.append(limit)

        with self.connect() as conn:
            conn.create_function("normalize_food_title", 1, _normalize_food_title)
            return conn.execute(
                f"""
                SELECT MIN(TRIM(title)) AS title,
                       COUNT(*) AS entries,
                       COUNT(DISTINCT user_id) AS users
                FROM food_entries
                WHERE title IS NOT NULL
                  AND normalize_food_title(title) != ''
                  {date_filter}
                GROUP BY normalize_food_title(title)
                ORDER BY users DESC, entries DESC, title COLLATE NOCASE
                LIMIT ?
                """,
                params,
            ).fetchall()

    def admin_latest_users(self, limit: int = 10) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT telegram_id,
                       created_at,
                       calorie_target,
                       reminder_time,
                       (SELECT COUNT(*) FROM food_entries WHERE user_id = users.id) AS entries
                FROM users
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

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
            water_target=row["water_target"],
            goal_set_at=row["goal_set_at"],
            reminder_time=row["reminder_time"],
            reminder_last_sent_date=row["reminder_last_sent_date"],
            water_reminders_enabled=bool(row["water_reminders_enabled"]),
            water_reminder_skip_date=row["water_reminder_skip_date"],
            current_streak=row["current_streak"],
            best_streak=row["best_streak"],
            last_active_date=row["last_active_date"],
            activation_step=row["activation_step"],
            last_activation_message_at=row["last_activation_message_at"],
            activation_disabled=bool(row["activation_disabled"]),
            display_name=row["display_name"],
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
            water_ml=row["water_ml"],
            confidence=row["confidence"],
            source=row["source"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )
