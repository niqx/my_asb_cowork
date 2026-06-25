"""Headless pipeline entrypoint for the nightly / morning cron scripts.

Replaces the old `claude --print -p` phase calls in process.sh / morning.sh:
every model turn now runs through the shared persistent interactive session
(subscription billing). The surrounding shell shims still handle graph rebuild,
memory decay, MOC, git, Telegram delivery, d-doctor context and JSON/HTML
post-processing — ONLY the `claude` invocation moves here.

Usage:
    uv run python -m d_brain.pipeline capture    # → JSON to stdout
    uv run python -m d_brain.pipeline execute     # → JSON to stdout
    uv run python -m d_brain.pipeline reflect     # → HTML report to stdout
    uv run python -m d_brain.pipeline daily       # monolith fallback → HTML
    uv run python -m d_brain.pipeline morning      # morning briefing → HTML
    echo "PROMPT" | uv run python -m d_brain.pipeline ask [--no-wrap]

The session runs with cwd = vault, so prompts use vault-relative paths exactly
as the old `cd "$VAULT_DIR"; claude --print …` calls did.

Dynamic context the shell computes is passed in via env / .session files:
    TODAY                       (env)  — date string; defaults to today
    CURRENT_CITY, LOCATION_TZ,
    WEEKDAY                     (env)  — morning briefing
    .session/morning_context.txt       — weather + news block ($CONTEXT)
    .session/log_errors.txt            — journalctl errors for REFLECT
    .session/reflect_extra.md          — d-doctor health/nutrition sections

Exit code 0 on success, 1 otherwise.
"""

import logging
import os
import sys
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)


def _today() -> str:
    return os.environ.get("TODAY") or date.today().isoformat()


def _yearly_goals_name(vault: Path) -> str:
    matches = sorted(vault.glob("goals/1-yearly-*.md"))
    return matches[-1].name if matches else "1-yearly.md"


def _read_session_file(vault: Path, name: str) -> str:
    f = vault / ".session" / name
    try:
        return f.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError):
        return ""


# ── prompt builders (verbatim from the old process.sh / morning.sh) ─────────

def _capture_prompt(vault: Path, today: str) -> str:
    yearly = _yearly_goals_name(vault)
    return (
        f"Today is {today}. Read .claude/skills/dbrain-processor/phases/capture.md "
        f"and execute Phase 1.\n"
        f"Read daily/{today}.md, goals/3-weekly.md, goals/2-monthly.md, goals/{yearly}.\n"
        f"Classify each entry. Return ONLY JSON."
    )


def _execute_prompt(today: str) -> str:
    return (
        f"Today is {today}. Read .claude/skills/dbrain-processor/phases/execute.md "
        f"and execute Phase 2.\n"
        f"Read .session/capture.json for input data.\n"
        f"Create tasks in Todoist via mcp-cli (Bash tool), save thoughts, build links. "
        f"Return ONLY JSON.\n\n"
        f"CRITICAL: Use Bash tool to call mcp-cli for Todoist operations.\n"
        f"Example: mcp-cli call todoist find-tasks-by-date '{{\"startDate\": \"today\"}}'\n"
        f"mcp-cli may take 10-30 sec on first call (server startup). Retry 3x on error."
    )


def _reflect_prompt(vault: Path, today: str) -> str:
    log_errors = _read_session_file(vault, "log_errors.txt")
    extra = _read_session_file(vault, "reflect_extra.md")
    extra_block = f"\n{extra}\n" if extra else ""
    return (
        f"Today is {today}. Read .claude/skills/dbrain-processor/phases/reflect.md "
        f"and execute Phase 3.\n"
        f"Read .session/capture.json and .session/execute.json for input data.\n"
        f"Read MEMORY.md, .session/handoff.md.\n"
        f"Generate HTML report, update MEMORY, record observations.\n\n"
        f"SYSTEM LOGS (last 24h, may be empty):\n{log_errors}\n\n"
        f"AGENT NOTES TASK: Scan the input text for:\n"
        f"1. User complaints about the bot (not found, failed, broken, did not save) - "
        f"add to vault/agent/agent_notes.md section \"Проблемы из рефлексии\" with id: r-{{DATE}}-NNN\n"
        f"2. Ideas for new automations (would be convenient, want a command, should automate) - "
        f"add to \"Идеи агента\" with id: a-{{DATE}}-NNN\n"
        f"3. If LOG_ERRORS is not empty - add brief error summary to \"Системные ошибки\" "
        f"with id: e-{{DATE}}-NNN (skip if already logged today)\n"
        f"4. If you notice friction patterns or repetitive actions - add 1-2 ideas to \"Идеи агента\"\n\n"
        f"Format for each agent_notes.md entry:\n"
        f"- `[ ]` **[source]** description <!-- id: X-YYYYMMDD-NNN -->\n\n"
        f"FORMATTING RULES (mandatory for Telegram report):\n"
        f"- Tasks: ONLY name + priority + due date. NEVER include task ID (like abc123xyz).\n"
        f"- Thoughts: read each saved file H1 heading, show title in RUSSIAN. NO [[wikilink]] syntax.\n"
        f"- New links: plain note names without [[ ]] brackets.\n"
        f"{extra_block}\n"
        f"Return ONLY RAW HTML (for Telegram)."
    )


