"""
plugins/productivity/productivity_stats.py — Статистика продуктивности.

Считает за последние 7 дней:
  • Выполненные задачи (из tasks плагина)
  • Выполненные рутины
  • Выполненные привычки (streak)
  • Сводка по дням недели

API:
    ps = ProductivityStats()
    summary = ps.last_week()
    streak = ps.calc_streak(habit_history)
"""

from __future__ import annotations

from datetime import datetime, timedelta

from plugins import CorePlugin
from plugins.core.tasks import TasksPlugin
from plugins.productivity.routines import RoutinesPlugin


class ProductivityStats(CorePlugin):
    name = "productivity_stats"

    def __init__(self) -> None:
        super().__init__()

    def last_week(self) -> dict:
        """Возвращает статистику за последние 7 дней."""
        now = datetime.now()
        week_ago = now - timedelta(days=7)

        # Задачи
        try:
            tasks_plugin = TasksPlugin()
            done_tasks = []
            for t in tasks_plugin.list_all(status="done"):
                try:
                    upd = datetime.fromisoformat(t.get("updated_at", ""))
                    if upd >= week_ago:
                        done_tasks.append(t)
                except (ValueError, KeyError):
                    continue
        except Exception:
            done_tasks = []

        # Рутины
        try:
            routines_plugin = RoutinesPlugin()
            routine_completions = 0
            for r in routines_plugin.list_all():
                for ts in r.get("history", []):
                    try:
                        if datetime.fromisoformat(ts) >= week_ago:
                            routine_completions += 1
                    except ValueError:
                        continue
        except Exception:
            routine_completions = 0

        # Привычки
        try:
            from plugins.productivity.habit_checklist import HabitChecklist
            habits_plugin = HabitChecklist()
            habit_completions = 0
            for h in habits_plugin.list_all():
                for ts in h.get("history", []):
                    try:
                        if datetime.fromisoformat(ts) >= week_ago:
                            habit_completions += 1
                    except ValueError:
                        continue
        except Exception:
            habit_completions = 0

        # По дням недели
        by_day: dict[str, int] = {}
        for t in done_tasks:
            try:
                day = datetime.fromisoformat(t["updated_at"]).strftime("%a")
                by_day[day] = by_day.get(day, 0) + 1
            except (ValueError, KeyError):
                continue

        return {
            "period": "7 дней",
            "tasks_completed": len(done_tasks),
            "routines_completed": routine_completions,
            "habits_completed": habit_completions,
            "by_day": by_day,
            "total_actions": len(done_tasks) + routine_completions + habit_completions,
        }

    def calc_streak(self, history: list[str]) -> int:
        """Считает текущий streak (дней подряд) по списку timestamps."""
        if not history:
            return 0
        try:
            dates = sorted({
                datetime.fromisoformat(ts).date() for ts in history
            }, reverse=True)
        except ValueError:
            return 0

        if not dates:
            return 0

        today = datetime.now().date()
        # Streak считается, если последняя отметка была сегодня или вчера
        if dates[0] < today - timedelta(days=1):
            return 0

        streak = 1
        for i in range(1, len(dates)):
            if (dates[i - 1] - dates[i]).days == 1:
                streak += 1
            else:
                break
        return streak

    def format_summary(self) -> str:
        s = self.last_week()
        lines = [
            "📊 **Статистика за неделю:**\n",
            f"• ✅ Задач выполнено: **{s['tasks_completed']}**",
            f"• 🔁 Рутин выполнено: **{s['routines_completed']}**",
            f"• 🎯 Привычек: **{s['habits_completed']}**",
            f"• 📈 Всего действий: **{s['total_actions']}**",
        ]
        if s["by_day"]:
            lines.append("\n**По дням:**")
            for day, count in sorted(s["by_day"].items()):
                lines.append(f"  • {day}: {count}")
        return "\n".join(lines)
