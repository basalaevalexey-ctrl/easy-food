from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from app.database import Database


ADMIN_SCREENS = {
    "today": "Сегодня",
    "week": "7 дней",
    "month": "30 дней",
    "total": "Общее",
    "daily": "По дням",
    "funnel": "Воронка",
    "retention": "Retention",
    "reminders": "Напоминания",
    "revenue": "Деньги",
}


@dataclass(frozen=True)
class AdminStatsService:
    db: Database

    def get_stats_today(self) -> dict[str, Any]:
        stats = self._period_stats(1)
        stats.update(self._retention_stats(1))
        stats.update(self._reminders_stats(1))
        return stats

    def get_stats_7d(self) -> dict[str, Any]:
        stats = self._period_stats(7)
        stats.update(self._growth_stats(7))
        stats.update(self._retention_stats(7))
        stats.update(self._reminders_stats(7))
        return stats

    def get_stats_30d(self) -> dict[str, Any]:
        stats = self._period_stats(30)
        stats.update(self._growth_stats(30))
        stats.update(self._retention_stats(30))
        stats.update(self._reminders_stats(30))
        return stats

    def get_stats_total(self) -> dict[str, Any]:
        stats = self._period_stats(None)
        stats.update(self._retention_stats(None))
        stats.update(self._reminders_stats(None))
        return stats

    def get_daily_stats(self, days: int = 7) -> list[dict[str, Any]]:
        start = date.today() - timedelta(days=days - 1)
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT date(created_at, 'localtime') AS day,
                       COUNT(DISTINCT user_id) AS active_users,
                       COUNT(*) AS meal_logs,
                       COALESCE(SUM(calories), 0) AS kcal
                FROM food_entries
                WHERE date(created_at, 'localtime') >= ?
                GROUP BY day
                """,
                (start.isoformat(),),
            ).fetchall()
        by_day = {row["day"]: dict(row) for row in rows}
        result = []
        for offset in range(days):
            day = start + timedelta(days=offset)
            row = by_day.get(day.isoformat(), {})
            result.append(
                {
                    "date": day.isoformat(),
                    "active_users": int(row.get("active_users", 0) or 0),
                    "meal_logs": int(row.get("meal_logs", 0) or 0),
                    "kcal": round_num(row.get("kcal", 0)),
                }
            )
        return result

    def get_funnel_stats(self, days: int = 7) -> dict[str, Any]:
        stats = self._period_stats(days)
        stats.update(self._retention_stats(days))
        return stats

    def get_retention_stats(self) -> dict[str, Any]:
        return self._retention_stats(None)

    def get_reminders_stats(self, days: int = 7) -> dict[str, Any]:
        return self._reminders_stats(days)

    def get_revenue_stats(self) -> dict[str, Any]:
        return {
            "mrr": 0,
            "paying_users": 0,
            "active_paying_users": 0,
            "paywall_views": 0,
            "checkout_started": 0,
            "payments_success": 0,
            "free_to_premium_conv": 0.0,
            "arppu": 0,
            "payments_connected": False,
        }

    def _period_stats(self, days: int | None) -> dict[str, Any]:
        date_filter = self._date_filter("created_at", days)
        event_filter = self._date_filter("created_at", days)
        user_filter = self._date_filter("created_at", days)
        previous_filter = self._previous_date_filter("created_at", days)

        with self.db.connect() as conn:
            row = conn.execute(
                f"""
                SELECT
                    (SELECT COUNT(*) FROM users) AS total_users,
                    (SELECT COUNT(*) FROM users WHERE {user_filter}) AS new_users,
                    (SELECT COUNT(*) FROM users WHERE {previous_filter}) AS previous_new_users,
                    (SELECT COUNT(DISTINCT user_id) FROM user_events WHERE event_type = 'start' AND {event_filter}) AS starts,
                    (SELECT COUNT(DISTINCT user_id) FROM user_events WHERE event_type = 'goal_set' AND {event_filter}) AS goal_set,
                    (SELECT COUNT(DISTINCT user_id) FROM food_entries WHERE {date_filter}) AS active_users,
                    (SELECT COUNT(DISTINCT user_id) FROM food_entries WHERE {previous_filter}) AS previous_active_users,
                    (SELECT COUNT(*) FROM food_entries WHERE {date_filter}) AS meal_logs,
                    (SELECT COUNT(*) FROM food_entries WHERE source = 'photo' AND {date_filter}) AS photo_logs,
                    (SELECT COUNT(*) FROM food_entries WHERE source = 'text' AND {date_filter}) AS text_logs,
                    (SELECT COALESCE(SUM(calories), 0) FROM food_entries WHERE {date_filter}) AS kcal,
                    (
                        SELECT COUNT(*) FROM (
                            SELECT user_id FROM food_entries
                            WHERE {date_filter}
                            GROUP BY user_id
                            HAVING COUNT(*) >= 3
                        )
                    ) AS users_3plus_logs,
                    (
                        SELECT COUNT(*) FROM (
                            SELECT user_id FROM food_entries
                            WHERE date(created_at, 'localtime') >= date('now', 'localtime', '-6 days')
                            GROUP BY user_id
                            HAVING COUNT(*) >= 5
                        )
                    ) AS power_users,
                    (
                        SELECT COUNT(DISTINCT u.id)
                        FROM users u
                        JOIN food_entries f ON f.user_id = u.id
                        WHERE u.calorie_target IS NOT NULL
                    ) AS total_activated_users,
                    (SELECT COUNT(DISTINCT user_id) FROM food_entries) AS total_active_users_ever,
                    (SELECT COUNT(*) FROM users WHERE calorie_target IS NOT NULL) AS total_goal_set,
                    (SELECT COUNT(DISTINCT user_id) FROM user_events WHERE event_type = 'start') AS total_starts,
                    (SELECT COUNT(*) FROM users WHERE reminder_time IS NOT NULL) AS reminders_enabled_total
                """
            ).fetchone()

            first_meal = conn.execute(
                f"""
                SELECT COUNT(*) AS value
                FROM (
                    SELECT user_id, MIN(created_at) AS first_at
                    FROM food_entries
                    GROUP BY user_id
                )
                WHERE {self._date_filter("first_at", days)}
                """,
            ).fetchone()["value"]

        stats = dict(row)
        stats["first_meal"] = int(first_meal or 0)
        stats["avg_logs_per_active"] = ratio(stats["meal_logs"], stats["active_users"])
        stats["goal_set_conv"] = percent(stats["goal_set"], stats["starts"])
        stats["first_meal_conv"] = percent(stats["first_meal"], stats["starts"])
        stats["users_3plus_logs_conv"] = percent(stats["users_3plus_logs"], stats["starts"])
        stats["photo_share"] = percent(stats["photo_logs"], stats["meal_logs"])
        stats["text_share"] = percent(stats["text_logs"], stats["meal_logs"])
        return {key: normalize(value) for key, value in stats.items()}

    def _growth_stats(self, days: int) -> dict[str, Any]:
        stats = self._period_stats(days)
        return {
            "new_users_growth": growth(stats["new_users"], stats["previous_new_users"]),
            "active_growth": growth(stats["active_users"], stats["previous_active_users"]),
        }

    def _retention_stats(self, days: int | None) -> dict[str, Any]:
        with self.db.connect() as conn:
            d1 = self._retention_for_day(conn, 1, days)
            d3 = self._retention_for_day(conn, 3, days)
            d7 = self._retention_for_day(conn, 7, days)
            d14 = self._retention_for_day(conn, 14, days)
            d30 = self._retention_for_day(conn, 30, days)
            streak_2 = self._streak_count(conn, 2, days)
            streak_7 = self._streak_count(conn, 7, days)
            streak_14 = self._streak_count(conn, 14, days)
        return {
            "d1_retention": d1["percent"],
            "d1_return_users": d1["returned"],
            "d1_return_conv": d1["percent"],
            "d3_retention": d3["percent"],
            "d7_retention": d7["percent"],
            "d7_return_users": d7["returned"],
            "d7_return_conv": d7["percent"],
            "d14_retention": d14["percent"],
            "d30_retention": d30["percent"],
            "streak_2": streak_2,
            "streak_7": streak_7,
            "streak_14": streak_14,
        }

    def _reminders_stats(self, days: int | None) -> dict[str, Any]:
        event_filter = self._date_filter("e.created_at", days)
        sent_filter = self._date_filter("created_at", days)
        with self.db.connect() as conn:
            sent = conn.execute(
                f"SELECT COUNT(*) FROM user_events WHERE event_type IN ('reminder_sent', 'duolingo_push_sent') AND {sent_filter}",
            ).fetchone()[0]
            converted = conn.execute(
                f"""
                SELECT COUNT(DISTINCT e.user_id)
                FROM user_events e
                JOIN food_entries f ON f.user_id = e.user_id
                 AND datetime(f.created_at) >= datetime(e.created_at)
                 AND datetime(f.created_at) <= datetime(e.created_at, '+1 hour')
                WHERE e.event_type IN ('reminder_sent', 'duolingo_push_sent')
                  AND {event_filter}
                """,
            ).fetchone()[0]
            enabled = conn.execute("SELECT COUNT(*) FROM users WHERE reminder_time IS NOT NULL").fetchone()[0]
        return {
            "reminders_enabled_total": int(enabled or 0),
            "reminders_sent": int(sent or 0),
            "reminder_converted": int(converted or 0),
            "reminder_cvr": percent(converted, sent),
            "morning_cvr": "нет данных",
            "day_cvr": "нет данных",
            "evening_cvr": "нет данных",
            "best_slot": "нет данных",
            "worst_slot": "нет данных",
        }

    def _retention_for_day(self, conn: Any, offset: int, days: int | None) -> dict[str, Any]:
        cohort_filter = self._date_filter("u.created_at", days)
        row = conn.execute(
            f"""
            SELECT COUNT(DISTINCT u.id) AS cohort,
                   COUNT(DISTINCT f.user_id) AS returned
            FROM users u
            LEFT JOIN food_entries f
              ON f.user_id = u.id
             AND date(f.created_at, 'localtime') = date(u.created_at, 'localtime', '+{offset} days')
            WHERE {cohort_filter}
            """
        ).fetchone()
        cohort = int(row["cohort"] or 0)
        returned = int(row["returned"] or 0)
        return {"cohort": cohort, "returned": returned, "percent": percent(returned, cohort)}

    def _streak_count(self, conn: Any, length: int, days: int | None) -> int:
        period_filter = self._date_filter("created_at", days)
        rows = conn.execute(
            f"""
            SELECT user_id, date(created_at, 'localtime') AS day
            FROM food_entries
            WHERE {period_filter}
            GROUP BY user_id, day
            ORDER BY user_id, day
            """
        ).fetchall()
        by_user: dict[int, list[date]] = {}
        for row in rows:
            by_user.setdefault(int(row["user_id"]), []).append(date.fromisoformat(row["day"]))
        total = 0
        for days_list in by_user.values():
            if has_consecutive_days(days_list, length):
                total += 1
        return total

    @staticmethod
    def _date_filter(column: str, days: int | None) -> str:
        if days is None:
            return "1 = 1"
        if days == 1:
            return f"date({column}, 'localtime') = date('now', 'localtime')"
        start = date.today() - timedelta(days=days - 1)
        return f"date({column}, 'localtime') >= date('{start.isoformat()}')"

    @staticmethod
    def _previous_date_filter(column: str, days: int | None) -> str:
        if days is None:
            return "0 = 1"
        if days == 1:
            return f"date({column}, 'localtime') = date('now', 'localtime', '-1 day')"
        current_start = date.today() - timedelta(days=days - 1)
        previous_start = current_start - timedelta(days=days)
        return (
            f"date({column}, 'localtime') >= date('{previous_start.isoformat()}') "
            f"AND date({column}, 'localtime') < date('{current_start.isoformat()}')"
        )


def format_today_stats(stats: dict[str, Any]) -> str:
    return "\n".join(
        [
            "📊 НЯММЕТР — СЕГОДНЯ",
            "",
            "━━━━━━━━━━━━",
            "👥 РОСТ",
            f"Новые пользователи: {stats['new_users']}",
            f"Активные пользователи: {stats['active_users']}",
            f"Всего в базе: {stats['total_users']}",
            "",
            "━━━━━━━━━━━━",
            "🚀 АКТИВАЦИЯ",
            f"Запустили бота: {stats['starts']}",
            f"Поставили цель: {stats['goal_set']} ({fmt_percent(stats['goal_set_conv'])})",
            f"Сделали 1-й лог еды: {stats['first_meal']} ({fmt_percent(stats['first_meal_conv'])})",
            "",
            "━━━━━━━━━━━━",
            "🍽 ИСПОЛЬЗОВАНИЕ",
            f"Логов еды: {stats['meal_logs']}",
            f"Фото-логов: {stats['photo_logs']}",
            f"Текст-логов: {stats['text_logs']}",
            f"Среднее логов на активного: {fmt_float(stats['avg_logs_per_active'])}",
            f"Ккал записано: {stats['kcal']}",
            "",
            "━━━━━━━━━━━━",
            "🔔 НАПОМИНАНИЯ",
            f"Пользователей с напоминаниями: {stats['reminders_enabled_total']}",
            f"Отправлено сегодня: {stats['reminders_sent']}",
            f"Сработало: {stats['reminder_converted']}",
            f"CVR: {fmt_percent(stats['reminder_cvr'])}",
            "",
            "━━━━━━━━━━━━",
            "🔥 УДЕРЖАНИЕ",
            f"2 дня подряд: {stats['streak_2']}",
            f"7 дней подряд: {stats['streak_7']}",
        ]
    )


def format_7d_stats(stats: dict[str, Any]) -> str:
    return format_period_stats("📊 НЯММЕТР — 7 ДНЕЙ", stats, active_label="Активные пользователи (WAU)")


def format_30d_stats(stats: dict[str, Any]) -> str:
    return format_period_stats("📊 НЯММЕТР — 30 ДНЕЙ", stats, active_label="Активные пользователи (MAU)")


def format_period_stats(title: str, stats: dict[str, Any], active_label: str) -> str:
    return "\n".join(
        [
            title,
            "",
            "━━━━━━━━━━━━",
            "👥 РОСТ",
            f"Новые пользователи: {stats['new_users']}",
            f"{active_label}: {stats['active_users']}",
            f"Всего в базе: {stats['total_users']}",
            f"Рост новых к прошлому периоду: {fmt_percent(stats.get('new_users_growth', 0))}",
            f"Рост активных к прошлому периоду: {fmt_percent(stats.get('active_growth', 0))}",
            "",
            "━━━━━━━━━━━━",
            "🚀 ВОРОНКА АКТИВАЦИИ",
            f"Запустили бота: {stats['starts']}",
            f"Поставили цель: {stats['goal_set']} ({fmt_percent(stats['goal_set_conv'])})",
            f"Сделали 1-й лог еды: {stats['first_meal']} ({fmt_percent(stats['first_meal_conv'])})",
            f"Сделали 3+ логов еды: {stats['users_3plus_logs']} ({fmt_percent(stats['users_3plus_logs_conv'])})",
            f"Вернулись на следующий день: {stats['d1_return_users']} ({fmt_percent(stats['d1_return_conv'])})",
            "",
            "━━━━━━━━━━━━",
            "🍽 ИСПОЛЬЗОВАНИЕ",
            f"Всего логов еды: {stats['meal_logs']}",
            f"Фото-логов: {stats['photo_logs']}",
            f"Текст-логов: {stats['text_logs']}",
            f"Фото / текст: {fmt_percent(stats['photo_share'])} / {fmt_percent(stats['text_share'])}",
            f"Среднее логов на активного: {fmt_float(stats['avg_logs_per_active'])}",
            f"Ккал записано: {stats['kcal']}",
            "",
            "━━━━━━━━━━━━",
            "🔥 УДЕРЖАНИЕ",
            f"D1 retention: {fmt_percent(stats['d1_retention'])}",
            f"D3 retention: {fmt_percent(stats['d3_retention'])}",
            f"D7 retention: {fmt_percent(stats['d7_retention'])}",
            f"2 дня подряд: {stats['streak_2']}",
            f"7 дней подряд: {stats['streak_7']}",
            "",
            "━━━━━━━━━━━━",
            "🔔 НАПОМИНАНИЯ",
            f"Пользователей с напоминаниями: {stats['reminders_enabled_total']}",
            f"Напоминаний отправлено: {stats['reminders_sent']}",
            f"Сработало: {stats['reminder_converted']}",
            f"CVR: {fmt_percent(stats['reminder_cvr'])}",
            "",
            "━━━━━━━━━━━━",
            "📈 КЛЮЧЕВАЯ МЕТРИКА",
            f"Активированные пользователи: {stats['total_activated_users']}",
            f"Сильные пользователи: {stats['power_users']}",
        ]
    )


def format_total_stats(stats: dict[str, Any]) -> str:
    return "\n".join(
        [
            "📊 НЯММЕТР — ОБЩЕЕ",
            "",
            "━━━━━━━━━━━━",
            "👥 БАЗА",
            f"Всего пользователей: {stats['total_users']}",
            f"Всего запустили бота: {stats['total_starts']}",
            f"Всего поставили цель: {stats['total_goal_set']}",
            f"Всего сделали хотя бы 1 лог еды: {stats['total_activated_users']}",
            f"Всего активных пользователей за всё время: {stats['total_active_users_ever']}",
            "",
            "━━━━━━━━━━━━",
            "🍽 ЗА ВСЁ ВРЕМЯ",
            f"Всего логов еды: {stats['meal_logs']}",
            f"Фото-логов: {stats['photo_logs']}",
            f"Текст-логов: {stats['text_logs']}",
            f"Среднее логов на активного: {fmt_float(stats['avg_logs_per_active'])}",
            f"Ккал записано: {stats['kcal']}",
            "",
            "━━━━━━━━━━━━",
            "🔔 НАПОМИНАНИЯ",
            f"Всего включили напоминания: {stats['reminders_enabled_total']}",
            f"Всего отправлено напоминаний: {stats['reminders_sent']}",
            f"Всего сработало после напоминания: {stats['reminder_converted']}",
            "",
            "━━━━━━━━━━━━",
            "🔥 ПОВЕДЕНИЕ",
            f"2 дня подряд: {stats['streak_2']}",
            f"7 дней подряд: {stats['streak_7']}",
            f"14 дней подряд: {stats['streak_14']}",
        ]
    )


def format_daily_stats(rows: list[dict[str, Any]]) -> str:
    lines = ["📅 НЯММЕТР — ПО ДНЯМ (7D)", ""]
    lines.extend(
        f"{row['date']} — {row['active_users']} активных • {row['meal_logs']} логов • {row['kcal']} ккал"
        for row in rows
    )
    return "\n".join(lines)


def format_funnel_stats(stats: dict[str, Any]) -> str:
    return "\n".join(
        [
            "🚀 НЯММЕТР — ВОРОНКА (7D)",
            "",
            f"Запустили бота: {stats['starts']}",
            f"└ Поставили цель: {stats['goal_set']} ({fmt_percent(stats['goal_set_conv'])})",
            f"└ Сделали 1-й лог еды: {stats['first_meal']} ({fmt_percent(stats['first_meal_conv'])})",
            f"└ Сделали 3+ логов: {stats['users_3plus_logs']} ({fmt_percent(stats['users_3plus_logs_conv'])})",
            f"└ Вернулись на следующий день: {stats['d1_return_users']} ({fmt_percent(stats['d1_return_conv'])})",
            f"└ Активны через 7 дней: {stats['d7_return_users']} ({fmt_percent(stats['d7_return_conv'])})",
        ]
    )


def format_retention_stats(stats: dict[str, Any]) -> str:
    return "\n".join(
        [
            "🔥 НЯММЕТР — RETENTION",
            "",
            f"D1: {fmt_percent(stats['d1_retention'])}",
            f"D3: {fmt_percent(stats['d3_retention'])}",
            f"D7: {fmt_percent(stats['d7_retention'])}",
            f"D14: {fmt_percent(stats['d14_retention'])}",
            f"D30: {fmt_percent(stats['d30_retention'])}",
            "",
            f"2 дня подряд: {stats['streak_2']}",
            f"7 дней подряд: {stats['streak_7']}",
            f"14 дней подряд: {stats['streak_14']}",
        ]
    )


def format_reminders_stats(stats: dict[str, Any]) -> str:
    return "\n".join(
        [
            "🔔 НЯММЕТР — НАПОМИНАНИЯ",
            "",
            f"Пользователей с напоминаниями: {stats['reminders_enabled_total']}",
            "",
            "За 7 дней:",
            f"Отправлено: {stats['reminders_sent']}",
            f"Сработало: {stats['reminder_converted']}",
            f"Конверсия: {fmt_percent(stats['reminder_cvr'])}",
            "",
            "По слотам:",
            f"Утро: {stats['morning_cvr']}",
            f"День: {stats['day_cvr']}",
            f"Вечер: {stats['evening_cvr']}",
            "",
            f"Лучший слот: {stats['best_slot']}",
            f"Худший слот: {stats['worst_slot']}",
        ]
    )


def format_revenue_stats(stats: dict[str, Any]) -> str:
    if not stats.get("payments_connected"):
        return "\n".join(
            [
                "💸 НЯММЕТР — ДЕНЬГИ",
                "",
                "Платежи пока не подключены.",
                "Здесь позже будут:",
                "MRR",
                "Платящие пользователи",
                "Конверсия в премиум",
                "ARPPU",
                "Отписки",
            ]
        )
    return "\n".join(
        [
            "💸 НЯММЕТР — ДЕНЬГИ",
            "",
            f"MRR: {stats['mrr']} ₽",
            f"Платящих пользователей: {stats['paying_users']}",
            f"Платящих активных: {stats['active_paying_users']}",
            f"Paywall views: {stats['paywall_views']}",
            f"Начали оплату: {stats['checkout_started']}",
            f"Оплатили: {stats['payments_success']}",
            f"Конверсия free → premium: {fmt_percent(stats['free_to_premium_conv'])}",
            f"ARPPU: {stats['arppu']} ₽",
        ]
    )


def ratio(numerator: Any, denominator: Any) -> float:
    denominator = float(denominator or 0)
    return round(float(numerator or 0) / denominator, 1) if denominator else 0.0


def percent(numerator: Any, denominator: Any) -> float:
    denominator = float(denominator or 0)
    return round(float(numerator or 0) / denominator * 100, 1) if denominator else 0.0


def growth(current: Any, previous: Any) -> float:
    previous = float(previous or 0)
    if not previous:
        return 0.0
    return round((float(current or 0) - previous) / previous * 100, 1)


def fmt_percent(value: Any) -> str:
    return f"{float(value or 0):.1f}%"


def fmt_float(value: Any) -> str:
    return f"{float(value or 0):.1f}"


def round_num(value: Any) -> int:
    return int(round(float(value or 0)))


def normalize(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 1)
    if value is None:
        return 0
    return value


def has_consecutive_days(days: list[date], length: int) -> bool:
    if not days:
        return False
    unique_days = sorted(set(days))
    current = 1
    previous = unique_days[0]
    for day in unique_days[1:]:
        if day == previous + timedelta(days=1):
            current += 1
        else:
            current = 1
        if current >= length:
            return True
        previous = day
    return length <= 1