def _reflect_strict_prompt(today: str) -> str:
    """Retry used when REFLECT returned no HTML — forces a Telegram-HTML-only reply."""
    return (
        f"Today is {today}. Read .session/capture.json and .session/execute.json. "
        f"Generate ONLY a Telegram HTML report following "
        f".claude/skills/dbrain-processor/phases/reflect.md. "
        f"Do NOT write any files. Do NOT explain what you did. "
        f"Output MUST start with <b> tag and use only <b>,<i>,<code>,<s>,<u>,<a> tags. "
        f"No markdown. Max 4096 chars."
    )


def _daily_monolith_prompt(today: str) -> str:
    """Fallback used when CAPTURE produced no valid entries."""
    return (
        f"Today is {today}. TIME: 23:00. EVENING DAILY PROCESSING.\n\n"
        f"USE ONLY: dbrain-processor skill. Output template: Обработка за {{DATE}}\n"
        f"DO NOT use morning-briefer skill. DO NOT generate morning briefing.\n\n"
        f"TASK: Process todays voice/text entries -> classify -> create Todoist tasks "
        f"-> save thoughts -> generate evening HTML report."
    )


def _morning_prompt(vault: Path, today: str) -> str:
    weekday = os.environ.get("WEEKDAY", "")
    city = os.environ.get("CURRENT_CITY", "Москва")
    tz = os.environ.get("LOCATION_TZ", "Europe/Moscow")
    context = _read_session_file(vault, "morning_context.txt")
    return (
        f"User's current location: {city} (timezone: {tz}).\n"
        f"Today is {today} ({weekday}). Generate morning briefing according to "
        f"morning-briefer skill.\n\n"
        f"=== CONTEXT FOR TODAY ===\n{context}\n\n"
        f"=== INSTRUCTIONS ===\n"
        f"1. Read MEMORY.md, goals/3-weekly.md, goals/2-monthly.md\n"
        f"2. Read daily logs for last 2 days\n"
        f"3. Call mcp__todoist__find-tasks-by-date for today\n"
        f"4. Call mcp__todoist__find-tasks to get overdue tasks\n"
        f"5. Generate HTML briefing using morning-briefer skill template\n\n"
        f"CRITICAL: Return RAW HTML only. No markdown. No explanations."
    )


def _work_insights_prompt(today: str) -> str:
    """Weekly work insights: patterns, hanging commitments, risks from digest/ last 7 days.

    Registered as "work-insights" mode. NOT auto-scheduled — activate manually.
    """
    work_dir = Path.home() / ".dbrain" / "work"
    return (
        f"Сегодня {today}. Сформируй сводку рабочих инсайтов за последние 7 дней.\n\n"
        f"БАЗА РАБОЧЕГО КОНТЕКСТА: {work_dir}\n\n"
        f"ЗАДАЧА:\n"
        f"1. Прочитай {work_dir}/index.md — найди материалы за последние 7 дней\n"
        f"2. Прочитай соответствующие дайджесты из {work_dir}/digest/ "
        f"(используй Glob/Read для перечисления файлов)\n"
        f"3. Прочитай {work_dir}/commitments.md — выдели незакрытые договорённости\n"
        f"4. Выяви паттерны: повторяющиеся темы, тренды в метриках, системные риски\n"
        f"5. Сформируй HTML-сводку\n\n"
        f"ФОРМАТ (строго Telegram HTML: только теги <b>,<i>,<code>,<s>,<u>):\n"
        f"💡 <b>Инсайты недели</b>\n\n"
        f"Секция «Ключевые метрики»: тренды и изменения по данным из дайджестов\n"
        f"Секция «Висящие договорённости»: кто — что — срок (из commitments.md)\n"
        f"Секция «Риски»: что требует внимания по материалам недели\n"
        f"Секция «Паттерны»: повторяющиеся темы и наблюдения\n\n"
        f"ПРАВИЛА:\n"
        f"- Если материалов нет — укажи «Нет материалов за последние 7 дней»\n"
        f"- Если commitments.md не существует — пропусти секцию договорённостей\n"
        f"- Только raw HTML, без markdown\n"
        f"- Начни строго с 💡 <b>Инсайты недели</b>"
    )


