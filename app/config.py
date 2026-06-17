from dataclasses import dataclass
import json
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
    openai_model: str
    auto_push_time: str
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
    path = Path(raw_path)
    if not path.is_absolute():
        path = BASE_DIR / path
    return path


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


def load_config() -> Config:
    return Config(
        bot_token=os.getenv("BOT_TOKEN", ""),
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        admin_ids=_parse_admin_ids(os.getenv("ADMIN_IDS", "")),
        database_path=_database_path(),
        legacy_database_paths=(BASE_DIR / "calories.sqlite3",),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        auto_push_time=os.getenv("AUTO_PUSH_TIME", "19:00"),
        admin_total_baseline=_parse_int_dict(os.getenv("ADMIN_TOTAL_BASELINE", "")),
        admin_total_baseline_offset=_parse_int_dict(os.getenv("ADMIN_TOTAL_BASELINE_OFFSET", "")),
    )
