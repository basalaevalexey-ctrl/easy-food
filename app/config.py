from dataclasses import dataclass
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
    openai_model: str


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


def load_config() -> Config:
    return Config(
        bot_token=os.getenv("BOT_TOKEN", ""),
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        admin_ids=_parse_admin_ids(os.getenv("ADMIN_IDS", "")),
        database_path=_database_path(),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
    )