def _nutrition_prompt(today: str) -> str:
    """Night health report: food summary from daily note + Oura data."""
    kcal = os.environ.get("NUTRITION_DAILY_KCAL", "2650")
    protein = os.environ.get("NUTRITION_DAILY_PROTEIN", "180")
    fat = os.environ.get("NUTRITION_DAILY_FAT", "80")
    carbs = os.environ.get("NUTRITION_DAILY_CARBS", "303")
    return (
        f"Сегодня {today}. Сформируй ночной отчёт о здоровье.\n\n"
        f"Суточные нормы пользователя: {kcal} ккал | Б:{protein}г Ж:{fat}г У:{carbs}г\n\n"
        f"ЗАДАЧА:\n"
        f"1. Прочитай daily/{today}.md — найди все строки с тегом [food], "
        f"просуммируй КБЖУ за день (калории, белки, жиры, углеводы)\n"
        f"2. Проверь доступность Oura: вызови mcp__oura__user-info или аналогичный tool. "
        f"Если доступен — запроси данные за сон и активность за сегодня ({today})\n"
        f"3. Сравни итог КБЖУ с нормами\n"
        f"4. Сформируй HTML-отчёт\n\n"
        f"ФОРМАТ ОТЧЁТА (строго Telegram HTML: только теги <b>,<i>,<code>,<s>,<u>):\n"
        f"🌙 <b>Здоровье за день</b>\n\n"
        f"Секция «Еда»: итог КБЖУ vs норма + краткая оценка\n"
        f"Секция «Сон»: часы и качество из Oura (или «Oura: нет данных за сегодня»)\n"
        f"Секция «Активность»: шаги/калории из Oura (или «Oura: нет данных за сегодня»)\n\n"
        f"ПРАВИЛА:\n"
        f"- Если записей [food] нет — укажи «Еда: записей не было»\n"
        f"- Если Oura недоступен или вернул ошибку — укажи «Oura: нет данных за сегодня», не прерывай работу\n"
        f"- Только raw HTML, без markdown, без объяснений перед тегами\n"
        f"- Начни строго с 🌙 <b>Здоровье за день</b>"
    )


# ── runner ──────────────────────────────────────────────────────────────────

def run(cmd: str, *, stdin_text: str = "", wrap_override: bool | None = None) -> tuple[str, bool]:
    """Build the prompt for `cmd`, ask the shared session, return (text, ok)."""
    from d_brain.config import get_settings
    from d_brain.services.runtime import get_session

    settings = get_settings()
    vault = settings.vault_path
    today = _today()

    # (prompt, wrap) per command. JSON/HTML phases use wrap=True (clean reply
    # between markers); the shell still post-processes via extract_json.py /
    # clean_claude_output.
    builders = {
        "capture": (_capture_prompt(vault, today), True),
        "execute": (_execute_prompt(today), True),
        "reflect": (_reflect_prompt(vault, today), True),
        "reflect-strict": (_reflect_strict_prompt(today), True),
        "daily": (_daily_monolith_prompt(today), True),
        "morning": (_morning_prompt(vault, today), True),
        "nutrition": (_nutrition_prompt(today), True),
        "work-insights": (_work_insights_prompt(today), True),
    }

    if cmd == "ask":
        prompt = stdin_text
        wrap = True if wrap_override is None else wrap_override
        if not prompt.strip():
            return "empty prompt on stdin", False
    elif cmd in builders:
        prompt, wrap = builders[cmd]
        if wrap_override is not None:
            wrap = wrap_override
    else:
        return f"unknown command: {cmd}", False

    session = get_session(settings)
    res = session.ask(prompt, request_id=f"maint-{cmd}", wrap=wrap)
    if res.ok:
        return (res.reply or ""), True
    return (res.detail or f"pipeline {cmd} failed: {res.status}"), False


def main() -> None:  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    argv = sys.argv[1:]
    cmd = argv[0] if argv else "daily"
    wrap_override: bool | None = None
    if "--no-wrap" in argv:
        wrap_override = False
    stdin_text = ""
    if cmd == "ask" and not sys.stdin.isatty():
        stdin_text = sys.stdin.read()
    text, ok = run(cmd, stdin_text=stdin_text, wrap_override=wrap_override)
    print(text)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
