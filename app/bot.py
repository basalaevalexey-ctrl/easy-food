import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import logging
import mimetypes
import re
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse, urlunparse

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonDefault,
    MenuButtonWebApp,
    Message,
    ReplyKeyboardRemove,
    WebAppInfo,
)

from app.admin_stats import (
    AdminStatsService,
    format_30d_stats,
    format_7d_stats,
    format_channel_stats,
    format_daily_stats,
    format_funnel_stats,
    format_reminders_stats,
    format_retention_stats,
    format_revenue_stats,
    format_today_stats,
    format_total_stats,
)
from app.achievements import ACHIEVEMENTS
from app.calorie_calculator import calculate_targets
from app.config import load_config
from app.database import Database
from app.keyboards import (
    admin_food_keyboard,
    admin_keyboard,
    activity_keyboard,
    activation_keyboard,
    food_actions,
    goal_nudge_keyboard,
    goal_keyboard,
    instruction_keyboard,
    main_menu,
    reminder_keyboard,
    reminder_time_keyboard,
    setup_goal_intro_keyboard,
    sex_keyboard,
    streak_rescue_keyboard,
)
from app.models import FoodEntry, User
from app.openai_client import FoodRecognitionClient, NotFoodError, OpenAIRecognitionError


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)
MOSCOW_TZ = timezone(timedelta(hours=3))

router = Router()
config = load_config()
db = Database(
    config.database_path,
    legacy_paths=config.legacy_database_paths,
    backup_paths=config.database_backup_paths,
)
admin_stats_service = AdminStatsService(db)
food_ai = FoodRecognitionClient(config.openai_api_key, config.openai_model)
WEBAPP_BUILD = "nyam-94"
WEBAPP_ENTRY_PATH = "/nyammetr-live.html"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
BOT_USERNAME = ""
WATER_REMINDER_SLOTS = {
    "11:30": 0.25,
    "15:30": 0.50,
    "19:30": 0.75,
}
STREAK_RESCUE_TIME = "21:00"
WEEKLY_REPORT_TIME = "10:00"
DAILY_REMINDER_MESSAGES = (
    (
        "Нямметр заглянул на минутку 🍽\n\n"
        "Если уже что-нибудь ел, запиши это фото или текстом. "
        "Даже одной записи достаточно, чтобы увидеть примерный баланс дня."
    ),
    (
        "Интересно, сколько уже набралось за сегодня? 👀\n\n"
        "Добавь еду в Нямметр, и сразу увидишь калории, БЖУ и сколько осталось до дневной цели."
    ),
    (
        "Учёт еды без весов и таблиц 💚\n\n"
        "Сфотографируй блюдо или напиши, что ел. Нямметр сделает остальное."
    ),
    (
        "Пора ненадолго заглянуть в Нямметр 🍽\n\n"
        "В миниаппе можно быстро добавить еду, проверить дневной прогресс и отметить воду."
    ),
    (
        "Пока ещё помнишь, что было на тарелке 👀\n\n"
        "Запиши приём пищи фото или текстом, чтобы вечером не пришлось вспоминать весь день."
    ),
    (
        "Продолжим Ням-стрик? 🔥\n\n"
        "Добавь хотя бы одну еду за сегодня. Фото, текст или быстрый ввод через миниапп — подойдёт любой вариант."
    ),
    (
        "Небольшой ням-чек 🍽\n\n"
        "Что сегодня уже успело попасть в меню? Добавь еду, и посмотрим текущий баланс дня."
    ),
)
DAILY_REMINDER_ORDER = (0, 3, 6, 2, 5, 1, 4)


def daily_reminder_text(telegram_id: int, reminder_date: date, mission_status: dict) -> str:
    position = (reminder_date.toordinal() + telegram_id) % len(DAILY_REMINDER_ORDER)
    message_index = DAILY_REMINDER_ORDER[position]
    return f"{DAILY_REMINDER_MESSAGES[message_index]}\n\n{format_daily_mission(mission_status)}"


