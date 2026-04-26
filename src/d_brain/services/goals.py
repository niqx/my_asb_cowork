"""Goals service — weekly goals review state management.

Manages the "pending goals review" lifecycle:
  1. reflect_finalize.py writes a flag file after generating next week's goals
  2. Bot voice/text handlers append user corrections to goals-corrections.md
  3. /approve applies corrections (or accepts as-is) and clears the flag
"""

import json
import logging
import urllib.request
from datetime import datetime, date, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

_PENDING_FILENAME = "{week}-goals-pending.json"
_CORRECTIONS_FILENAME = "{week}-goals-corrections.md"


class GoalsService:
    """Service for managing weekly goals review state."""

    def __init__(self, vault_path: Path | str) -> None:
        self.summaries_dir = Path(vault_path) / "summaries"
        self.summaries_dir.mkdir(parents=True, exist_ok=True)
        self.goals_path = Path(vault_path) / "goals" / "3-weekly.md"

    def _flag_path(self, week: str) -> Path:
        return self.summaries_dir / _PENDING_FILENAME.format(week=week)

    def _corrections_path(self, week: str) -> Path:
        return self.summaries_dir / _CORRECTIONS_FILENAME.format(week=week)

    def start(self, week: str) -> None:
        """Create flag file to mark goals review as pending."""
        flag = {
            "week": week,
            "started": datetime.now().astimezone().isoformat(),
        }
        self._flag_path(week).write_text(
            json.dumps(flag, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        corrections = self._corrections_path(week)
        if not corrections.exists():
            corrections.write_text(
                f"# Правки к целям недели {week}\n\n", encoding="utf-8"
            )
        logger.info("Goals review started for week %s", week)

    def get_pending_week(self) -> str | None:
        """Return the current pending week ID, or None if no goals review is pending."""
        for path in self.summaries_dir.glob("*-goals-pending.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                week = data.get("week", "")
                if week:
                    return week
            except Exception:
                continue
        return None

    def append_correction(self, week: str, text: str, source: str = "voice") -> None:
        """Append a user correction to the goals corrections file."""
        corrections_path = self._corrections_path(week)
        ts = datetime.now().strftime("%H:%M")
        entry = f"\n## {ts} [{source}]\n{text}\n"
        with corrections_path.open("a", encoding="utf-8") as f:
            f.write(entry)
        logger.info("Goals correction appended for week %s (%s)", week, source)

    def has_corrections(self, week: str) -> bool:
        """Return True if the corrections file has actual user content."""
        corrections_path = self._corrections_path(week)
        if not corrections_path.exists():
            return False
        content = corrections_path.read_text(encoding="utf-8")
        lines = [ln for ln in content.splitlines() if ln.strip() and not ln.startswith("#")]
        return len(lines) > 0

    def get_corrections_path(self, week: str) -> Path:
        """Return path to the corrections file."""
        return self._corrections_path(week)

    def clear(self, week: str) -> None:
        """Remove the pending flag file."""
        flag = self._flag_path(week)
        if flag.exists():
            flag.unlink()
            logger.info("Goals flag cleared for week %s", week)


def detect_weekend_overdue(todoist_api_key: str) -> int:
    """If today is Sat/Sun and process-goal tasks are overdue, reschedule them to next Monday.

    Returns the number of tasks rescheduled, or 0 if not a weekend / nothing overdue.
    """
    today = date.today()
    if today.weekday() not in (5, 6):  # 5=Saturday, 6=Sunday
        return 0

    days_until_monday = (7 - today.weekday()) % 7 or 7
    next_monday = (today + timedelta(days=days_until_monday)).isoformat()

    headers = {
        "Authorization": f"Bearer {todoist_api_key}",
        "Content-Type": "application/json",
    }

    # Fetch overdue process-goal tasks
    filter_query = "overdue & label:process-goal"
    url = f"https://api.todoist.com/rest/v2/tasks?filter={urllib.request.quote(filter_query)}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            tasks = json.loads(resp.read().decode())
    except Exception as e:
        logger.error("detect_weekend_overdue: failed to fetch tasks: %s", e)
        return 0

    if not tasks:
        return 0

    rescheduled = 0
    for task in tasks:
        task_id = task["id"]
        payload = json.dumps({"due_date": next_monday}).encode()
        update_req = urllib.request.Request(
            f"https://api.todoist.com/rest/v2/tasks/{task_id}",
            data=payload,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(update_req, timeout=15):
                pass
            rescheduled += 1
            logger.info(
                "detect_weekend_overdue: rescheduled task %s to %s", task_id, next_monday
            )
        except Exception as e:
            logger.error(
                "detect_weekend_overdue: failed to reschedule task %s: %s", task_id, e
            )

    return rescheduled
