from dataclasses import dataclass
import json
import time
from pathlib import Path

from dotenv import load_dotenv
import os


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


@dataclass(frozen=True)
class Config:
    bot_token: str
    openai_api_key: str
    admin_ids: set[int]
    database_path: Path
    legacy_database_paths: tuple[Path, ...]
    database_backup_paths: tuple[Path, ...]
    openai_model: str
    auto_push_time: str
    timezone: str
    miniapp_url: str
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
    candidates = (
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


def _app_timezone() -> str:
    return os.getenv("APP_TIMEZONE", os.getenv("TZ", "Europe/Moscow")).strip() or "Europe/Moscow"


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
        database_path=database_path,
        legacy_database_paths=_legacy_database_paths(),
        database_backup_paths=_database_backup_paths(database_path),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        auto_push_time=os.getenv("AUTO_PUSH_TIME", "19:00"),
        timezone=timezone,
        miniapp_url=os.getenv("MINIAPP_URL", "").strip(),
        admin_total_baseline=_parse_int_dict(os.getenv("ADMIN_TOTAL_BASELINE", "")),
        admin_total_baseline_offset=_parse_int_dict(os.getenv("ADMIN_TOTAL_BASELINE_OFFSET", "")),
    )
