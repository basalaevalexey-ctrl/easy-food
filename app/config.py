from dataclasses import dataclass
import json
import time
from pathlib import Path

from dotenv import load_dotenv
import os


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
DEFAULT_WEBAPP_URL = ""


@dataclass(frozen=True)
class Config:
    bot_token: str
    openai_api_key: str
    admin_ids: set[int]
    port: int
    webapp_url: str
    web_session_secret: str
    vkid_client_id: str
    vkid_client_secret: str
    vkid_redirect_uri: str
    public_dir: Path
    database_url: str
    database_path: Path
    legacy_database_paths: tuple[Path, ...]
    database_backup_paths: tuple[Path, ...]
    database_require_existing: bool
    database_min_users: int
    database_min_entries: int
    database_min_events: int
    openai_model: str
    openai_proxy_url: str
    auto_push_time: str
    timezone: str
    telegram_polling_enabled: bool
    background_jobs_enabled: bool
    database_smoke_test: bool
    instance_name: str
    admin_total_baseline: dict[str, int]
    admin_total_baseline_offset: dict[str, int]


def _parse_admin_ids(raw: str) -> set[int]:
    result: set[int] = set()
    for item in raw.replace(";", ",").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            result.add(int(item))
        except ValueError:
            continue
    return result


def _database_path() -> Path:
    raw_path = os.getenv("DATABASE_PATH", "data/calories.sqlite3").strip()
    if raw_path == "/data/calories.sqlite3" and Path("/app").exists():
        raw_path = "/app/data/calories.sqlite3"
    path = Path(raw_path)
    if not path.is_absolute():
        path = BASE_DIR / path
    return path


def _legacy_database_paths() -> tuple[Path, ...]:
    return (
        BASE_DIR / "calories.sqlite3",
        BASE_DIR / "data" / "calories.sqlite3",
        Path("/data/calories.sqlite3"),
    )


def _database_backup_paths(primary_path: Path) -> tuple[Path, ...]:
    configured_paths: list[Path] = []
    for item in os.getenv("DATABASE_BACKUP_PATHS", "").split(","):
        raw_path = item.strip()
        if not raw_path:
            continue
        path = Path(raw_path)
        configured_paths.append(path if path.is_absolute() else BASE_DIR / path)
    configured = tuple(configured_paths)
    candidates = (
        *configured,
        BASE_DIR / "data" / "calories.sqlite3",
        BASE_DIR / "calories.sqlite3",
        Path("/data/calories.sqlite3"),
    )
    return tuple(path for path in candidates if path != primary_path)


def _parse_int_dict(raw: str) -> dict[str, int]:
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    result: dict[str, int] = {}
    if not isinstance(data, dict):
        return result
    for key, value in data.items():
        try:
            result[str(key)] = int(value)
        except (TypeError, ValueError):
            continue
    return result


def _int_env(name: str, fallback: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return fallback
    try:
        return int(raw)
    except ValueError:
        return fallback


def _bool_env(name: str, fallback: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return fallback
    return raw in {"1", "true", "yes", "on"}


def _app_timezone() -> str:
    return os.getenv("APP_TIMEZONE", os.getenv("TZ", "Europe/Moscow")).strip() or "Europe/Moscow"


def _webapp_url() -> str:
    return os.getenv("WEBAPP_URL", DEFAULT_WEBAPP_URL).strip() or DEFAULT_WEBAPP_URL


def _apply_process_timezone(timezone: str) -> None:
    os.environ["TZ"] = timezone
    if hasattr(time, "tzset"):
        time.tzset()


def load_config() -> Config:
    database_path = _database_path()
    timezone = _app_timezone()
    _apply_process_timezone(timezone)
    return Config(
        bot_token=os.getenv("BOT_TOKEN", ""),
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        admin_ids=_parse_admin_ids(os.getenv("ADMIN_IDS", "")),
        port=_int_env("PORT", 3000),
        webapp_url=_webapp_url(),
        web_session_secret=os.getenv("WEB_SESSION_SECRET", "").strip(),
        vkid_client_id=os.getenv("VKID_CLIENT_ID", "").strip(),
        vkid_client_secret=os.getenv("VKID_CLIENT_SECRET", "").strip(),
        vkid_redirect_uri=os.getenv("VKID_REDIRECT_URI", "").strip(),
        public_dir=BASE_DIR / "public",
        database_url=os.getenv("DATABASE_URL", "").strip(),
        database_path=database_path,
        legacy_database_paths=_legacy_database_paths(),
        database_backup_paths=_database_backup_paths(database_path),
        database_require_existing=_bool_env("DATABASE_REQUIRE_EXISTING", False),
        database_min_users=_int_env("DATABASE_MIN_USERS", 0),
        database_min_entries=_int_env("DATABASE_MIN_ENTRIES", 0),
        database_min_events=_int_env("DATABASE_MIN_EVENTS", 0),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        openai_proxy_url=os.getenv("OPENAI_PROXY_URL", "").strip(),
        auto_push_time=os.getenv("AUTO_PUSH_TIME", "19:00"),
        timezone=timezone,
        telegram_polling_enabled=_bool_env("TELEGRAM_POLLING_ENABLED", True),
        background_jobs_enabled=_bool_env("BACKGROUND_JOBS_ENABLED", True),
        database_smoke_test=_bool_env("DATABASE_SMOKE_TEST", False),
        instance_name=os.getenv("INSTANCE_NAME", "local").strip() or "local",
        admin_total_baseline=_parse_int_dict(os.getenv("ADMIN_TOTAL_BASELINE", "")),
        admin_total_baseline_offset=_parse_int_dict(os.getenv("ADMIN_TOTAL_BASELINE_OFFSET", "")),
    )
