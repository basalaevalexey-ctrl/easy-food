from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.database import Database


MOSCOW_TZ = timezone(timedelta(hours=3))


def moscow_today() -> date:
    return datetime.now(MOSCOW_TZ).date()


ADMIN_SCREENS = {
    "today": "Сегодня",
    "week": "7 дней",
    "month": "30 дней",
    "total": "Общее",
    "daily": "По дням",
    "funnel": "Воронка",
    "retention": "Retention",
    "channels": "Каналы",
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
        stats.update(self._referral_stats(1))
        return stats

    def get_stats_7d(self) -> dict[str, Any]:
        stats = self._period_stats(7)
        stats.update(self._growth_stats(7))
        stats.update(self._retention_stats(7))
        stats.update(self._reminders_stats(7))
        stats.update(self._referral_stats(7))
        return stats

    def get_stats_30d(self) -> dict[str, Any]:
        stats = self._period_stats(30)
        stats.update(self._growth_stats(30))
        stats.update(self._retention_stats(30))
        stats.update(self._reminders_stats(30))
        stats.update(self._referral_stats(30))
        return stats

    def get_stats_total(self) -> dict[str, Any]:
        stats = self._period_stats(None)
        stats.update(self._retention_stats(None))
        stats.update(self._reminders_stats(None))
        stats.update(self._referral_stats(None))
        return stats

    def get_daily_stats(self, days: int = 7) -> list[dict[str, Any]]:
        start = moscow_today() - timedelta(days=days - 1)
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT date(created_at, '+3 hours') AS day,
                       COUNT(DISTINCT user_id) AS active_users,
                       COUNT(*) AS meal_logs,
                       COALESCE(SUM(calories), 0) AS kcal
                FROM food_entries
                WHERE date(created_at, '+3 hours') >= ?
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

    def get_channel_stats(self) -> dict[str, Any]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                WITH bot AS (
                    SELECT user_id, MIN(created_at) AS first_at
                    FROM user_events
                    WHERE event_type IN (
                        'text_input',
                        'photo_recognition',
                        'today_opened',
                        'history_opened',
                        'mission_opened',
                        'portion_adjustment'
                    )
                    GROUP BY user_id
                ),
                miniapp AS (
                    SELECT user_id, MIN(created_at) AS first_at
                    FROM user_events
                    WHERE event_type LIKE 'miniapp_%'
                       OR event_type IN ('food_text_added', 'food_photo_added')
                    GROUP BY user_id
                ),
                channels AS (
                    SELECT u.id AS user_id,
                           CASE
                               WHEN bot.user_id IS NOT NULL AND miniapp.user_id IS NOT NULL THEN 'mixed'
                               WHEN miniapp.user_id IS NOT NULL THEN 'miniapp_only'
                               WHEN bot.user_id IS NOT NULL THEN 'bot_only'
                               ELSE 'no_channel'
                           END AS channel,
                           CASE
                               WHEN bot.first_at IS NOT NULL AND miniapp.first_at IS NOT NULL
                                    THEN MIN(bot.first_at, miniapp.first_at)
                               ELSE COALESCE(bot.first_at, miniapp.first_at)
                           END AS first_at
                    FROM users u
                    LEFT JOIN bot ON bot.user_id = u.id
                    LEFT JOIN miniapp ON miniapp.user_id = u.id
                ),
                activity AS (
                    SELECT user_id, date(created_at, '+3 hours') AS day FROM user_events
                    UNION
                    SELECT user_id, date(created_at, '+3 hours') AS day FROM food_entries
                ),
                food_counts AS (
                    SELECT user_id, COUNT(*) AS logs
                    FROM food_entries
                    GROUP BY user_id
                )
                SELECT channel,
                       COUNT(*) AS users,
                       SUM(CASE WHEN EXISTS (
                           SELECT 1 FROM activity
                           WHERE activity.user_id = channels.user_id
                             AND activity.day = date(channels.first_at, '+3 hours', '+1 day')
                       ) THEN 1 ELSE 0 END) AS d1_users,
                       SUM(CASE WHEN EXISTS (
                           SELECT 1 FROM activity
                           WHERE activity.user_id = channels.user_id
                             AND activity.day = date(channels.first_at, '+3 hours', '+3 day')
                       ) THEN 1 ELSE 0 END) AS d3_users,
                       SUM(CASE WHEN EXISTS (
                           SELECT 1 FROM activity
                           WHERE activity.user_id = channels.user_id
                             AND activity.day = date(channels.first_at, '+3 hours', '+7 day')
                       ) THEN 1 ELSE 0 END) AS d7_users,
                       SUM(CASE WHEN COALESCE(food_counts.logs, 0) >= 1 THEN 1 ELSE 0 END) AS users_with_food,
                       SUM(CASE WHEN COALESCE(food_counts.logs, 0) >= 3 THEN 1 ELSE 0 END) AS users_3plus_food,
                       COALESCE(SUM(food_counts.logs), 0) AS meal_logs
                FROM channels
                LEFT JOIN food_counts ON food_counts.user_id = channels.user_id
                WHERE channel != 'no_channel'
                  AND first_at IS NOT NULL
                GROUP BY channel
                """
            ).fetchall()

        result: dict[str, Any] = {"channels": {}}
        for channel in ("bot_only", "miniapp_only", "mixed"):
            result["channels"][channel] = {
                "users": 0,
                "d1_users": 0,
                "d1_retention": 0.0,
                "d3_users": 0,
                "d3_retention": 0.0,
                "d7_users": 0,
                "d7_retention": 0.0,
                "users_with_food": 0,
                "first_log_conv": 0.0,
                "users_3plus_food": 0,
                "users_3plus_conv": 0.0,
                "meal_logs": 0,
                "avg_logs_per_user": 0.0,
            }

        for row in rows:
            users = int(row["users"] or 0)
            channel = row["channel"]
            result["channels"][channel] = {
                "users": users,
                "d1_users": int(row["d1_users"] or 0),
                "d1_retention": percent(row["d1_users"], users),
                "d3_users": int(row["d3_users"] or 0),
                "d3_retention": percent(row["d3_users"], users),
                "d7_users": int(row["d7_users"] or 0),
                "d7_retention": percent(row["d7_users"], users),
                "users_with_food": int(row["users_with_food"] or 0),
                "first_log_conv": percent(row["users_with_food"], users),
                "users_3plus_food": int(row["users_3plus_food"] or 0),
                "users_3plus_conv": percent(row["users_3plus_food"], users),
                "meal_logs": int(row["meal_logs"] or 0),
                "avg_logs_per_user": safe_div(row["meal_logs"], users),
            }

        return result

    def get_reminders_stats(self, days: int = 7) -> dict[str, Any]:
        stats = self._reminders_stats(days)
        stats.update(self._broadcast_stats(days))
        return stats

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
                            WHERE date(created_at, '+3 hours') >= date('now', '+3 hours', '-6 days')
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

    def _referral_stats(self, days: int | None) -> dict[str, Any]:
        registered_filter = self._date_filter("created_at", days)
        with self.db.connect() as conn:
            registered = conn.execute(
                f"SELECT COUNT(*) FROM referrals WHERE {registered_filter}"
            ).fetchone()[0]
            activated = conn.execute(
                f"""
                SELECT COUNT(*)
                FROM referrals
                WHERE activated_at IS NOT NULL AND {registered_filter}
                """
            ).fetchone()[0]
            inviters = conn.execute(
                f"""
                SELECT COUNT(DISTINCT inviter_user_id)
                FROM referrals
                WHERE {registered_filter}
                """
            ).fetchone()[0]
            active_inviters = conn.execute(
                f"""
                SELECT COUNT(DISTINCT inviter_user_id)
                FROM referrals
                WHERE activated_at IS NOT NULL AND {registered_filter}
                """
            ).fetchone()[0]
            milestone_row = conn.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN activated >= 1 THEN 1 ELSE 0 END), 0) AS one_plus,
                    COALESCE(SUM(CASE WHEN activated >= 5 THEN 1 ELSE 0 END), 0) AS five_plus,
                    COALESCE(SUM(CASE WHEN activated >= 10 THEN 1 ELSE 0 END), 0) AS ten_plus
                FROM (
                    SELECT inviter_user_id, COUNT(*) AS activated
                    FROM referrals
                    WHERE activated_at IS NOT NULL
                    GROUP BY inviter_user_id
                )
                """
            ).fetchone()

        return {
            "referral_inviters": int(inviters or 0),
            "referral_active_inviters": int(active_inviters or 0),
            "referral_registered": int(registered or 0),
            "referral_activated": int(activated or 0),
            "referral_activation_rate": percent(activated, registered),
            "referral_one_plus": int(milestone_row["one_plus"] or 0),
            "referral_five_plus": int(milestone_row["five_plus"] or 0),
            "referral_ten_plus": int(milestone_row["ten_plus"] or 0),
        }

    def _reminders_stats(self, days: int | None) -> dict[str, Any]:
        log_filter = self._date_filter("sent_at", days)
        conversion_log_filter = self._date_filter("l.sent_at", days)
        with self.db.connect() as conn:
            total_logs = conn.execute(
                "SELECT COUNT(*) FROM reminder_logs WHERE reminder_type != 'water'"
            ).fetchone()[0]
            sent = conn.execute(
                f"SELECT COUNT(*) FROM reminder_logs WHERE reminder_type != 'water' AND status = 'sent' AND {log_filter}",
            ).fetchone()[0]
            failed = conn.execute(
                f"SELECT COUNT(*) FROM reminder_logs WHERE reminder_type != 'water' AND status = 'failed' AND {log_filter}",
            ).fetchone()[0]
            converted = conn.execute(
                f"""
                SELECT COUNT(DISTINCT l.id)
                FROM reminder_logs l
                JOIN food_entries f ON f.user_id = l.user_id
                 AND datetime(f.created_at) >= datetime(l.sent_at)
                 AND datetime(f.created_at) <= datetime(l.sent_at, '+1 hour')
                WHERE l.status = 'sent'
                  AND l.reminder_type != 'water'
                  AND {conversion_log_filter}
                """,
            ).fetchone()[0]
            enabled = conn.execute("SELECT COUNT(*) FROM users WHERE reminder_time IS NOT NULL").fetchone()[0]
            slot_rows = conn.execute(
                f"""
                SELECT slot_group,
                       COUNT(*) AS sent_count,
                       SUM(converted) AS converted_count
                FROM (
                    SELECT l.id,
                           CASE
                               WHEN CAST(substr(COALESCE(l.slot, strftime('%H:%M', l.sent_at, '+3 hours')), 1, 2) AS INTEGER) < 12 THEN 'morning'
                               WHEN CAST(substr(COALESCE(l.slot, strftime('%H:%M', l.sent_at, '+3 hours')), 1, 2) AS INTEGER) < 18 THEN 'day'
                               ELSE 'evening'
                           END AS slot_group,
                           CASE WHEN EXISTS (
                               SELECT 1
                               FROM food_entries f
                               WHERE f.user_id = l.user_id
                                 AND datetime(f.created_at) >= datetime(l.sent_at)
                                 AND datetime(f.created_at) <= datetime(l.sent_at, '+1 hour')
                           ) THEN 1 ELSE 0 END AS converted
                    FROM reminder_logs l
                    WHERE l.reminder_type != 'water' AND l.status = 'sent' AND {conversion_log_filter}
                )
                GROUP BY slot_group
                """
            ).fetchall()
        has_data = int(total_logs or 0) > 0
        slot_cvr = {
            row["slot_group"]: percent(row["converted_count"], row["sent_count"])
            for row in slot_rows
            if int(row["sent_count"] or 0) > 0
        }
        best_slot, worst_slot = reminder_slot_extremes(slot_cvr)
        return {
            "reminders_enabled_total": int(enabled or 0),
            "reminders_sent": int(sent or 0),
            "reminders_failed": int(failed or 0),
            "reminder_converted": int(converted or 0),
            "reminder_cvr": percent(converted, sent),
            "reminders_has_data": has_data,
            "morning_cvr": format_slot_cvr(slot_cvr, "morning", has_data),
            "day_cvr": format_slot_cvr(slot_cvr, "day", has_data),
            "evening_cvr": format_slot_cvr(slot_cvr, "evening", has_data),
            "best_slot": best_slot if has_data else "нет данных",
            "worst_slot": worst_slot if has_data else "нет данных",
        }

    def _broadcast_stats(self, days: int | None) -> dict[str, Any]:
        log_filter = self._date_filter("sent_at", days)
        conversion_log_filter = self._date_filter("l.sent_at", days)
        with self.db.connect() as conn:
            total_logs = conn.execute("SELECT COUNT(*) FROM broadcast_logs").fetchone()[0]
            sent = conn.execute(
                f"SELECT COUNT(*) FROM broadcast_logs WHERE status = 'sent' AND {log_filter}",
            ).fetchone()[0]
            failed = conn.execute(
                f"SELECT COUNT(*) FROM broadcast_logs WHERE status != 'sent' AND {log_filter}",
            ).fetchone()[0]
            sent_users = conn.execute(
                f"SELECT COUNT(DISTINCT user_id) FROM broadcast_logs WHERE status = 'sent' AND {log_filter}",
            ).fetchone()[0]
            converted_users = conn.execute(
                f"""
                SELECT COUNT(DISTINCT l.user_id)
                FROM broadcast_logs l
                WHERE l.status = 'sent'
                  AND {conversion_log_filter}
                  AND EXISTS (
                      SELECT 1
                      FROM food_entries f
                      WHERE f.user_id = l.user_id
                        AND datetime(f.created_at) >= datetime(l.sent_at)
                        AND datetime(f.created_at) <= datetime(l.sent_at, '+24 hours')
                  )
                """,
            ).fetchone()[0]
            latest = conn.execute(
                """
                SELECT campaign_id, MAX(sent_at) AS sent_at
                FROM broadcast_logs
                GROUP BY campaign_id
                ORDER BY sent_at DESC
                LIMIT 1
                """
            ).fetchone()
            latest_campaign = latest["campaign_id"] if latest else ""
            latest_sent_at = latest["sent_at"] if latest else ""
            latest_sent = latest_failed = latest_sent_users = latest_converted = 0
            if latest_campaign:
                latest_sent = conn.execute(
                    "SELECT COUNT(*) FROM broadcast_logs WHERE campaign_id = ? AND status = 'sent'",
                    (latest_campaign,),
                ).fetchone()[0]
                latest_failed = conn.execute(
                    "SELECT COUNT(*) FROM broadcast_logs WHERE campaign_id = ? AND status != 'sent'",
                    (latest_campaign,),
                ).fetchone()[0]
                latest_sent_users = conn.execute(
                    "SELECT COUNT(DISTINCT user_id) FROM broadcast_logs WHERE campaign_id = ? AND status = 'sent'",
                    (latest_campaign,),
                ).fetchone()[0]
                latest_converted = conn.execute(
                    """
                    SELECT COUNT(DISTINCT l.user_id)
                    FROM broadcast_logs l
                    WHERE l.campaign_id = ?
                      AND l.status = 'sent'
                      AND EXISTS (
                          SELECT 1
                          FROM food_entries f
                          WHERE f.user_id = l.user_id
                            AND datetime(f.created_at) >= datetime(l.sent_at)
                            AND datetime(f.created_at) <= datetime(l.sent_at, '+24 hours')
                      )
                    """,
                    (latest_campaign,),
                ).fetchone()[0]

        has_data = int(total_logs or 0) > 0
        return {
            "broadcasts_has_data": has_data,
            "broadcast_sent": int(sent or 0),
            "broadcast_failed": int(failed or 0),
            "broadcast_sent_users": int(sent_users or 0),
            "broadcast_converted_users": int(converted_users or 0),
            "broadcast_cvr": percent(converted_users, sent_users),
            "latest_broadcast_id": latest_campaign,
            "latest_broadcast_at": latest_sent_at,
            "latest_broadcast_sent": int(latest_sent or 0),
            "latest_broadcast_failed": int(latest_failed or 0),
            "latest_broadcast_sent_users": int(latest_sent_users or 0),
            "latest_broadcast_converted_users": int(latest_converted or 0),
            "latest_broadcast_cvr": percent(latest_converted, latest_sent_users),
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
             AND date(f.created_at, '+3 hours') = date(u.created_at, '+3 hours', '+{offset} days')
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
            SELECT user_id, date(created_at, '+3 hours') AS day
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
            return f"date({column}, '+3 hours') = date('now', '+3 hours')"
        start = moscow_today() - timedelta(days=days - 1)
        return f"date({column}, '+3 hours') >= date('{start.isoformat()}')"

    @staticmethod
    def _previous_date_filter(column: str, days: int | None) -> str:
        if days is None:
            return "0 = 1"
        if days == 1:
            return f"date({column}, '+3 hours') = date('now', '+3 hours', '-1 day')"
        current_start = moscow_today() - timedelta(days=days - 1)
        previous_start = current_start - timedelta(days=days)
        return (
            f"date({column}, '+3 hours') >= date('{previous_start.isoformat()}') "
            f"AND date({column}, '+3 hours') < date('{current_start.isoformat()}')"
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
            "🤝 ПРИГЛАШЕНИЯ",
            f"Приглашавших пользователей: {stats['referral_inviters']}",
            f"Привели активных друзей: {stats['referral_active_inviters']}",
            f"Перешли по реферальной ссылке: {stats['referral_registered']}",
            f"Активировались: {stats['referral_activated']} ({fmt_percent(stats['referral_activation_rate'])})",
            "",
            "━━━━━━━━━━━━",
            "🔔 НАПОМИНАНИЯ",
            f"Пользователей с напоминаниями: {stats['reminders_enabled_total']}",
            f"Отправлено сегодня: {fmt_reminder_count(stats, 'reminders_sent')}",
            f"Не отправилось: {fmt_reminder_count(stats, 'reminders_failed')}",
            f"Сработало: {fmt_reminder_count(stats, 'reminder_converted')}",
            f"CVR: {fmt_reminder_percent(stats, 'reminder_cvr')}",
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
            "🤝 ПРИГЛАШЕНИЯ",
            f"Приглашавших пользователей: {stats['referral_inviters']}",
            f"Привели активных друзей: {stats['referral_active_inviters']}",
            f"Перешли по реферальной ссылке: {stats['referral_registered']}",
            f"Активировались: {stats['referral_activated']} ({fmt_percent(stats['referral_activation_rate'])})",
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
            f"Напоминаний отправлено: {fmt_reminder_count(stats, 'reminders_sent')}",
            f"Не отправилось: {fmt_reminder_count(stats, 'reminders_failed')}",
            f"Сработало: {fmt_reminder_count(stats, 'reminder_converted')}",
            f"CVR: {fmt_reminder_percent(stats, 'reminder_cvr')}",
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
            "🤝 ПРИГЛАШЕНИЯ",
            f"Приглашавших пользователей: {stats['referral_inviters']}",
            f"Привели активных друзей: {stats['referral_active_inviters']}",
            f"Всего переходов по реферальной ссылке: {stats['referral_registered']}",
            f"Всего активированных приглашённых: {stats['referral_activated']} ({fmt_percent(stats['referral_activation_rate'])})",
            f"Пригласили 1+ активного друга: {stats['referral_one_plus']}",
            f"Пригласили 5+ активных друзей: {stats['referral_five_plus']}",
            f"Пригласили 10+ активных друзей: {stats['referral_ten_plus']}",
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
            f"Всего отправлено напоминаний: {fmt_reminder_count(stats, 'reminders_sent')}",
            f"Всего не отправилось: {fmt_reminder_count(stats, 'reminders_failed')}",
            f"Всего сработало после напоминания: {fmt_reminder_count(stats, 'reminder_converted')}",
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


def format_channel_stats(stats: dict[str, Any]) -> str:
    channels = stats["channels"]

    def block(title: str, key: str) -> list[str]:
        row = channels[key]
        return [
            title,
            f"Пользователей: {row['users']}",
            f"D1: {fmt_percent(row['d1_retention'])} ({row['d1_users']})",
            f"D3: {fmt_percent(row['d3_retention'])} ({row['d3_users']})",
            f"D7: {fmt_percent(row['d7_retention'])} ({row['d7_users']})",
            f"Сделали лог еды: {row['users_with_food']} ({fmt_percent(row['first_log_conv'])})",
            f"Сделали 3+ лога: {row['users_3plus_food']} ({fmt_percent(row['users_3plus_conv'])})",
            f"Среднее логов на пользователя: {fmt_float(row['avg_logs_per_user'])}",
        ]

    lines = [
        "📱 НЯММЕТР — КАНАЛЫ",
        "",
        "Сравнение удержания по тому, где человек реально пользовался Нямметром.",
        "",
        "━━━━━━━━━━━━",
    ]
    lines.extend(block("🤖 ТОЛЬКО БОТ", "bot_only"))
    lines.extend(["", "━━━━━━━━━━━━"])
    lines.extend(block("📱 ТОЛЬКО МИНИАПП", "miniapp_only"))
    lines.extend(["", "━━━━━━━━━━━━"])
    lines.extend(block("🔁 БОТ + МИНИАПП", "mixed"))
    return "\n".join(lines)


def format_reminders_stats(stats: dict[str, Any]) -> str:
    return "\n".join(
        [
            "🔔 НЯММЕТР — НАПОМИНАНИЯ",
            "",
            f"Пользователей с напоминаниями: {stats['reminders_enabled_total']}",
            "",
            "За 7 дней:",
            f"Отправлено: {fmt_reminder_count(stats, 'reminders_sent')}",
            f"Не отправилось: {fmt_reminder_count(stats, 'reminders_failed')}",
            f"Сработало: {fmt_reminder_count(stats, 'reminder_converted')}",
            f"Конверсия: {fmt_reminder_percent(stats, 'reminder_cvr')}",
            "",
            "По слотам:",
            f"Утро: {stats['morning_cvr']}",
            f"День: {stats['day_cvr']}",
            f"Вечер: {stats['evening_cvr']}",
            "",
            f"Лучший слот: {stats['best_slot']}",
            f"Худший слот: {stats['worst_slot']}",
            "",
            "━━━━━━━━━━━━",
            "📣 ПУШИ",
            "",
            "За 7 дней:",
            f"Отправлено: {fmt_broadcast_count(stats, 'broadcast_sent')}",
            f"Не отправилось: {fmt_broadcast_count(stats, 'broadcast_failed')}",
            f"Добавили еду за 24ч: {fmt_broadcast_count(stats, 'broadcast_converted_users')}",
            f"CVR: {fmt_broadcast_percent(stats, 'broadcast_cvr')}",
            "",
            "Последняя рассылка:",
            f"ID: {stats['latest_broadcast_id'] or 'нет данных'}",
            f"Отправлено: {fmt_broadcast_count(stats, 'latest_broadcast_sent')}",
            f"Не отправилось: {fmt_broadcast_count(stats, 'latest_broadcast_failed')}",
            f"Добавили еду за 24ч: {fmt_broadcast_count(stats, 'latest_broadcast_converted_users')}",
            f"CVR: {fmt_broadcast_percent(stats, 'latest_broadcast_cvr')}",
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


def safe_div(numerator: Any, denominator: Any) -> float:
    denominator = float(denominator or 0)
    return round(float(numerator or 0) / denominator, 2) if denominator else 0.0


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


def fmt_reminder_count(stats: dict[str, Any], key: str) -> str:
    if not stats.get("reminders_has_data"):
        return "нет данных"
    return str(stats.get(key, 0))


def fmt_reminder_percent(stats: dict[str, Any], key: str) -> str:
    if not stats.get("reminders_has_data"):
        return "нет данных"
    return fmt_percent(stats.get(key, 0))


def fmt_broadcast_count(stats: dict[str, Any], key: str) -> str:
    if not stats.get("broadcasts_has_data"):
        return "нет данных"
    return str(int(stats.get(key, 0) or 0))


def fmt_broadcast_percent(stats: dict[str, Any], key: str) -> str:
    if not stats.get("broadcasts_has_data"):
        return "нет данных"
    return fmt_percent(stats.get(key, 0))


def format_slot_cvr(slot_cvr: dict[str, float], slot: str, has_data: bool) -> str:
    if not has_data or slot not in slot_cvr:
        return "нет данных"
    return fmt_percent(slot_cvr[slot])


def reminder_slot_extremes(slot_cvr: dict[str, float]) -> tuple[str, str]:
    if not slot_cvr:
        return "нет данных", "нет данных"
    labels = {"morning": "утро", "day": "день", "evening": "вечер"}
    best_key = max(slot_cvr, key=slot_cvr.get)
    worst_key = min(slot_cvr, key=slot_cvr.get)
    return (
        f"{labels.get(best_key, best_key)} ({fmt_percent(slot_cvr[best_key])})",
        f"{labels.get(worst_key, worst_key)} ({fmt_percent(slot_cvr[worst_key])})",
    )


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