def webapp_url_with_build() -> str:
    if not config.webapp_url:
        return ""
    parsed = urlparse(config.webapp_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["v"] = WEBAPP_BUILD
    path = parsed.path if parsed.path and parsed.path != "/" else WEBAPP_ENTRY_PATH
    return urlunparse(parsed._replace(path=path, query=urlencode(query)))


def webapp_url_for_user(user_id: int | None) -> str:
    if not user_id or not config.webapp_url:
        return ""
    return webapp_url_with_build()


def referral_links(telegram_id: int) -> dict[str, str]:
    if not BOT_USERNAME:
        return {"invite_url": "", "share_url": ""}
    invite_url = f"https://t.me/{BOT_USERNAME}?start=ref_{telegram_id}"
    share_query = urlencode(
        {
            "url": invite_url,
            "text": "Попробуй Нямметр 🍽 Он считает калории и БЖУ по фото или описанию еды.",
        }
    )
    return {
        "invite_url": invite_url,
        "share_url": f"https://t.me/share/url?{share_query}",
    }


def sanitize_miniapp_html(html: str) -> str:
    if "<base " not in html:
        html = html.replace("<head>", '<head>\n    <base href="/" />', 1)
    html = re.sub(
        r"<small>mini app(?:\s*[·В]\s*nyam-\d+)?</small>",
        "<small>mini app</small>",
        html,
    )
    html = re.sub(r"styles\.css\?v=nyam-\d+", f"styles.css?v={WEBAPP_BUILD}", html)
    html = re.sub(r"<small data-build-label>nyam-\d+</small>", "<small data-build-label></small>", html)
    html = re.sub(r"<span data-calories>\d+</span>", "<span data-calories>0</span>", html)
    html = re.sub(r"<em data-calorie-goal-label>.*?</em>", "<em data-calorie-goal-label></em>", html)
    html = re.sub(r'style="width:\s*\d+%"', 'style="width: 0%"', html)
    html = re.sub(r'<span class="macro-current">\d+</span>', '<span class="macro-current">0</span>', html)
    html = re.sub(r"<small>/\d+</small>", "<small></small>", html)
    html = re.sub(r'<small class="macro-percent">\d+%</small>', '<small class="macro-percent"></small>', html)
    html = re.sub(r"<strong data-goal-calories>.*?</strong>", '<strong data-goal-calories>0 ккал</strong>', html)
    html = re.sub(r"<strong data-goal-protein>.*?</strong>", '<strong data-goal-protein>0 г</strong>', html)
    html = re.sub(r"<small>Собрано \d+ из 12</small>", "<small>Собрано 0 из 12</small>", html)
    html = re.sub(r"<strong>\d+ дней</strong>", "<strong>0 дней</strong>", html)

    meal_start = html.find('<div class="meal-list">')
    meal_end = html.find('<aside class="tip-card wide">', meal_start)
    if meal_start != -1 and meal_end != -1:
        meal_list = """<div class="meal-list">
          <article class="meal-card open">
            <button type="button" class="meal-toggle" aria-expanded="true">
              <span class="meal-emoji"><img src="./assets/meal-snack.png" alt="" /></span>
              <span><b>Загружаю данные</b><small>Синхронизируюсь с дневником Нямметра</small></span>
              <strong>0 ккал</strong>
            </button>
          </article>
        </div>

        """
        html = html[:meal_start] + meal_list + html[meal_end:]
    return html


def miniapp_shell_html() -> str:
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta http-equiv="Cache-Control" content="no-store, no-cache, must-revalidate, max-age=0" />
  <meta http-equiv="Pragma" content="no-cache" />
  <meta http-equiv="Expires" content="0" />
  <title>Нямметр</title>
  <script src="https://telegram.org/js/telegram-web-app.js"></script>
  <style>
    :root {{ color-scheme: light; --green:#58bd35; --dark:#15212b; --muted:#6f766f; --paper:#fffaf0; --line:#eadfcd; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:Arial, sans-serif; background:#f6f1e7; color:#111; }}
    main {{ width:min(420px, 100%); min-height:100vh; margin:0 auto; padding:18px 16px 90px; background:#fffaf3; }}
    header {{ display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:18px; }}
    header b {{ display:block; font-size:20px; }}
    header small {{ color:var(--muted); }}
    section {{ display:none; }}
    section.active {{ display:block; }}
    .card {{ background:#fff; border:1px solid var(--line); border-radius:18px; padding:16px; margin:12px 0; box-shadow:0 8px 24px rgba(39,32,20,.08); }}
    .hero {{ text-align:center; }}
    .hero h1 {{ margin:0; font-size:24px; }}
    .summary strong {{ font-size:32px; }}
    .summary em {{ font-style:normal; font-size:18px; }}
    .bar {{ height:12px; background:#ece5d7; border-radius:999px; overflow:hidden; margin:12px 0; }}
    .bar span {{ display:block; height:100%; width:0; background:var(--green); }}
    .macros {{ display:grid; grid-template-columns:repeat(3,1fr); gap:8px; }}
    .macros span {{ background:var(--paper); border-radius:12px; padding:10px; text-align:center; font-weight:700; }}
    .macros small {{ display:block; color:var(--muted); font-weight:400; }}
    .entry {{ display:grid; gap:6px; border-bottom:1px solid var(--line); padding:12px 0; }}
    .entry:last-child {{ border-bottom:0; }}
    .entry b {{ font-size:16px; }}
    .entry small {{ color:var(--muted); }}
    label {{ display:grid; gap:6px; margin:12px 0; font-weight:700; }}
    input, select {{ width:100%; border:1px solid #dbcdb8; border-radius:12px; padding:12px; font:inherit; background:#fffaf0; }}
    button {{ border:0; border-radius:14px; padding:12px 14px; font:inherit; font-weight:700; background:var(--green); color:#fff; }}
    .ghost {{ background:#efe8d9; color:#111; }}
    nav {{ position:fixed; left:50%; bottom:12px; transform:translateX(-50%); width:min(390px, calc(100% - 24px)); display:grid; grid-template-columns:repeat(3,1fr); gap:8px; background:#fff; border:1px solid var(--line); border-radius:18px; padding:8px; }}
    nav button {{ background:#f5efdf; color:#777; padding:10px 6px; }}
    nav button.active {{ color:#208a19; background:#eaf7df; }}
    .toast {{ position:fixed; left:50%; bottom:88px; transform:translate(-50%, 140px); max-width:min(340px, calc(100% - 32px)); padding:12px 16px; border-radius:999px; background:rgba(20,20,20,.9); color:#fff; text-align:center; transition:.2s; }}
    .toast.show {{ transform:translate(-50%, 0); }}
  </style>
</head>
<body>
<main>
  <header>
    <span>Закрыть</span>
    <div><b>Нямметр</b><small>mini app</small></div>
    <button class="ghost" type="button">•••</button>
  </header>

  <section class="active" data-screen="home">
    <div class="card hero"><h1>Привет, я Нямметр 🍽</h1><small>Данные подтягиваются из твоего дневника</small></div>
    <div class="card summary">
      <small>Калории сегодня</small>
      <div><strong data-calories>0</strong> <em data-target></em></div>
      <div class="bar"><span data-calorie-bar></span></div>
      <div class="macros">
        <span>Б <b data-protein>0</b><small data-protein-target></small></span>
        <span>Ж <b data-fat>0</b><small>г</small></span>
        <span>У <b data-carbs>0</b><small>г</small></span>
      </div>
    </div>
  </section>

  <section data-screen="diary">
    <h2>Дневник</h2>
    <div class="card" data-entries><p>Загружаю данные...</p></div>
  </section>

  <section data-screen="profile">
    <h2 data-name>Профиль</h2>
    <div class="card">
      <label>Вес, кг<input type="number" min="30" max="300" data-weight /></label>
      <label>Рост, см<input type="number" min="100" max="230" data-height /></label>
      <label>Активность<select data-activity><option value="low">Низкая</option><option value="medium">Средняя</option><option value="high">Высокая</option></select></label>
      <button type="button" data-save>Сохранить профиль</button>
    </div>
    <div class="card">
      <b>Цели</b>
      <p>Калории: <span data-profile-calories>0</span> ккал</p>
      <p>Белок: <span data-profile-protein>0</span> г</p>
    </div>
  </section>
</main>
<nav>
  <button class="active" type="button" data-tab="home">Главная</button>
  <button type="button" data-tab="diary">Дневник</button>
  <button type="button" data-tab="profile">Профиль</button>
</nav>
<div class="toast" data-toast></div>
<script>
const tg = window.Telegram?.WebApp;
tg?.ready?.();
tg?.expand?.();

const toast = document.querySelector("[data-toast]");
const state = {{ data: null }};

function showToast(message) {{
  toast.textContent = message;
  toast.classList.add("show");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.remove("show"), 2400);
}}

function initData() {{
  return window.Telegram?.WebApp?.initData || "";
}}

async function request(path, options = {{}}) {{
  const headers = {{ ...(options.headers || {{}}) }};
  let requestPath = path;
  const auth = initData();
  if (auth) {{
    headers.Authorization = `TMA ${{auth}}`;
    const url = new URL(path, window.location.href);
    url.searchParams.set("initData", auth);
    requestPath = `${{url.pathname}}${{url.search}}`;
  }}
  const response = await fetch(requestPath, {{ ...options, headers }});
  if (!response.ok) {{
    let error = `request_failed_${{response.status}}`;
    try {{ error = (await response.json()).error || error; }} catch {{}}
    throw new Error(error);
  }}
  return response.json();
}}

function round(value) {{
  return Math.round(Number(value) || 0);
}}

function render(data) {{
  state.data = data;
  const totals = data.today?.totals || {{}};
  const entries = data.today?.entries || [];
  const target = round(data.targets?.calories);
  const proteinTarget = round(data.targets?.protein);
  const calories = round(totals.calories);
  document.querySelector("[data-calories]").textContent = calories;
  document.querySelector("[data-target]").textContent = target ? `/ ${{target}} ккал` : "";
  document.querySelector("[data-calorie-bar]").style.width = target ? `${{Math.min(100, Math.round(calories / target * 100))}}%` : "0%";
  document.querySelector("[data-protein]").textContent = round(totals.protein);
  document.querySelector("[data-fat]").textContent = round(totals.fat);
  document.querySelector("[data-carbs]").textContent = round(totals.carbs);
  document.querySelector("[data-protein-target]").textContent = proteinTarget ? `/ ${{proteinTarget}} г` : "г";
  document.querySelector("[data-profile-calories]").textContent = target;
  document.querySelector("[data-profile-protein]").textContent = proteinTarget;

  const user = data.user || {{}};
  document.querySelector("[data-name]").textContent = user.display_name || "Профиль";
  document.querySelector("[data-weight]").value = user.weight || "";
  document.querySelector("[data-height]").value = user.height || "";
  document.querySelector("[data-activity]").value = user.activity || "medium";

  const box = document.querySelector("[data-entries]");
  if (!entries.length) {{
    box.innerHTML = "";
    return;
  }}
  box.innerHTML = entries.map((entry) => `
    <div class="entry">
      <b>${{escapeHtml(entry.title || "Еда")}}</b>
      <small>${{escapeHtml(entry.description || "")}}</small>
      <strong>${{round(entry.calories)}} ккал</strong>
      <small>Б ${{round(entry.protein)}} г · Ж ${{round(entry.fat)}} г · У ${{round(entry.carbs)}} г</small>
    </div>
  `).join("");
}}

function escapeHtml(value) {{
  return String(value).replace(/[&<>"']/g, (char) => ({{ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#039;" }}[char]));
}}

async function load() {{
  if (!initData()) {{
    document.querySelector("[data-entries]").innerHTML = "<p>Открой миниапп через кнопку в Telegram, чтобы увидеть свои данные.</p>";
    return;
  }}
  try {{
    render(await request("/api/miniapp/me"));
  }} catch (error) {{
    showToast(`Не смог загрузить данные: ${{error.message}}`);
  }}
}}

async function saveProfile() {{
  try {{
    const payload = {{
      weight: Number(document.querySelector("[data-weight]").value),
      height: Number(document.querySelector("[data-height]").value),
      activity: document.querySelector("[data-activity]").value,
      goal: state.data?.user?.goal || "support",
      sex: state.data?.user?.sex || "male",
    }};
    render(await request("/api/miniapp/profile", {{
      method: "POST",
      headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify(payload),
    }}));
    showToast("Профиль сохранен");
  }} catch (error) {{
    showToast(`Не смог сохранить профиль: ${{error.message}}`);
  }}
}}

document.querySelector("[data-save]").addEventListener("click", saveProfile);
document.querySelectorAll("[data-tab]").forEach((tab) => {{
  tab.addEventListener("click", () => {{
    document.querySelectorAll("[data-tab]").forEach((item) => item.classList.toggle("active", item === tab));
    document.querySelectorAll("[data-screen]").forEach((screen) => screen.classList.toggle("active", screen.dataset.screen === tab.dataset.tab));
  }});
}});
load();
</script>
</body>
</html>"""


def miniapp_shell_html() -> str:
    html_path = config.public_dir / "nyammetr-live.html"
    try:
        html = html_path.read_text(encoding="utf-8")
    except OSError:
        logger.exception("Failed to read mini app html")
        return "<!doctype html><meta charset='utf-8'><title>Нямметр</title><p>Не смог загрузить миниапп.</p>"
    return sanitize_miniapp_html(html).replace("__WEBAPP_BUILD__", WEBAPP_BUILD)


class SetupGoal(StatesGroup):
    age = State()
    height = State()
    weight = State()


class PortionCorrection(StatesGroup):
    grams = State()
    dish = State()


class ReminderSetup(StatesGroup):
    custom_time = State()


GOAL_LABELS = {
    "lose": "похудеть",
    "maintain": "поддерживать",
    "gain": "набрать",
}

ACTIVITY_LABELS = {
    "low": "низкая",
    "medium": "средняя",
    "high": "высокая",
}

DEFAULT_ADMIN_TOTAL_BASELINE = {
    "users_started": 21,
    "users_new": 24,
    "users_goal_set": 8,
    "photo_recognitions": 25,
    "users_wrote_text": 8,
    "food_entries": 37,
    "text_entries": 13,
    "active_users": 15,
    "calories": 13486,
    "users_total": 24,
    "users_with_goal_total": 8,
    "users_with_reminders_total": 0,
    "users_two_day_streak": 0,
}

DEFAULT_ADMIN_TOTAL_BASELINE_OFFSET = {
    "users_started": 6,
    "users_new": 8,
    "users_goal_set": 3,
    "photo_recognitions": 8,
    "users_wrote_text": 2,
    "food_entries": 10,
    "text_entries": 2,
    "active_users": 4,
    "calories": 5839,
    "users_total": 8,
    "users_with_goal_total": 3,
    "users_with_reminders_total": 0,
    "users_two_day_streak": 0,
}

ACTIVATION_TEXTS = {
    1: (
        "Нямметр на связи 🍽\n\n"
        "Чтобы попробовать бота, просто отправь фото еды или напиши, что ел сегодня.\n\n"
        "Например:\n"
        "“омлет, кофе и банан”\n\n"
        "Я примерно посчитаю калории и БЖУ."
    ),
    2: (
        "Можно начать без настроек 👋\n\n"
        "Просто скинь фото еды — Нямметр примерно посчитает калории и БЖУ.\n\n"
        "Цель, вес и норму калорий можно настроить потом в приложении."
    ),
    3: (
        "Кажется, ты пока не попробовал Нямметр.\n\n"
        "Можно начать с самого простого: отправь любое фото еды, а я покажу, как работает расчет.\n\n"
        "Если неактуально — просто не буду больше напоминать."
    ),
}

LIFECYCLE_PUSH_TEXTS = {
    "started_no_goal": {
        1: (
            "Сделай Нямметр персональным 🎯\n\n"
            "Укажи свои параметры, и Нямметр рассчитает специально для тебя:\n\n"
            "• индивидуальную дневную норму калорий;\n"
            "• цель по белку;\n"
            "• норму воды;\n"
            "• сколько калорий и БЖУ осталось на сегодня.\n\n"
            "Так ты будешь видеть не просто цифры, а свой личный прогресс в течение дня."
        ),
        2: (
            "С целью прогресс становится понятнее 🍽\n\n"
            "Нямметр покажет, насколько ты приблизился к своей норме, и откроет персональные миссии:\n\n"
            "• попасть в коридор калорий;\n"
            "• добрать белок;\n"
            "• завершить «День в цель».\n\n"
            "Без таблиц и ручных расчетов."
        ),
        3: (
            "Еду можно записывать и без цели, но с ней Нямметр работает заметно полезнее 💚\n\n"
            "Ты увидишь личную норму, остаток калорий и прогресс за день. "
            "Цель можно изменить в любой момент в профиле."
        ),
    },
    "one_food_no_return": {
        1: (
            "Ты уже сделал первый шаг 🍽\n\n"
            "Привычка формируется легче, когда действие маленькое. Просто запиши одну еду сегодня — фото или текстом."
        ),
        2: (
            "Нямметр помнит твой первый лог 💚\n\n"
            "Не нужно вести дневник идеально. Даже одна запись в день помогает лучше понимать, что ты ешь — особенно когда прогресс видно в приложении."
        ),
        3: (
            "Вернуться можно без давления 👋\n\n"
            "Начни с самого простого: открой Нямметр или отправь фото еды прямо сюда. Я примерно посчитаю калории и БЖУ."
        ),
    },
    "goal_no_food": {
        1: (
            "Цель уже настроена 🎯\n\n"
            "Остался маленький тест: добавь одну еду, и в приложении сразу будет видно калории, БЖУ и остаток на день."
        ),
        2: (
            "Можно начать без весов и таблиц 🍽\n\n"
            "Просто отправь фото блюда или напиши, что съел. Я разложу это на калории, белки, жиры и углеводы."
        ),
        3: (
            "Твоя дневная норма уже ждет тебя 💚\n\n"
            "Одна запись еды — и в приложении станет видно, сколько осталось на сегодня."
        ),
    },
}


def is_activation_window(now: datetime) -> bool:
    current = now.time()
    return datetime.strptime("09:00", "%H:%M").time() <= current <= datetime.strptime("23:30", "%H:%M").time()


def round_num(value: float) -> int:
    return round(float(value))


def today_totals(entries: list[FoodEntry]) -> dict[str, float]:
    return {
        "calories": sum(entry.calories for entry in entries),
        "protein": sum(entry.protein for entry in entries),
        "fat": sum(entry.fat for entry in entries),
        "carbs": sum(entry.carbs for entry in entries),
    }


def _json_safe(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def parse_telegram_init_data(init_data: str) -> dict | None:
    if not init_data:
        return None

    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None

    check_string = "\n".join(f"{key}={value}" for key, value in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", config.bot_token.encode("utf-8"), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash):
        return None

    try:
        user_data = json.loads(pairs.get("user", "{}"))
    except json.JSONDecodeError:
        return None

    if not user_data.get("id"):
        return None

    return user_data


def _valid_miniapp_day(value: str | None) -> str | None:
    if not value or not DATE_RE.fullmatch(value):
        return None
    return value


def build_miniapp_period_report(telegram_user: dict, period_start: str | None, period_end: str | None) -> dict:
    if not _valid_miniapp_day(period_start) or not _valid_miniapp_day(period_end):
        raise ValueError("invalid_period")

    start = date.fromisoformat(period_start)
    end = date.fromisoformat(period_end)
    period_days = (end - start).days + 1
    if period_days < 2 or period_days > 7:
        raise ValueError("period_out_of_range")
    if end > datetime.now(MOSCOW_TZ).date():
        raise ValueError("future_period")

    telegram_id = int(telegram_user["id"])
    db.get_or_create_user(telegram_id)
    summary = db.get_weekly_report_summary(telegram_id, period_start, period_end)
    db.record_user_event(telegram_id, "miniapp_period_report_opened")
    return {
        "period": {
            "start": period_start,
            "end": period_end,
            "days": period_days,
        },
        "summary": summary,
    }


def build_miniapp_payload(telegram_user: dict, selected_day: str | None = None) -> dict:
    telegram_id = int(telegram_user["id"])
    user = db.get_or_create_user(telegram_id)
    selected_day = _valid_miniapp_day(selected_day)
    entries = db.get_entries_for_day(telegram_id, selected_day) if selected_day else db.get_today_entries(telegram_id)
    totals = today_totals(entries)
    progress = db.get_user_progress_stats(telegram_id)
    db.unlock_available_achievements(telegram_id)
    achievements = db.get_user_achievements(telegram_id)
    mission_status = db.get_daily_mission_status(telegram_id)
    mission = mission_status["mission"]
    active_dates = db.get_food_entry_days(telegram_id)
    freeze_status = db.get_streak_freeze_status(telegram_id)
    frozen_dates = db.get_streak_freeze_days(telegram_id)
    referral_progress = db.get_referral_progress(telegram_id)
    water = db.get_water_summary(telegram_id, selected_day)

    return {
        "user": {
            "telegram_id": telegram_id,
            "display_name": telegram_user.get("first_name") or telegram_user.get("username") or "Алексей",
            "sex": user.sex,
            "age": user.age,
            "height": user.height,
            "weight": user.weight,
            "goal": {"maintain": "support"}.get(user.goal, user.goal),
            "activity": user.activity,
        },
        "reminder": {
            "enabled": user.reminder_time is not None,
            "time": user.reminder_time or "09:00",
            "timezone": "МСК",
        },
        "water_reminder": {
            "enabled": user.water_reminders_enabled,
            "timezone": "МСК",
            "schedule": ["11:30", "15:30", "19:30"],
            "max_per_day": 2,
        },
        "targets": {
            "calories": user.calorie_target,
            "protein": user.protein_target,
            "water_ml": water["target_ml"],
        },
        "water": water,
        "today": {
            "date": selected_day,
            "totals": {key: round_num(value) for key, value in totals.items()},
            "entries": [
                {
                    "id": entry.id,
                    "title": entry.title,
                    "description": entry.description,
                    "calories": round_num(entry.calories),
                    "protein": round_num(entry.protein),
                    "fat": round_num(entry.fat),
                    "carbs": round_num(entry.carbs),
                    "water_ml": round_num(entry.water_ml),
                    "source": entry.source,
                    "created_at": _json_safe(entry.created_at),
                }
                for entry in entries
            ],
        },
        "food_suggestions": db.get_popular_foods(telegram_id, limit=3),
        "calendar": {
            "active_dates": active_dates,
            "frozen_dates": frozen_dates,
        },
        "progress": progress,
        "streak_freeze": freeze_status,
        "mission": {
            "key": mission.key,
            "is_completed": mission_status["is_completed"],
            "progress_text": mission_status["progress_text"],
            "mission_date": mission_status["mission_date"],
        },
        "achievements": [
            {
                "key": row["achievement_key"],
                "unlocked_at": _json_safe(row["unlocked_at"]),
            }
            for row in achievements
        ],
        "referral": {
            **referral_progress,
            **referral_links(telegram_id),
            "target": 10,
        },
        "debug": {
            "build": WEBAPP_BUILD,
            "is_admin": telegram_id in config.admin_ids,
            "telegram_id": telegram_id,
            "db_path": str(config.database_path),
            "today_entries": len(entries),
            "webapp_url": webapp_url_with_build(),
        },
    }


def update_miniapp_profile(telegram_user: dict, payload: dict) -> dict:
    telegram_id = int(telegram_user["id"])
    user = db.get_or_create_user(telegram_id)

    sex = user.sex or str(payload.get("sex") or "male")
    age = int(user.age or payload.get("age") or 30)
    height = int(payload.get("height") or user.height or 175)
    weight = float(payload.get("weight") or user.weight or 75)
    goal = str(payload.get("goal") or user.goal or "maintain")
    activity = str(payload.get("activity") or user.activity or "medium")

    if goal == "support":
        goal = "maintain"
    if sex not in {"male", "female"}:
        sex = "male"
    if goal not in {"lose", "maintain", "gain"}:
        goal = "maintain"
    if activity not in {"low", "medium", "high"}:
        activity = "medium"
    if not 10 <= age <= 100 or not 100 <= height <= 230 or not 30 <= weight <= 300:
        raise ValueError("profile_out_of_range")

    calorie_target, protein_target = calculate_targets(
        sex=sex,
        age=age,
        height=height,
        weight=weight,
        goal=goal,
        activity=activity,
    )
    db.update_user_goal(
        telegram_id,
        {
            "sex": sex,
            "age": age,
            "height": height,
            "weight": weight,
            "goal": goal,
            "activity": activity,
            "calorie_target": calorie_target,
            "protein_target": protein_target,
        },
    )
    db.activate_referral(telegram_id)
    db.record_user_event(telegram_id, "miniapp_profile_updated")
    return build_miniapp_payload(telegram_user)


def update_miniapp_reminder(telegram_user: dict, payload: dict) -> dict:
    telegram_id = int(telegram_user["id"])
    enabled = bool(payload.get("enabled"))
    reminder_time = normalize_reminder_time(str(payload.get("time") or "09:00")) or "09:00"
    db.set_reminder_time(telegram_id, reminder_time if enabled else None)
    db.record_user_event(telegram_id, "miniapp_reminder_updated" if enabled else "miniapp_reminder_disabled")
    return build_miniapp_payload(telegram_user)


def update_miniapp_water(telegram_user: dict, payload: dict) -> dict:
    telegram_id = int(telegram_user["id"])
    action = str(payload.get("action") or "add")
    if action == "remove":
        db.remove_last_water_entry(telegram_id)
    elif action == "add":
        amount_ml = int(payload.get("amount_ml") or 200)
        db.add_water_entry(telegram_id, amount_ml)
    else:
        raise ValueError("invalid_water_action")
    return build_miniapp_payload(telegram_user)


def update_miniapp_water_reminder(telegram_user: dict, payload: dict) -> dict:
    telegram_id = int(telegram_user["id"])
    enabled = bool(payload.get("enabled"))
    db.set_water_reminders_enabled(telegram_id, enabled)
    db.record_user_event(
        telegram_id,
        "miniapp_water_reminder_enabled" if enabled else "miniapp_water_reminder_disabled",
    )
    return build_miniapp_payload(telegram_user)


def freeze_miniapp_streak(telegram_user: dict) -> dict:
    telegram_id = int(telegram_user["id"])
    result = db.freeze_streak(telegram_id)
    if result["status"] not in {"frozen", "already_frozen"}:
        raise ValueError(str(result["status"]))
    db.record_user_event(telegram_id, "miniapp_streak_frozen")
    return {"result": result, "state": build_miniapp_payload(telegram_user)}


async def add_miniapp_food_text(telegram_user: dict, text: str) -> dict:
    telegram_id = int(telegram_user["id"])
    estimate = await food_ai.estimate_text(text)
    entry = db.add_food_entry(telegram_id, estimate, source="text")
    db.record_useful_action(telegram_id, "food_text_added")
    db.activate_referral(telegram_id)
    db.record_user_event(telegram_id, "miniapp_food_text")
    db.unlock_available_achievements(telegram_id)
    db.complete_daily_mission_if_ready(telegram_id)
    return {"entry": entry, "state": build_miniapp_payload(telegram_user)}


async def add_miniapp_food_photo(telegram_user: dict, image_bytes: bytes, mime_type: str) -> dict:
    telegram_id = int(telegram_user["id"])
    estimate = await food_ai.estimate_image(image_bytes, mime_type=mime_type)
    entry = db.add_food_entry(telegram_id, estimate, source="photo")
    db.record_useful_action(telegram_id, "food_photo_added")
    db.activate_referral(telegram_id)
    db.record_user_event(telegram_id, "miniapp_food_photo")
    db.unlock_available_achievements(telegram_id)
    db.complete_daily_mission_if_ready(telegram_id)
    return {"entry": entry, "state": build_miniapp_payload(telegram_user)}


class MiniAppApiHandler(BaseHTTPRequestHandler):
    server_version = "NyammetrMiniApi/1.0"

    def log_message(self, format: str, *args) -> None:
        logger.debug("Mini app API: " + format, *args)

    def _send_headers(
        self,
        status: int,
        content_type: str = "application/json",
        cache_control: str = "no-store, no-cache, must-revalidate, max-age=0",
    ) -> None:
        origin = self.headers.get("Origin") or "*"
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Cache-Control", cache_control)
        if "no-store" in cache_control:
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        self.end_headers()

    def _send_json(self, status: int, payload: dict) -> None:
        self._send_headers(status)
        self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def _send_static(self, requested_path: str) -> None:
        relative_path = "index.html" if requested_path in {"", "/", WEBAPP_ENTRY_PATH} else requested_path.lstrip("/")
        try:
            file_path = (config.public_dir / relative_path).resolve()
            public_dir = config.public_dir.resolve()
            if public_dir not in file_path.parents and file_path != public_dir:
                self._send_json(404, {"error": "not_found"})
                return
            if not file_path.is_file():
                self._send_json(404, {"error": "not_found"})
                return
            content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
            if content_type.startswith("text/"):
                content_type += "; charset=utf-8"
            cache_control = (
                "no-store, no-cache, must-revalidate, max-age=0"
                if file_path.suffix.lower() in {".html", ".htm"}
                else "public, max-age=86400"
            )
            self._send_headers(200, content_type, cache_control=cache_control)
            if relative_path == "index.html":
                html = file_path.read_text(encoding="utf-8", errors="replace")
                self.wfile.write(sanitize_miniapp_html(html).encode("utf-8"))
                return
            self.wfile.write(file_path.read_bytes())
        except OSError:
            logger.exception("Failed to serve mini app file: %s", requested_path)
            self._send_json(500, {"error": "static_file_error"})

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > 8 * 1024 * 1024:
            raise ValueError("request_too_large")
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def _telegram_user(self) -> dict | None:
        authorization = self.headers.get("Authorization", "")
        init_data = ""
        if authorization.lower().startswith("tma "):
            init_data = authorization[4:].strip()
        if not init_data:
            query = dict(parse_qsl(urlparse(self.path).query, keep_blank_values=True))
            init_data = query.get("initData", "")
        return parse_telegram_init_data(init_data)

    def _entry_payload(self, entry: FoodEntry) -> dict:
        return {
            "id": entry.id,
            "title": entry.title,
            "description": entry.description,
            "calories": round_num(entry.calories),
            "protein": round_num(entry.protein),
            "fat": round_num(entry.fat),
            "carbs": round_num(entry.carbs),
            "water_ml": round_num(entry.water_ml),
            "source": entry.source,
            "created_at": _json_safe(entry.created_at),
        }

    def do_OPTIONS(self) -> None:
        self._send_headers(204)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send_headers(200, "text/plain; charset=utf-8")
            self.wfile.write(b"ok")
            return
        if parsed.path == WEBAPP_ENTRY_PATH:
            self._send_headers(200, "text/html; charset=utf-8")
            self.wfile.write(miniapp_shell_html().encode("utf-8"))
            return

        if not parsed.path.startswith("/api/"):
            self._send_static(parsed.path)
            return

        if parsed.path not in {"/api/miniapp/me", "/api/miniapp/report"}:
            self._send_json(404, {"error": "not_found"})
            return

        telegram_user = self._telegram_user()
        if not telegram_user:
            self._send_json(401, {"error": "invalid_init_data"})
            return

        query = parse_qs(parsed.query)
        if parsed.path == "/api/miniapp/report":
            try:
                report = build_miniapp_period_report(
                    telegram_user,
                    (query.get("start") or [None])[0],
                    (query.get("end") or [None])[0],
                )
            except ValueError as exc:
                self._send_json(400, {"error": str(exc) or "invalid_period"})
                return
            self._send_json(200, report)
            return

        selected_day = _valid_miniapp_day((query.get("date") or [None])[0])
        db.record_user_event(int(telegram_user["id"]), "miniapp_opened")
        self._send_json(200, build_miniapp_payload(telegram_user, selected_day=selected_day))

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path not in {
            "/api/miniapp/food/text",
            "/api/miniapp/food/photo",
            "/api/miniapp/profile",
            "/api/miniapp/reminder",
            "/api/miniapp/water",
            "/api/miniapp/water-reminder",
            "/api/miniapp/streak/freeze",
        }:
            self._send_json(404, {"error": "not_found"})
            return

        telegram_user = self._telegram_user()
        if not telegram_user:
            self._send_json(401, {"error": "invalid_init_data"})
            return

        try:
            payload = self._read_json()
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            self._send_json(400, {"error": "bad_request"})
            return

        try:
            if parsed.path == "/api/miniapp/profile":
                try:
                    result = update_miniapp_profile(telegram_user, payload)
                except ValueError as exc:
                    self._send_json(400, {"error": str(exc) or "profile_out_of_range"})
                    return
                except Exception:
                    logger.exception("Failed to save mini app profile")
                    self._send_json(500, {"error": "profile_save_failed"})
                    return
                self._send_json(200, result)
                return
            if parsed.path == "/api/miniapp/reminder":
                result = update_miniapp_reminder(telegram_user, payload)
                self._send_json(200, result)
                return
            if parsed.path == "/api/miniapp/water":
                try:
                    result = update_miniapp_water(telegram_user, payload)
                except ValueError as exc:
                    self._send_json(400, {"error": str(exc) or "invalid_water_action"})
                    return
                self._send_json(200, result)
                return
            if parsed.path == "/api/miniapp/water-reminder":
                result = update_miniapp_water_reminder(telegram_user, payload)
                self._send_json(200, result)
                return
            if parsed.path == "/api/miniapp/streak/freeze":
                try:
                    result = freeze_miniapp_streak(telegram_user)
                except ValueError as exc:
                    self._send_json(409, {"error": str(exc) or "streak_freeze_unavailable"})
                    return
                self._send_json(200, result)
                return
            if parsed.path == "/api/miniapp/food/text":
                text = str(payload.get("text") or "").strip()
                if not text:
                    self._send_json(400, {"error": "empty_text"})
                    return
                result = asyncio.run(add_miniapp_food_text(telegram_user, text))
            else:
                image_base64 = str(payload.get("imageBase64") or "")
                mime_type = str(payload.get("mimeType") or "image/jpeg")
                if "," in image_base64:
                    image_base64 = image_base64.split(",", 1)[1]
                image_bytes = base64.b64decode(image_base64, validate=True)
                if not image_bytes:
                    self._send_json(400, {"error": "empty_photo"})
                    return
                result = asyncio.run(add_miniapp_food_photo(telegram_user, image_bytes, mime_type))
        except NotFoodError as exc:
            self._send_json(422, {"error": "not_food", "reason": exc.reason})
            return
        except (OpenAIRecognitionError, ValueError, binascii.Error):
            self._send_json(400, {"error": "recognition_failed"})
            return

        self._send_json(
            200,
            {
                "entry": self._entry_payload(result["entry"]),
                "state": result["state"],
            },
        )


def format_food_saved(entry: FoodEntry, user: User, entries: list[FoodEntry], is_photo: bool = False) -> str:
    totals = today_totals(entries)
    lines = ["Записал 🍽", "", entry.title]
    if entry.description and entry.description.strip().lower() != entry.title.strip().lower():
        lines.extend(["", entry.description])
    lines.extend(["", "Примерно:"])
    lines.extend(
        [
            f"{round_num(entry.calories)} ккал",
            f"Б: {round_num(entry.protein)} г",
            f"Ж: {round_num(entry.fat)} г",
            f"У: {round_num(entry.carbs)} г",
            "",
        ]
    )
    if user.calorie_target:
        left = max(0, user.calorie_target - round_num(totals["calories"]))
        lines.append(f"Осталось на сегодня: {left} ккал")
    return "\n".join(lines)


def format_water_reminder(summary: dict[str, int]) -> str:
    return (
        "💧 Небольшая пауза на воду\n\n"
        f"Сегодня в балансе {summary['total_ml']} из {summary['target_ml']} мл. "
        "Можно добавить одну обычную кружку.\n\n"
        "Не обязательно пить много сразу — небольшие порции тоже считаются."
    )


def streak_rescue_text(current_streak: int) -> str:
    return (
        f"Твой Ням-стрик: {current_streak} дней 🔥\n\n"
        "Сегодня в дневнике пока нет еды. Можно добавить хотя бы одну запись или использовать заморозку.\n\n"
        "❄️ Заморозка сохранит текущий стрик, но не увеличит его. Она доступна один раз в 7 дней."
    )


def format_weekly_report(summary: dict, period_start: date, period_end: date) -> str:
    lines = [
        "Твоя неделя с Нямметром 💚",
        f"С {period_start.strftime('%d.%m')} по {period_end.strftime('%d.%m')}",
        "",
        f"📅 Активных дней: {summary['active_days']} из 7",
        f"🍽 Записей еды: {summary['entries']}",
        f"✅ Полных дней: {summary['full_days']}",
        f"⚡ В среднем: {summary['average_calories']} ккал за активный день",
    ]
    if summary["target_days"] is not None:
        lines.append(f"🎯 Дней в коридоре цели: {summary['target_days']}")
    lines.append(f"🔥 Текущий Ням-стрик: {summary['current_streak']} дней")
    if summary["top_food"]:
        lines.append(f"🥣 Чаще всего записывал: {summary['top_food']}")
    lines.extend(["", "Каждая запись помогает лучше видеть свой ритм. Новая неделя уже началась ✨"])
    return "\n".join(lines)


def weekly_report_keyboard(telegram_id: int) -> InlineKeyboardMarkup | None:
    webapp_url = webapp_url_for_user(telegram_id)
    if not webapp_url:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Открыть Нямметр", web_app=WebAppInfo(url=webapp_url))],
        ]
    )


def water_reminder_keyboard(telegram_id: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="💧 +200 мл", callback_data="water:add:200")],
    ]
    webapp_url = webapp_url_for_user(telegram_id)
    if webapp_url:
        rows.append([InlineKeyboardButton(text="Открыть Нямметр", web_app=WebAppInfo(url=webapp_url))])
    rows.append([InlineKeyboardButton(text="Сегодня не напоминать", callback_data="water:skip")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def answer_not_food(message: Message, reason: str = "") -> None:
    text = "Похоже, это не еда, поэтому я ничего не записал 🙂"
    if reason:
        text += f"\n\nПричина: {reason}"
    text += "\n\nМожно отправить фото блюда или написать, что ты съел. Если нужно, нажми «Инструкция»."
    await message.answer(text)


async def safe_delete_message(message: Message | None) -> None:
    if message is None:
        return
    try:
        await message.delete()
    except TelegramBadRequest as exc:
        logger.debug("Telegram did not delete message %s: %s", message.message_id, exc)
    except Exception as exc:
        logger.debug("Could not delete message %s: %s", message.message_id, exc)
        return


async def cleanup_flow_messages(state: FSMContext, chat_id: int, bot: Bot) -> None:
    data = await state.get_data()
    message_ids = data.get("cleanup_message_ids", [])
    flow_message_id = data.get("flow_message_id")
    if flow_message_id:
        message_ids = [*message_ids, flow_message_id]
    for message_id in message_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
        except TelegramBadRequest:
            continue
    await state.update_data(cleanup_message_ids=[])


async def answer_clean(message: Message, state: FSMContext, text: str, **kwargs) -> Message:
    data = await state.get_data()
    flow_message_id = data.get("flow_message_id")
    reply_markup = kwargs.get("reply_markup")
    if flow_message_id:
        try:
            edited = await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=flow_message_id,
                text=text,
                reply_markup=reply_markup,
            )
            await state.update_data(flow_message_id=flow_message_id, cleanup_message_ids=[])
            return edited
        except TelegramBadRequest:
            pass
    sent = await message.answer(text, **kwargs)
    await state.update_data(flow_message_id=sent.message_id, cleanup_message_ids=[])
    return sent


async def callback_answer_clean(callback: CallbackQuery, state: FSMContext, text: str, **kwargs) -> Message:
    reply_markup = kwargs.get("reply_markup")
    if callback.message:
        try:
            edited = await callback.message.edit_text(text, reply_markup=reply_markup)
            await state.update_data(flow_message_id=callback.message.message_id, cleanup_message_ids=[])
            return edited
        except TelegramBadRequest:
            pass
    data = await state.get_data()
    flow_message_id = data.get("flow_message_id")
    if flow_message_id and callback.message:
        try:
            edited = await callback.bot.edit_message_text(
                chat_id=callback.message.chat.id,
                message_id=flow_message_id,
                text=text,
                reply_markup=reply_markup,
            )
            await state.update_data(flow_message_id=flow_message_id, cleanup_message_ids=[])
            return edited
        except TelegramBadRequest:
            pass
    sent = await callback.message.answer(text, **kwargs)
    await state.update_data(flow_message_id=sent.message_id, cleanup_message_ids=[])
    return sent


async def send_food_entry(message: Message, entry: FoodEntry, is_photo: bool = False) -> None:
    user = db.get_or_create_user(message.from_user.id)
    entries = db.get_today_entries(message.from_user.id)
    await message.answer(
        format_food_saved(entry, user, entries, is_photo=is_photo),
        reply_markup=food_actions(entry.id, can_fix_dish=entry.source == "photo"),
    )


async def maybe_send_nyam_streak(message: Message) -> None:
    streak = db.mark_nyam_streak_if_first_today(message.from_user.id)
    if not streak:
        return
    lines = [
        "День засчитан 💚",
        f"🔥 Ням-стрик: {streak['current_streak']} дней",
    ]
    if streak["best_updated"]:
        lines.append("Новый рекорд!")
    await message.answer("\n".join(lines))


async def maybe_send_achievements(message: Message) -> None:
    for achievement in db.unlock_available_achievements(message.from_user.id):
        await message.answer(
            f"🏆 Ачивка открыта: {achievement.emoji} {achievement.title}\n"
            f"{achievement.description}"
        )


def format_daily_mission(status: dict) -> str:
    mission = status["mission"]
    return (
        f"🎯 Миссия дня: {mission.emoji} {mission.title}\n"
        f"{mission.description}"
    )


async def maybe_send_daily_mission_completed(message: Message, telegram_id: int) -> None:
    mission = db.complete_daily_mission_if_ready(telegram_id)
    if mission:
        await message.answer(
            "Миссия выполнена 💚\n"
            f"{mission.emoji} {mission.title}\n"
            f"{mission.short_success_text}"
        )


def format_today(
    user: User,
    entries: list[FoodEntry],
    mission_status: dict | None = None,
    water_summary: dict[str, int] | None = None,
) -> str:
    totals = today_totals(entries)
    target = user.calorie_target
    protein_target = user.protein_target
    entries_count = len(entries)
    if entries_count >= 3:
        full_day_line = "Полный день закрыт 🍽"
    elif entries_count in (1, 2):
        full_day_line = f"До полного дня осталось {3 - entries_count} запись"
    else:
        full_day_line = "Добавь первую запись еды, и день начнется мягко 💚"

    if target:
        left = target - round_num(totals["calories"])
        calorie_line = f"Калории: {round_num(totals['calories'])} / {target} ккал"
        left_line = f"Осталось: {max(0, left)} ккал" if left >= 0 else f"Перебор: {abs(left)} ккал"
    else:
        calorie_line = f"Калории: {round_num(totals['calories'])} ккал"
        left_line = "Цель пока не настроена."

    protein_line = f"Белки: {round_num(totals['protein'])}"
    if protein_target:
        protein_line += f" / {protein_target} г"
    else:
        protein_line += " г"

    lines = [
        "Сегодня",
        "",
        f"🔥 Текущий Ням-стрик: {user.current_streak} дней",
        f"🏆 Лучший стрик: {user.best_streak} дней",
        f"🍽 Записей еды сегодня: {entries_count}",
        full_day_line,
        "",
    ]
    if mission_status:
        lines.extend([format_daily_mission(mission_status), ""])
    lines.extend(
        [
            calorie_line,
            left_line,
            protein_line,
            f"Жиры: {round_num(totals['fat'])} г",
            f"Углеводы: {round_num(totals['carbs'])} г",
            (
                f"Вода: {water_summary['total_ml']} из {water_summary['target_ml']} мл"
                if water_summary
                else ""
            ),
            "",
            "Еда:",
        ]
    )

    if not entries:
        lines.append("Пока записей нет.")
    else:
        for entry in entries:
            lines.append(f"- {entry.title}: {round_num(entry.calories)} ккал")
    return "\n".join(lines)


def parse_positive_int(text: str, min_value: int, max_value: int) -> int | None:
    try:
        value = int(text.strip())
    except ValueError:
        return None
    if min_value <= value <= max_value:
        return value
    return None


def parse_positive_float(text: str, min_value: float, max_value: float) -> float | None:
    normalized = text.strip().replace(",", ".")
    try:
        value = float(normalized)
    except ValueError:
        return None
    if min_value <= value <= max_value:
        return value
    return None


def normalize_reminder_time(text: str) -> str | None:
    value = text.strip()
    match = re.fullmatch(r"([01]?\d|2[0-3])[:. ]([0-5]\d)", value)
    if not match:
        match = re.fullmatch(r"([01]?\d|2[0-3])", value)
        if not match:
            return None
        return f"{int(match.group(1)):02d}:00"
    return f"{int(match.group(1)):02d}:{int(match.group(2)):02d}"


def is_admin(telegram_id: int) -> bool:
    return telegram_id in config.admin_ids


def format_admin_dashboard(screen: str) -> str:
    if screen == "today":
        return format_today_stats(admin_stats_service.get_stats_today())
    if screen == "week":
        return format_7d_stats(admin_stats_service.get_stats_7d())
    if screen == "month":
        return format_30d_stats(admin_stats_service.get_stats_30d())
    if screen == "daily":
        return format_daily_stats(admin_stats_service.get_daily_stats(7))
    if screen == "funnel":
        return format_funnel_stats(admin_stats_service.get_funnel_stats(7))
    if screen == "retention":
        return format_retention_stats(admin_stats_service.get_retention_stats())
    if screen == "channels":
        return format_channel_stats(admin_stats_service.get_channel_stats())
    if screen == "reminders":
        return format_reminders_stats(admin_stats_service.get_reminders_stats(7))
    if screen == "revenue":
        return format_revenue_stats(admin_stats_service.get_revenue_stats())
    if screen == "food7":
        return format_admin_popular_food("7 ДНЕЙ", 7)
    if screen == "foodall":
        return format_admin_popular_food("ВСЁ ВРЕМЯ", None)
    if screen == "food30":
        return format_admin_popular_food("30 ДНЕЙ", 30)
    return format_total_stats(apply_admin_dashboard_baseline(admin_stats_service.get_stats_total()))


def format_admin_popular_food(period_title: str, days: int | None) -> str:
    rows = db.admin_popular_food(days=days, limit=10)
    lines = [
        f"🍽 ПОПУЛЯРНАЯ ЕДА — {period_title}",
        "",
        "Рейтинг по числу разных пользователей:",
        "",
    ]
    if not rows:
        lines.append("За этот период записей еды пока нет.")
        return "\n".join(lines)

    for index, row in enumerate(rows, start=1):
        title = " ".join(str(row["title"]).split())
        title = title.capitalize()
        lines.append(
            f"{index}. {title}\n"
            f"👥 {int(row['users'])} чел. · 🍽 {int(row['entries'])} записей"
        )
    return "\n".join(lines)


def apply_admin_dashboard_baseline(stats: dict[str, int | float]) -> dict[str, int | float]:
    result = dict(stats)
    baseline = {**DEFAULT_ADMIN_TOTAL_BASELINE, **config.admin_total_baseline}
    offset = {**DEFAULT_ADMIN_TOTAL_BASELINE_OFFSET, **config.admin_total_baseline_offset}
    mapping = {
        "users_total": "total_users",
        "users_started": "total_starts",
        "users_goal_set": "total_goal_set",
        "users_with_reminders_total": "reminders_enabled_total",
        "food_entries": "meal_logs",
        "text_entries": "text_logs",
        "photo_recognitions": "photo_logs",
        "active_users": "total_active_users_ever",
        "calories": "kcal",
    }
    for old_key, new_key in mapping.items():
        if old_key not in baseline:
            continue
        current_value = result.get(new_key, 0)
        result[new_key] = baseline[old_key] + max(0, current_value - offset.get(old_key, 0))
    return result


def detect_admin_screen(text: str | None) -> str:
    source = text or ""
    if "ПОПУЛЯРНАЯ ЕДА" in source:
        if "7 ДНЕЙ" in source:
            return "food7"
        if "ВСЁ ВРЕМЯ" in source:
            return "foodall"
        return "food30"
    if "СЕГОДНЯ" in source:
        return "today"
    if "7 ДНЕЙ" in source:
        return "week"
    if "30 ДНЕЙ" in source:
        return "month"
    if "ОБЩЕЕ" in source:
        return "total"
    if "ПО ДНЯМ" in source:
        return "daily"
    if "ВОРОНКА" in source:
        return "funnel"
    if "RETENTION" in source:
        return "retention"
    if "КАНАЛЫ" in source:
        return "channels"
    if "НАПОМИНАНИЯ" in source:
        return "reminders"
    if "ДЕНЬГИ" in source:
        return "revenue"
    return "today"


def format_admin_period(title: str, days: int | None) -> str:
    stats = db.admin_period_stats(days)
    if days is None:
        stats = apply_admin_total_baseline(stats)
    users_total = int(stats["users_total"])
    food_entries = int(stats["food_entries"])
    active_users = int(stats["active_users"])
    average_entries = food_entries / active_users if active_users else 0

    lines = [
        title,
        "",
        f"Нажали /start: {int(stats['users_started'])}",
        f"Новых пользователей: {int(stats['users_new'])}",
        f"Поставили цель: {int(stats['users_goal_set'])}",
        f"Распознаваний фото: {int(stats['photo_recognitions'])}",
        f"Хоть раз писали текстом: {int(stats['users_wrote_text'])}",
        "",
        f"Записей еды: {food_entries}",
        f"Текстовых записей: {int(stats['text_entries'])}",
        f"Активных пользователей: {active_users}",
        f"В среднем записей на активного: {average_entries:.1f}",
        f"Ккал всего: {round_num(stats['calories'])}",
    ]
    if days is None:
        db_info = db.database_info()
        lines.extend(
            [
                "",
                f"Пользователей в базе: {users_total}",
                f"Всего с настроенной целью: {int(stats['users_with_goal_total'])}",
                f"Всего с напоминаниями: {int(stats['users_with_reminders_total'])}",
                f"Взаимодействовали 2 дня подряд: {int(stats['users_two_day_streak'])}",
                "Старая статистика из скрина: учтена",
                "",
                "База данных:",
                f"Путь: {db_info['path']}",
                f"Размер: {int(db_info['size'])} байт",
                (
                    "users / food / events / achievements / missions: "
                    f"{db_info['users']} / {db_info['entries']} / {db_info['events']} / "
                    f"{db_info['achievements']} / {db_info['missions']}"
                ),
                "Копии:",
                str(db_info["backups"]) or "нет",
            ]
        )
    return "\n".join(lines)


def apply_admin_total_baseline(stats: dict[str, int | float]) -> dict[str, int | float]:
    result = dict(stats)
    baseline = {**DEFAULT_ADMIN_TOTAL_BASELINE, **config.admin_total_baseline}
    offset = {**DEFAULT_ADMIN_TOTAL_BASELINE_OFFSET, **config.admin_total_baseline_offset}
    for key, value in baseline.items():
        current_value = result.get(key, 0)
        already_counted = offset.get(key, 0)
        result[key] = value + max(0, current_value - already_counted)
    return result


def format_admin_stats() -> str:
    stats = db.admin_stats()
    users_total = int(stats["users_total"])
    users_with_goal = int(stats["users_with_goal"])
    users_with_reminders = int(stats["users_with_reminders"])
    entries_total = int(stats["entries_total"])
    entries_today = int(stats["entries_today"])
    entries_week = int(stats["entries_week"])

    average_entries = entries_total / users_total if users_total else 0
    return (
        "Админка Нямметра\n\n"
        f"Пользователей всего: {users_total}\n"
        f"С настроенной целью: {users_with_goal}\n"
        f"С напоминаниями: {users_with_reminders}\n\n"
        f"Записей еды всего: {entries_total}\n"
        f"Сегодня: {entries_today}\n"
        f"За 7 дней: {entries_week}\n"
        f"В среднем на пользователя: {average_entries:.1f}\n\n"
        f"Активных сегодня: {int(stats['active_today'])}\n"
        f"Активных за 7 дней: {int(stats['active_week'])}\n\n"
        f"Фото-записей: {int(stats['photo_entries'])}\n"
        f"Текстовых записей: {int(stats['text_entries'])}\n\n"
        f"Ккал сегодня: {round_num(stats['calories_today'])}\n"
        f"Ккал за 7 дней: {round_num(stats['calories_week'])}"
    )


def format_admin_today() -> str:
    rows = db.admin_today_food()
    lines = [format_admin_period("Сегодня", 1), "", "По пользователям:", ""]
    if not rows:
        lines.append("Сегодня записей еды пока нет.")
    else:
        for row in rows:
            lines.append(
                f"{row['telegram_id']}: {row['entries']} записей, "
                f"{round_num(row['calories'] or 0)} ккал, Б {round_num(row['protein'] or 0)} г"
            )
    return "\n".join(lines)


def format_admin_week() -> str:
    rows = db.admin_week_food()
    lines = [format_admin_period("За 7 дней", 7), "", "По дням:", ""]
    if not rows:
        lines.append("За неделю записей пока нет.")
    else:
        for row in rows:
            lines.append(
                f"{row['day']}: {row['users']} пользователей, "
                f"{row['entries']} записей, {round_num(row['calories'] or 0)} ккал"
            )
    return "\n".join(lines)


def format_admin_users() -> str:
    rows = db.admin_latest_users()
    lines = ["Последние пользователи", ""]
    if not rows:
        lines.append("Пользователей пока нет.")
    else:
        for row in rows:
            goal_status = "цель есть" if row["calorie_target"] else "без цели"
            reminder = row["reminder_time"] or "без напоминаний"
            lines.append(
                f"{row['telegram_id']}: {row['entries']} записей, {goal_status}, "
                f"{reminder}, с {row['created_at']}"
            )
    return "\n".join(lines)


@router.message(Command("start"))
async def start(message: Message, state: FSMContext) -> None:
    start_parts = (message.text or "").split(maxsplit=1)
    if len(start_parts) == 2 and start_parts[1].startswith("ref_"):
        try:
            inviter_telegram_id = int(start_parts[1].removeprefix("ref_"))
        except ValueError:
            inviter_telegram_id = 0
        if inviter_telegram_id:
            db.register_referral(message.from_user.id, inviter_telegram_id)
    await cleanup_flow_messages(state, message.chat.id, message.bot)
    await state.clear()
    await safe_delete_message(message)
    user = db.record_start(message.from_user.id)
    await message.answer(
        "Привет, я Нямметр 🍽\n\n"
        "Помогаю считать калории без весов и таблиц. Начни в приложении или прямо здесь",
        reply_markup=main_menu(has_goal=bool(user.calorie_target), webapp_url=webapp_url_for_user(message.from_user.id)),
    )
    await answer_clean(
        message,
        state,
        "Укажи свои параметры, и я подберу дневную норму калорий и белка под твою цель.",
        reply_markup=setup_goal_intro_keyboard(),
    )


@router.callback_query(F.data == "reminder:choose")
async def reminder_choose(callback: CallbackQuery, state: FSMContext) -> None:
    db.record_user_event(callback.from_user.id, "reminder_choose_clicked")
    await callback_answer_clean(callback, state, "Выбери удобное время:", reply_markup=reminder_time_keyboard())
    await callback.answer()


@router.callback_query(F.data == "reminder:disable")
async def reminder_disable(callback: CallbackQuery, state: FSMContext) -> None:
    await cleanup_flow_messages(state, callback.message.chat.id, callback.bot)
    await state.clear()
    db.record_user_event(callback.from_user.id, "reminder_disabled")
    db.set_reminder_time(callback.from_user.id, None)
    await callback.message.answer("Хорошо, не буду напоминать.")
    await callback.answer()


@router.callback_query(F.data.startswith("reminder:time:"))
async def reminder_time(callback: CallbackQuery, state: FSMContext) -> None:
    await cleanup_flow_messages(state, callback.message.chat.id, callback.bot)
    await state.clear()
    reminder_time_value = callback.data.removeprefix("reminder:time:")
    db.record_user_event(callback.from_user.id, "reminder_time_set")
    db.set_reminder_time(callback.from_user.id, reminder_time_value)
    await callback.message.answer(
        f"Договорились! Буду напоминать про учет еды ежедневно в {reminder_time_value}."
    )
    await callback.answer()


@router.callback_query(F.data == "reminder:custom")
async def reminder_custom(callback: CallbackQuery, state: FSMContext) -> None:
    db.record_user_event(callback.from_user.id, "reminder_custom_clicked")
    await state.set_state(ReminderSetup.custom_time)
    await callback_answer_clean(callback, state, "Напиши время в формате 08:30 или 19:00.")
    await callback.answer()


@router.message(ReminderSetup.custom_time)
async def reminder_custom_apply(message: Message, state: FSMContext) -> None:
    reminder_time_value = normalize_reminder_time(message.text or "")
    await safe_delete_message(message)
    if reminder_time_value is None:
        await answer_clean(message, state, "Не понял время. Напиши, например: 08:30 или 19:00.")
        return
    await cleanup_flow_messages(state, message.chat.id, message.bot)
    db.record_user_event(message.from_user.id, "reminder_time_set")
    db.set_reminder_time(message.from_user.id, reminder_time_value)
    await state.clear()
    await message.answer(f"Договорились! Буду напоминать про учет еды ежедневно в {reminder_time_value}.")


@router.message(Command("help"))
@router.message(F.text == "Инструкция")
async def help_command(message: Message) -> None:
    db.record_user_event(message.from_user.id, "instruction_opened")
    await message.answer(
        "Инструкция по Нямметру 🍽\n\n"
        "1. Как добавить еду\n"
        "📸 Отправь фото блюда.\n"
        "✍️ Или напиши текстом: «2 яйца, творог 200 г, банан».\n\n"
        "2. Что я посчитаю\n"
        "🔥 Калории\n"
        "🥩 Белки\n"
        "🥑 Жиры\n"
        "🍚 Углеводы\n\n"
        "3. Как исправить запись\n"
        "Под каждой записью есть кнопки:\n"
        "➖ Порция меньше\n"
        "➕ Порция больше\n"
        "⚖️ Указать граммы\n"
        "🗑 Удалить\n"
        "Если еда была по фото, можно нажать «Исправить блюдо» и написать, что там на самом деле.\n\n"
        "4. Где смотреть прогресс\n"
        "📊 Нажми «Сегодня» или напиши /today — покажу калории, БЖУ и список еды за день.\n"
        "📅 Нажми «Дневник» или напиши /history — покажу последние 7 дней.\n\n"
        "5. Цель и напоминания\n"
        "🎯 Через «Настроить цель» или «Изменить цель/параметры» можно задать норму калорий и белка.\n"
        "⏰ После старта можно выбрать время ежедневного напоминания.\n\n"
        "Важно: все оценки примерные, особенно по фото. Нямметр помогает вести учет, но не заменяет врача или нутрициолога.",
        reply_markup=instruction_keyboard(),
    )


@router.message(Command("today"))
@router.message(F.text == "Сегодня")
async def today_command(message: Message) -> None:
    db.record_user_event(message.from_user.id, "today_opened")
    if datetime.now(MOSCOW_TZ).hour >= 18:
        db.record_user_event(message.from_user.id, "today_opened_evening")
    user = db.get_or_create_user(message.from_user.id)
    entries = db.get_today_entries(message.from_user.id)
    await maybe_send_daily_mission_completed(message, message.from_user.id)
    mission_status = db.get_daily_mission_status(message.from_user.id)
    water_summary = db.get_water_summary(message.from_user.id)
    await message.answer(
        format_today(
            user,
            entries,
            mission_status=mission_status,
            water_summary=water_summary,
        )
    )
    await maybe_send_achievements(message)
    await maybe_send_daily_mission_completed(message, message.from_user.id)


@router.message(Command("mission"))
async def mission_command(message: Message) -> None:
    db.record_user_event(message.from_user.id, "mission_opened")
    await maybe_send_daily_mission_completed(message, message.from_user.id)
    await message.answer(format_daily_mission(db.get_daily_mission_status(message.from_user.id)))


@router.message(Command("history"))
@router.message(F.text == "Дневник")
async def history_command(message: Message) -> None:
    db.record_user_event(message.from_user.id, "history_opened")
    user = db.get_or_create_user(message.from_user.id)
    progress = db.get_user_progress_stats(message.from_user.id)
    achievements = db.get_user_achievements(message.from_user.id)
    rows = db.get_daily_history(message.from_user.id)
    lines = [
        "Твой прогресс:",
        "",
        f"🔥 Текущий Ням-стрик: {progress['current_streak']} дней",
        f"🏆 Лучший стрик: {progress['best_streak']} дней",
        f"🍽 Всего записей еды: {progress['total_entries']}",
        f"📅 Дней с Нямметром: {progress['days_with_nyammetr']}",
        "",
        "Мои ачивки:",
    ]
    if achievements:
        for row in achievements:
            achievement = ACHIEVEMENTS.get(row["achievement_key"])
            if achievement:
                lines.append(f"{achievement.emoji} {achievement.title}")
    else:
        lines.append("Пока нет открытых ачивок. Они появятся сами по ходу дневника.")
    lines.extend(
        [
            "",
            "Последние 7 дней",
            "",
        ]
    )
    if not rows:
        lines.append("Пока записей нет.")
    else:
        for row in rows:
            calories = round_num(row["calories"] or 0)
            protein = round_num(row["protein"] or 0)
            if not user.calorie_target:
                status = "цель не настроена"
            elif calories < user.calorie_target * 0.9:
                status = "недобор"
            elif calories <= user.calorie_target * 1.1:
                status = "норма"
            else:
                status = "перебор"
            lines.append(f"{row['day']}: {calories} ккал, Б {protein} г, {status}")
    await message.answer("\n".join(lines))


@router.message(Command("admin"))
async def admin_command(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("Команда только для админа.")
        return
    db.record_user_event(message.from_user.id, "admin_opened")
    db.get_or_create_user(message.from_user.id)
    await message.answer(format_admin_dashboard("today"), reply_markup=admin_keyboard())


@router.message(Command("stats"))
async def stats_command(message: Message) -> None:
    await admin_command(message)


@router.message(Command("broadcast"))
async def broadcast_command(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("Команда только для админа.")
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        users_count = len(db.get_users_who_started())
        await message.answer(
            "Рассылка всем, кто нажимал /start.\n\n"
            "Формат:\n"
            "/broadcast текст сообщения\n\n"
            f"Сейчас в аудитории: {users_count} пользователей."
        )
        return

    broadcast_text = parts[1].strip()
    if len(broadcast_text) > 4000:
        await message.answer("Сообщение слишком длинное. Лучше уложиться до 4000 символов.")
        return

    users = db.get_users_who_started()
    if not users:
        await message.answer("Пока некому отправлять: нет пользователей с /start.")
        return

    await message.answer(f"Начинаю рассылку по {len(users)} пользователям.")

    campaign_id = f"broadcast:{datetime.now(MOSCOW_TZ).strftime('%Y%m%d%H%M%S')}"
    sent = 0
    blocked = 0
    failed = 0
    for user in users:
        try:
            await message.bot.send_message(user.telegram_id, broadcast_text)
            db.log_broadcast(user.telegram_id, campaign_id, "sent")
            sent += 1
        except TelegramForbiddenError:
            db.log_broadcast(user.telegram_id, campaign_id, "blocked", "bot_blocked")
            blocked += 1
            logger.info("Broadcast skipped blocked user %s", user.telegram_id)
        except Exception as exc:
            db.log_broadcast(user.telegram_id, campaign_id, "failed", str(exc))
            failed += 1
            logger.exception("Broadcast failed for user %s", user.telegram_id)
        await asyncio.sleep(0.05)

    db.record_user_event(message.from_user.id, "broadcast_sent")
    await message.answer(
        "Рассылка завершена.\n\n"
        f"ID рассылки: {campaign_id}\n"
        f"Отправлено: {sent}\n"
        f"Заблокировали бота: {blocked}\n"
        f"Ошибок: {failed}"
    )


@router.message(Command("broadcast_segment"))
async def broadcast_segment_command(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("Команда только для админа.")
        return

    parts = (message.text or "").split(maxsplit=2)
    segments = {
        "no_food": db.get_started_users_without_food,
        "started_no_food": db.get_started_users_without_food,
        "started_no_goal": db.get_started_users_without_goal,
        "one_food_no_return": db.get_users_with_one_food_no_return,
        "goal_no_food": db.get_users_with_goal_no_food_no_return,
        "loyal_users": db.get_loyal_users,
    }

    if len(parts) < 3 or not parts[1].strip() or not parts[2].strip():
        no_food_count = len(db.get_started_users_without_food())
        started_no_goal_count = len(db.get_started_users_without_goal())
        one_food_no_return_count = len(db.get_users_with_one_food_no_return())
        goal_no_food_count = len(db.get_users_with_goal_no_food_no_return())
        loyal_users_count = len(db.get_loyal_users())
        await message.answer(
            "Рассылка по сегменту.\n\n"
            "Формат:\n"
            "/broadcast_segment no_food текст сообщения\n\n"
            "Доступные сегменты:\n"
            f"no_food — нажали /start, но еще не добавляли еду: {no_food_count}\n"
            "started_no_goal — нажали /start, но не настроили цель: "
            f"{started_no_goal_count}\n"
            "one_food_no_return — добавили еду 1 раз и не возвращались минимум 2 дня: "
            f"{one_food_no_return_count}\n"
            "goal_no_food — поставили цель, но не добавили еду и не возвращались минимум 2 дня: "
            f"{goal_no_food_count}\n"
            "loyal_users — минимум 5 записей, 3 разные даты и активность за последние 7 дней: "
            f"{loyal_users_count}"
        )
        return

    segment = parts[1].strip().lower()
    broadcast_text = parts[2].strip()
    if segment not in segments:
        await message.answer(
            "Не знаю такой сегмент. Сейчас доступны: no_food, started_no_goal, "
            "one_food_no_return, goal_no_food, loyal_users."
        )
        return
    if len(broadcast_text) > 4000:
        await message.answer("Сообщение слишком длинное. Лучше уложиться до 4000 символов.")
        return

    users = segments[segment]()
    if not users:
        await message.answer("В этом сегменте пока никого нет.")
        return

    await message.answer(f"Начинаю рассылку по сегменту {segment}: {len(users)} пользователей.")

    campaign_id = f"broadcast_segment:{segment}:{datetime.now(MOSCOW_TZ).strftime('%Y%m%d%H%M%S')}"
    sent = 0
    blocked = 0
    failed = 0
    for user in users:
        try:
            await message.bot.send_message(user.telegram_id, broadcast_text)
            db.log_broadcast(user.telegram_id, campaign_id, "sent")
            sent += 1
        except TelegramForbiddenError:
            db.log_broadcast(user.telegram_id, campaign_id, "blocked", "bot_blocked")
            blocked += 1
            logger.info("Segment broadcast skipped blocked user %s", user.telegram_id)
        except Exception as exc:
            db.log_broadcast(user.telegram_id, campaign_id, "failed", str(exc))
            failed += 1
            logger.exception("Segment broadcast failed for user %s", user.telegram_id)
        await asyncio.sleep(0.05)

    db.record_user_event(message.from_user.id, "broadcast_segment_sent")
    await message.answer(
        "Рассылка по сегменту завершена.\n\n"
        f"ID рассылки: {campaign_id}\n"
        f"Сегмент: {segment}\n"
        f"Отправлено: {sent}\n"
        f"Заблокировали бота: {blocked}\n"
        f"Ошибок: {failed}"
    )


@router.message(Command("miniapp_debug"))
async def miniapp_debug_command(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("Команда только для админа.")
        return
    payload = build_miniapp_payload(
        {
            "id": message.from_user.id,
            "first_name": message.from_user.first_name,
            "username": message.from_user.username,
        }
    )
    entries = payload["today"]["entries"]
    lines = [
        "Miniapp debug",
        "",
        f"build: {WEBAPP_BUILD}",
        f"telegram_id: {message.from_user.id}",
        f"WEBAPP_URL: {webapp_url_with_build() or 'не задан'}",
        f"DB: {config.database_path}",
        f"today entries: {len(entries)}",
        f"today calories: {payload['today']['totals']['calories']}",
        f"target: {payload['targets']['calories'] or 'нет'} ккал",
    ]
    if entries:
        lines.append("")
        lines.append("entries:")
        for entry in entries[:5]:
            lines.append(f"- {entry['title']}: {entry['calories']} ккал, {entry['created_at']}")
    await message.answer("\n".join(lines))


@router.message(Command("miniapp_link"))
async def miniapp_link_command(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("Команда только для админа.")
        return
    url = webapp_url_with_build()
    if not url:
        await message.answer("WEBAPP_URL не задан.")
        return
    await message.answer(
        f"Miniapp build: {WEBAPP_BUILD}\n{url}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"Открыть Нямметр {WEBAPP_BUILD}",
                        web_app=WebAppInfo(url=url),
                    )
                ]
            ]
        ),
    )


@router.message(Command("fix_menu"))
async def fix_menu_command(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("Команда только для админа.")
        return
    url = webapp_url_with_build()
    if not url:
        await message.answer("WEBAPP_URL не задан.")
        return
    await message.bot.set_chat_menu_button(
        chat_id=message.chat.id,
        menu_button=MenuButtonWebApp(
            text=f"Нямметр {WEBAPP_BUILD}",
            web_app=WebAppInfo(url=url),
        ),
    )
    await message.answer(
        f"Синюю кнопку обновил на {WEBAPP_BUILD}\n{url}",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(Command("miniapp_files"))
async def miniapp_files_command(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("Команда только для админа.")
        return
    lines = [
        "Miniapp files",
        "",
        f"build: {WEBAPP_BUILD}",
        f"public_dir: {config.public_dir}",
        f"url: {webapp_url_with_build() or 'не задан'}",
    ]
    banned = ("Овсянка", "Завтрак", "1800", "2350", "420 ккал", "680 ккал")
    for name in ("index.html", "nyammetr-live.html"):
        path = config.public_dir / name
        lines.append("")
        lines.append(name)
        if not path.exists():
            lines.append("missing")
            continue
        raw = path.read_bytes()
        text = raw.decode("utf-8", errors="replace")
        digest = hashlib.sha256(raw).hexdigest()[:16]
        found = [word for word in banned if word in text]
        lines.append(f"size: {len(raw)}")
        lines.append(f"sha256: {digest}")
        lines.append(f"has old demo: {'yes ' + ', '.join(found) if found else 'no'}")
        lines.append(f"head: {text[:80].replace(chr(10), ' ')}")
    await message.answer("\n".join(lines))


@router.message(Command("backup_db"))
async def backup_db_command(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("Команда только для админа.")
        return
    db.record_user_event(message.from_user.id, "database_backup_requested")
    db_info = db.database_info()
    with TemporaryDirectory() as temp_dir:
        snapshot_path = db.export_snapshot(Path(temp_dir) / "calories.sqlite3")
        await message.answer_document(
            FSInputFile(snapshot_path, filename="calories.sqlite3"),
            caption=(
                "Снимок базы Нямметра.\n"
                f"Путь на сервере: {db_info['path']}\n"
                f"users / food / events: {db_info['users']} / {db_info['entries']} / {db_info['events']}\n"
                f"achievements / missions: {db_info['achievements']} / {db_info['missions']}"
            ),
        )


@router.callback_query(F.data.startswith("admin:"))
async def admin_callback(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Команда только для админа.", show_alert=True)
        return

    db.record_user_event(callback.from_user.id, "admin_clicked")
    action = callback.data.split(":")[-1]
    if action == "refresh":
        action = detect_admin_screen(callback.message.text if callback.message else "")
    text = format_admin_dashboard(action)
    keyboard = admin_food_keyboard(action) if action.startswith("food") else admin_keyboard()

    try:
        await callback.message.edit_text(text, reply_markup=keyboard)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise
    await callback.answer()


@router.message(Command("setup"))
@router.message(F.text == "Настроить цель")
@router.message(F.text == "Изменить цель/параметры")
async def setup_goal_start(message: Message, state: FSMContext) -> None:
    await cleanup_flow_messages(state, message.chat.id, message.bot)
    await state.clear()
    await safe_delete_message(message)
    db.record_useful_action(message.from_user.id, "goal_setup_started")
    await answer_clean(message, state, "Начнем с простого. Укажи пол:", reply_markup=sex_keyboard())


@router.callback_query(F.data == "setup:start")
async def setup_goal_start_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await cleanup_flow_messages(state, callback.message.chat.id, callback.bot)
    await state.clear()
    db.record_useful_action(callback.from_user.id, "goal_setup_started")
    await callback_answer_clean(callback, state, "Начнем с простого. Укажи пол:", reply_markup=sex_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("activation:"))
async def activation_callback(callback: CallbackQuery, state: FSMContext) -> None:
    action = callback.data.split(":")[-1]
    if action == "disable":
        db.record_user_event(callback.from_user.id, "activation_disabled")
        db.disable_activation(callback.from_user.id)
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer("Хорошо, больше не буду напоминать про старт 💚")
        await callback.answer()
        return
    db.record_user_event(callback.from_user.id, "activation_prompt_clicked")
    if action == "setup":
        await cleanup_flow_messages(state, callback.message.chat.id, callback.bot)
        await state.clear()
        db.record_useful_action(callback.from_user.id, "goal_setup_started")
        await callback_answer_clean(callback, state, "Начнем с простого. Укажи пол:", reply_markup=sex_keyboard())
        await callback.answer()
        return
    if action in {"photo", "try"}:
        await callback.message.answer("Отправь фото еды прямо сюда — я примерно посчитаю калории и БЖУ 🍽")
    elif action == "text":
        await callback.message.answer("Напиши, что ел сегодня. Например: омлет, кофе и банан ✍️")
    await callback.answer()


@router.callback_query(F.data.startswith("setup:sex:"))
async def setup_sex(callback: CallbackQuery, state: FSMContext) -> None:
    sex = callback.data.split(":")[-1]
    await state.update_data(sex=sex)
    await state.set_state(SetupGoal.age)
    await callback_answer_clean(callback, state, "Сколько тебе лет? Напиши число.")
    await callback.answer()


@router.message(SetupGoal.age)
async def setup_age(message: Message, state: FSMContext) -> None:
    age = parse_positive_int(message.text or "", 10, 100)
    await safe_delete_message(message)
    if age is None:
        await answer_clean(message, state, "Напиши возраст числом, например 32.")
        return
    await state.update_data(age=age)
    await state.set_state(SetupGoal.height)
    await answer_clean(message, state, "Какой рост в сантиметрах?")


@router.message(SetupGoal.height)
async def setup_height(message: Message, state: FSMContext) -> None:
    height = parse_positive_int(message.text or "", 100, 230)
    await safe_delete_message(message)
    if height is None:
        await answer_clean(message, state, "Напиши рост числом в сантиметрах, например 176.")
        return
    await state.update_data(height=height)
    await state.set_state(SetupGoal.weight)
    await answer_clean(message, state, "Какой вес в килограммах?")


@router.message(SetupGoal.weight)
async def setup_weight(message: Message, state: FSMContext) -> None:
    weight = parse_positive_float(message.text or "", 30, 300)
    await safe_delete_message(message)
    if weight is None:
        await answer_clean(message, state, "Напиши вес числом, например 72 или 72.5.")
        return
    await state.update_data(weight=weight)
    await answer_clean(message, state, "Какая цель?", reply_markup=goal_keyboard())


@router.callback_query(F.data.startswith("setup:goal:"))
async def setup_goal(callback: CallbackQuery, state: FSMContext) -> None:
    goal = callback.data.split(":")[-1]
    await state.update_data(goal=goal)
    await callback_answer_clean(callback, state, "Какая активность?", reply_markup=activity_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("setup:activity:"))
async def setup_activity(callback: CallbackQuery, state: FSMContext) -> None:
    activity = callback.data.split(":")[-1]
    data = await state.get_data()
    data["activity"] = activity
    calorie_target, protein_target = calculate_targets(
        sex=data["sex"],
        age=data["age"],
        height=data["height"],
        weight=data["weight"],
        goal=data["goal"],
        activity=data["activity"],
    )
    data["calorie_target"] = calorie_target
    data["protein_target"] = protein_target
    db.update_user_goal(callback.from_user.id, data)
    db.activate_referral(callback.from_user.id)
    await cleanup_flow_messages(state, callback.message.chat.id, callback.bot)
    await state.clear()
    await callback.message.answer(
        "Готово, цель настроена.\n\n"
        f"Цель: {GOAL_LABELS[data['goal']]}\n"
        f"Активность: {ACTIVITY_LABELS[data['activity']]}\n"
        f"Дневная норма: {calorie_target} ккал\n"
        f"Белок: {protein_target} г в день",
        reply_markup=main_menu(has_goal=True, webapp_url=webapp_url_for_user(callback.from_user.id)),
    )
    reminder_message = await callback.message.answer(
        "Во сколько тебе обычно напоминать про учет еды?",
        reply_markup=reminder_keyboard(),
    )
    await state.update_data(cleanup_message_ids=[reminder_message.message_id])
    await callback.answer()


@router.callback_query(F.data.startswith("food:scale:"))
async def food_scale(callback: CallbackQuery) -> None:
    _, _, raw_entry_id, raw_factor = callback.data.split(":")
    entry = db.scale_food_entry(int(raw_entry_id), callback.from_user.id, float(raw_factor))
    if entry is None:
        await callback.answer("Запись не найдена", show_alert=True)
        return
    user = db.get_or_create_user(callback.from_user.id)
    entries = db.get_today_entries(callback.from_user.id)
    await callback.message.edit_text(
        format_food_saved(entry, user, entries),
        reply_markup=food_actions(entry.id, can_fix_dish=entry.source == "photo"),
    )
    db.record_user_event(callback.from_user.id, "portion_adjustment")
    await callback.answer("Порция обновлена")
    await maybe_send_daily_mission_completed(callback.message, callback.from_user.id)


@router.callback_query(F.data.startswith("food:delete:"))
async def food_delete(callback: CallbackQuery) -> None:
    entry_id = int(callback.data.split(":")[-1])
    deleted = db.delete_food_entry(entry_id, callback.from_user.id)
    if deleted:
        await callback.message.edit_text("Удалил запись.")
        await callback.answer("Удалено")
    else:
        await callback.answer("Запись не найдена", show_alert=True)


@router.callback_query(F.data.startswith("food:ok:"))
async def food_ok(callback: CallbackQuery) -> None:
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Готово")


@router.callback_query(F.data.startswith("food:grams:"))
async def food_grams(callback: CallbackQuery, state: FSMContext) -> None:
    entry_id = int(callback.data.split(":")[-1])
    if db.get_food_entry(entry_id, callback.from_user.id) is None:
        await callback.answer("Запись не найдена", show_alert=True)
        return
    await state.set_state(PortionCorrection.grams)
    await state.update_data(entry_id=entry_id)
    await callback.message.answer("Напиши новый вес или порцию, например: 180 г, 2 тарелки, половина порции.")
    await callback.answer()


@router.callback_query(F.data.startswith("goal_nudge:"))
async def goal_nudge_callback(callback: CallbackQuery, state: FSMContext) -> None:
    action = callback.data.split(":")[-1]
    if action == "disable":
        db.record_user_event(callback.from_user.id, "goal_nudge_disabled")
        db.disable_activation(callback.from_user.id)
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer("Хорошо, больше не буду напоминать про настройку цели 💚")
        await callback.answer()
        return

    db.record_user_event(callback.from_user.id, "goal_nudge_clicked")
    await cleanup_flow_messages(state, callback.message.chat.id, callback.bot)
    await state.clear()
    db.record_useful_action(callback.from_user.id, "goal_setup_started")
    await callback_answer_clean(callback, state, "Начнем с простого. Укажи пол:", reply_markup=sex_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("streak_rescue:"))
async def streak_rescue_callback(callback: CallbackQuery) -> None:
    action = callback.data.split(":")[-1]
    if action == "add":
        await callback.message.answer("Отправь фото еды или напиши, что съел — я добавлю запись в дневник 🍽")
        await callback.answer()
        return

    result = db.freeze_streak(callback.from_user.id)
    status = result["status"]
    current_streak = int(result["current_streak"])
    messages = {
        "frozen": f"Ням-стрик заморожен ❄️\n🔥 Серия сохранена: {current_streak} дней",
        "already_frozen": f"Сегодняшний стрик уже заморожен ❄️\n🔥 Серия: {current_streak} дней",
        "active_today": "Сегодня уже есть запись еды — заморозка не нужна 💚",
        "cooldown": "Заморозка уже использовалась за последние 7 дней. Стрик еще можно сохранить записью еды 🍽",
        "not_at_risk": "Сейчас заморозка не требуется.",
    }
    await callback.message.edit_text(messages.get(status, "Не получилось заморозить стрик."))
    await callback.answer()


@router.callback_query(F.data == "water:add:200")
async def water_reminder_add(callback: CallbackQuery) -> None:
    summary = db.add_water_entry(callback.from_user.id, 200)
    db.record_user_event(callback.from_user.id, "water_reminder_quick_add")
    await callback.answer("Добавил 200 мл 💧")
    if callback.message:
        try:
            await callback.message.edit_text(
                format_water_reminder(summary),
                reply_markup=water_reminder_keyboard(callback.from_user.id),
            )
        except Exception:
            logger.debug("Could not refresh water reminder for user %s", callback.from_user.id)


@router.callback_query(F.data == "water:skip")
async def water_reminder_skip(callback: CallbackQuery) -> None:
    today = datetime.now(MOSCOW_TZ).date().isoformat()
    db.skip_water_reminders_today(callback.from_user.id, today)
    db.record_user_event(callback.from_user.id, "water_reminder_skipped_today")
    if callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            logger.debug("Could not hide water reminder buttons for user %s", callback.from_user.id)
    await callback.answer("Сегодня больше не напомню 💚")


@router.callback_query(F.data.startswith("food:fix:"))
async def food_fix(callback: CallbackQuery, state: FSMContext) -> None:
    entry_id = int(callback.data.split(":")[-1])
    entry = db.get_food_entry(entry_id, callback.from_user.id)
    if entry is None:
        await callback.answer("Запись не найдена", show_alert=True)
        return
    await state.set_state(PortionCorrection.dish)
    await state.update_data(entry_id=entry_id)
    await callback.message.answer("Напиши, что это за блюдо и примерную порцию. Например: плов с курицей, одна тарелка.")
    await callback.answer()


@router.message(PortionCorrection.grams)
async def food_grams_apply(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    entry = db.get_food_entry(int(data["entry_id"]), message.from_user.id)
    await safe_delete_message(message)
    if entry is None:
        await state.clear()
        await message.answer("Не нашел запись. Попробуй добавить еду заново.")
        return
    try:
        estimate = await food_ai.estimate_with_portion(
            previous_description=f"{entry.title}. {entry.description}",
            portion=message.text or "",
        )
    except NotFoodError as exc:
        await answer_not_food(message, exc.reason)
        return
    except OpenAIRecognitionError:
        await message.answer("Не смог распознать, попробуй еще раз или опиши еду текстом.")
        return
    updated = db.replace_food_entry_estimate(entry.id, message.from_user.id, estimate)
    await state.clear()
    db.record_user_event(message.from_user.id, "portion_adjustment")
    await send_food_entry(message, updated)
    await maybe_send_daily_mission_completed(message, message.from_user.id)


@router.message(PortionCorrection.dish)
async def food_fix_apply(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    entry = db.get_food_entry(int(data["entry_id"]), message.from_user.id)
    await safe_delete_message(message)
    if entry is None:
        await state.clear()
        await message.answer("Не нашел запись. Попробуй добавить еду заново.")
        return
    try:
        estimate = await food_ai.estimate_text(message.text or "")
    except NotFoodError as exc:
        await answer_not_food(message, exc.reason)
        return
    except OpenAIRecognitionError:
        await message.answer("Не смог распознать, попробуй еще раз или опиши еду текстом.")
        return
    updated = db.replace_food_entry_estimate(entry.id, message.from_user.id, estimate)
    await state.clear()
    await message.answer("Исправил блюдо и пересчитал примерные калории.")
    await send_food_entry(message, updated, is_photo=entry.source == "photo")


@router.message(F.photo)
async def photo_food(message: Message, bot: Bot) -> None:
    db.record_user_event(message.from_user.id, "photo_recognition")
    await message.answer("Смотрю фото и прикидываю калории...")
    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    buffer = BytesIO()
    await bot.download_file(file.file_path, buffer)
    image_bytes = buffer.getvalue()
    try:
        estimate = await food_ai.estimate_image(image_bytes)
    except NotFoodError as exc:
        await answer_not_food(message, exc.reason)
        return
    except OpenAIRecognitionError:
        await message.answer("Не смог распознать, попробуй еще раз или опиши еду текстом.")
        return
    entry = db.add_food_entry(message.from_user.id, estimate, source="photo")
    db.record_useful_action(message.from_user.id, "food_photo_added")
    db.activate_referral(message.from_user.id)
    await send_food_entry(message, entry, is_photo=True)
    await maybe_send_nyam_streak(message)
    await maybe_send_achievements(message)
    await maybe_send_daily_mission_completed(message, message.from_user.id)


@router.message(F.text)
async def text_food(message: Message) -> None:
    text = (message.text or "").strip()
    if not text:
        return
    db.record_user_event(message.from_user.id, "text_input")
    await message.answer("Считаю примерные калории...")
    try:
        estimate = await food_ai.estimate_text(text)
    except NotFoodError as exc:
        await answer_not_food(message, exc.reason)
        return
    except OpenAIRecognitionError:
        await message.answer("Не смог распознать, попробуй еще раз или опиши еду текстом.")
        return
    entry = db.add_food_entry(message.from_user.id, estimate, source="text")
    db.record_useful_action(message.from_user.id, "food_text_added")
    db.activate_referral(message.from_user.id)
    await send_food_entry(message, entry)
    await maybe_send_nyam_streak(message)
    await maybe_send_achievements(message)
    await maybe_send_daily_mission_completed(message, message.from_user.id)


async def main() -> None:
    global BOT_USERNAME
    if not config.bot_token:
        raise RuntimeError("BOT_TOKEN is not set")
    if not config.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    db.init()
    bot = Bot(token=config.bot_token)
    bot_info = await bot.get_me()
    BOT_USERNAME = bot_info.username or ""
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(router)
    logger.info("Bot started with database: %s, timezone: %s", config.database_path, config.timezone)
    web_server = await start_miniapp_server()
    await configure_bot_ui(bot)
    reminder_task = asyncio.create_task(reminder_loop(bot))
    try:
        await dispatcher.start_polling(bot)
    finally:
        reminder_task.cancel()
        web_server.shutdown()
        web_server.server_close()
        await bot.session.close()


async def start_miniapp_server() -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("0.0.0.0", config.port), MiniAppApiHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("Mini app API started on http://0.0.0.0:%s", config.port)
    return server


async def configure_bot_ui(bot: Bot) -> None:
    if not config.webapp_url:
        await bot.set_chat_menu_button(menu_button=MenuButtonDefault())
        return

    await bot.set_chat_menu_button(
        menu_button=MenuButtonWebApp(
            text="Нямметр",
            web_app=WebAppInfo(url=webapp_url_with_build()),
        )
    )


async def reminder_loop(bot: Bot) -> None:
    while True:
        now = datetime.now(MOSCOW_TZ)
        current_time = now.strftime("%H:%M")
        today_date = now.date()
        today = today_date.isoformat()
        reminder_users = db.get_users_for_reminder(current_time, today)
        reminders_sent = 0
        reminders_logged = 0
        for user in reminder_users:
            try:
                mission_status = db.get_daily_mission_status(user.telegram_id)
                await bot.send_message(
                    user.telegram_id,
                    daily_reminder_text(user.telegram_id, today_date, mission_status),
                )
                db.mark_reminder_sent(user.telegram_id, today)
                db.log_reminder(user.telegram_id, "daily", current_time, "sent")
                reminders_sent += 1
                reminders_logged += 1
            except Exception as exc:
                db.log_reminder(user.telegram_id, "daily", current_time, "failed", str(exc))
                reminders_logged += 1
                logger.exception("Failed to send reminder to user %s", user.telegram_id)
        logger.debug(
            "Reminder loop %s: found=%s sent=%s reminder_logs=%s",
            current_time,
            len(reminder_users),
            reminders_sent,
            reminders_logged,
        )
        if reminder_users:
            logger.info(
                "Reminder loop %s: found=%s sent=%s reminder_logs=%s",
                current_time,
                len(reminder_users),
                reminders_sent,
                reminders_logged,
            )
        if today_date.weekday() == 0 and WEEKLY_REPORT_TIME <= current_time < "23:30":
            report_end = today_date - timedelta(days=1)
            report_start = report_end - timedelta(days=6)
            report_key = f"{report_start.isoformat()}:{report_end.isoformat()}"
            report_users = db.get_users_for_weekly_report(
                report_start.isoformat(),
                report_end.isoformat(),
                report_key,
            )
            reports_sent = 0
            for user in report_users:
                try:
                    summary = db.get_weekly_report_summary(
                        user.telegram_id,
                        report_start.isoformat(),
                        report_end.isoformat(),
                    )
                    await bot.send_message(
                        user.telegram_id,
                        format_weekly_report(summary, report_start, report_end),
                        reply_markup=weekly_report_keyboard(user.telegram_id),
                    )
                    db.log_reminder(user.telegram_id, "weekly_report", report_key, "sent")
                    reports_sent += 1
                except TelegramForbiddenError:
                    db.log_reminder(user.telegram_id, "weekly_report", report_key, "blocked", "bot_blocked")
                    logger.info("Weekly report skipped blocked user %s", user.telegram_id)
                except Exception as exc:
                    db.log_reminder(user.telegram_id, "weekly_report", report_key, "failed", str(exc))
                    logger.exception("Failed to send weekly report to user %s", user.telegram_id)
                await asyncio.sleep(0.05)
            if report_users:
                logger.info(
                    "Weekly report %s: candidates=%s sent=%s",
                    report_key,
                    len(report_users),
                    reports_sent,
                )
        water_ratio = WATER_REMINDER_SLOTS.get(current_time)
        if water_ratio is not None:
            water_candidates = db.get_users_for_water_reminder(current_time, today)
            water_sent = 0
            for user in water_candidates:
                summary = db.get_water_summary(user.telegram_id)
                expected_ml = round(summary["target_ml"] * water_ratio)
                if summary["total_ml"] >= max(0, expected_ml - 300):
                    continue
                try:
                    await bot.send_message(
                        user.telegram_id,
                        format_water_reminder(summary),
                        reply_markup=water_reminder_keyboard(user.telegram_id),
                    )
                    db.log_reminder(user.telegram_id, "water", current_time, "sent")
                    water_sent += 1
                except TelegramForbiddenError:
                    db.set_water_reminders_enabled(user.telegram_id, False)
                    db.log_reminder(user.telegram_id, "water", current_time, "failed", "bot_blocked")
                    logger.info("Water reminders disabled for blocked user %s", user.telegram_id)
                except Exception as exc:
                    db.log_reminder(user.telegram_id, "water", current_time, "failed", str(exc))
                    logger.exception("Failed to send water reminder to user %s", user.telegram_id)
                await asyncio.sleep(0.05)
            logger.info(
                "Water reminder loop %s: candidates=%s sent=%s",
                current_time,
                len(water_candidates),
                water_sent,
            )
        if is_activation_window(now):
            for user, step in db.get_users_for_activation():
                if db.has_reactivation_push_today(user.telegram_id):
                    logger.info("Activation push skipped by daily cap for user %s", user.telegram_id)
                    continue
                try:
                    await bot.send_message(
                        user.telegram_id,
                        ACTIVATION_TEXTS[step],
                        reply_markup=activation_keyboard(step),
                    )
                    db.log_reminder(user.telegram_id, "activation", f"step:{step}", "sent")
                    db.mark_activation_sent(user.telegram_id, step)
                except TelegramForbiddenError:
                    db.disable_activation(user.telegram_id)
                    db.log_reminder(user.telegram_id, "activation", f"step:{step}", "failed", "bot_blocked")
                    logger.info("User %s blocked bot, activation disabled", user.telegram_id)
                except Exception as exc:
                    db.log_reminder(user.telegram_id, "activation", f"step:{step}", "failed", str(exc))
                    logger.exception("Failed to send activation message to user %s", user.telegram_id)
            for segment, texts in LIFECYCLE_PUSH_TEXTS.items():
                for user, step in db.get_users_for_lifecycle_push(segment, max_steps=len(texts)):
                    if db.has_reactivation_push_today(user.telegram_id):
                        logger.info(
                            "Lifecycle push %s skipped by daily cap for user %s",
                            segment,
                            user.telegram_id,
                        )
                        continue
                    text = texts.get(step)
                    if not text:
                        continue
                    try:
                        reply_markup = (
                            goal_nudge_keyboard(step, webapp_url_with_build())
                            if segment == "started_no_goal"
                            else None
                        )
                        await bot.send_message(user.telegram_id, text, reply_markup=reply_markup)
                        db.log_lifecycle_push(user.telegram_id, segment, step, "sent")
                        db.log_reminder(user.telegram_id, f"lifecycle:{segment}", f"step:{step}", "sent")
                    except TelegramForbiddenError:
                        db.log_lifecycle_push(user.telegram_id, segment, step, "blocked", "bot_blocked")
                        db.log_reminder(user.telegram_id, f"lifecycle:{segment}", f"step:{step}", "failed", "bot_blocked")
                        logger.info("Lifecycle push skipped blocked user %s", user.telegram_id)
                    except Exception as exc:
                        db.log_lifecycle_push(user.telegram_id, segment, step, "failed", str(exc))
                        db.log_reminder(user.telegram_id, f"lifecycle:{segment}", f"step:{step}", "failed", str(exc))
                        logger.exception("Failed to send lifecycle push %s step %s to user %s", segment, step, user.telegram_id)
                    await asyncio.sleep(0.05)
        if current_time == STREAK_RESCUE_TIME:
            streak_candidates = db.get_users_for_streak_rescue(today, (today_date - timedelta(days=1)).isoformat())
            streak_sent = 0
            for user in streak_candidates:
                if db.has_reactivation_push_today(user.telegram_id):
                    logger.info("Streak rescue skipped by daily cap for user %s", user.telegram_id)
                    continue
                try:
                    await bot.send_message(
                        user.telegram_id,
                        streak_rescue_text(user.current_streak),
                        reply_markup=streak_rescue_keyboard(webapp_url_with_build()),
                    )
                    db.log_reminder(user.telegram_id, "streak_rescue", current_time, "sent")
                    streak_sent += 1
                except TelegramForbiddenError:
                    db.log_reminder(user.telegram_id, "streak_rescue", current_time, "failed", "bot_blocked")
                    logger.info("Streak rescue skipped blocked user %s", user.telegram_id)
                except Exception as exc:
                    db.log_reminder(user.telegram_id, "streak_rescue", current_time, "failed", str(exc))
                    logger.exception("Failed to send streak rescue to user %s", user.telegram_id)
                await asyncio.sleep(0.05)
            logger.info("Streak rescue loop %s: candidates=%s sent=%s", current_time, len(streak_candidates), streak_sent)
        if current_time == config.auto_push_time:
            yesterday = (today_date - timedelta(days=1)).isoformat()
            day_before_yesterday = (today_date - timedelta(days=2)).isoformat()
            for user in db.get_users_for_duolingo_push(today, yesterday, day_before_yesterday):
                if db.has_reactivation_push_today(user.telegram_id):
                    logger.info("Duolingo push skipped by daily cap for user %s", user.telegram_id)
                    continue
                try:
                    await bot.send_message(user.telegram_id, duolingo_push_text(user.telegram_id, today))
                    db.mark_duolingo_push_sent(user.telegram_id)
                    db.log_reminder(user.telegram_id, "duolingo", config.auto_push_time, "sent")
                except Exception as exc:
                    db.log_reminder(user.telegram_id, "duolingo", config.auto_push_time, "failed", str(exc))
                    logger.exception("Failed to send duolingo push to user %s", user.telegram_id)
        await asyncio.sleep(60)


def duolingo_push_text(telegram_id: int, today: str) -> str:
    messages = [
        "Нямметр смотрит на пустой дневник 👀\n\nДва дня подряд ты был в ритме. Давай не терять серию: скинь фото еды или напиши, что уже ел сегодня.",
        "Твоя цель скучает без тебя 🍽\n\nВчера и позавчера ты заходил. Остался маленький шаг: запиши еду за сегодня.",
        "Серия почти живая 🔥\n\nНямметр верит в тебя. Одно фото или одно сообщение с едой — и день уже под контролем.",
        "Эй, чемпион учета калорий 😄\n\nТы держался два дня подряд. Не дай дневнику сегодня остаться голодным.",
    ]
    index = (telegram_id + sum(ord(char) for char in today)) % len(messages)
    return messages[index]


if __name__ == "__main__":
    asyncio.run(main())
