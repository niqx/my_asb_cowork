"""Claude processing service.

ASB v3.0 billing migration: every model turn now goes through the shared
persistent interactive tmux session (``ClaudeSession.ask``) instead of a
headless ``claude --print`` subprocess — interactive usage stays on the
subscription. The public return shape ``{report|error, processed_entries}`` is
preserved so the calling handlers (do/edit/fix/process/weekly) need no change.
"""

import logging
from datetime import date
from pathlib import Path
from typing import Any, Optional

from d_brain.services.session import SessionStore

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 1200  # 20 minutes

# Human-readable Russian status when the session can't answer.
_STATUS_MESSAGES = {
    "rate_limited": "Достигнут лимит подписки Claude — попробуй позже.",
    "logged_out": "Claude Code разлогинен на сервере — нужно заново войти (claude login).",
    "timeout": "Claude не ответил вовремя (таймаут).",
    "error": "Ошибка сессии Claude.",
}


class ClaudeProcessor:
    """Service for triggering Claude Code processing via the shared session."""

    def __init__(
        self,
        vault_path: Path,
        todoist_api_key: str = "",
        *,
        session: Any = None,
    ) -> None:
        self.vault_path = Path(vault_path)
        self.todoist_api_key = todoist_api_key
        self._mcp_config_path = (self.vault_path.parent / "mcp-config.json").resolve()
        # May be None when constructed via the legacy positional ctor
        # (ClaudeProcessor(vault, key)); resolved lazily in _ask().
        self._session = session

    def _get_session(self) -> Any:
        if self._session is None:
            from d_brain.config import get_settings
            from d_brain.services.runtime import get_session

            self._session = get_session(get_settings())
        return self._session

    def _ask(
        self,
        prompt: str,
        *,
        wrap: bool = True,
        request_id: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> dict[str, Any]:
        """Send a prompt to the persistent session; map AskResult → report dict."""
        try:
            session = self._get_session()
            res = session.ask(
                prompt, timeout=timeout, request_id=request_id, wrap=wrap
            )
        except Exception as e:  # noqa: BLE001 — must never crash the caller
            logger.exception("session ask failed")
            return {"error": str(e), "processed_entries": 0}

        if res.ok:
            return {
                "report": self._clean_output(res.reply or ""),
                "processed_entries": 1,
            }
        logger.error("session ask non-ok: %s %s", res.status, res.detail)
        msg = _STATUS_MESSAGES.get(res.status, res.detail or "Ошибка обработки")
        return {"error": msg, "processed_entries": 0}

    def _load_skill_content(self) -> str:
        """Load dbrain-processor skill content for inclusion in prompt.

        NOTE: @vault/ references don't work in --print mode,
        so we must include skill content directly in the prompt.
        """
        skill_path = self.vault_path / ".claude/skills/dbrain-processor/SKILL.md"
        if skill_path.exists():
            return skill_path.read_text()
        return ""

    def _load_todoist_reference(self) -> str:
        """Load Todoist reference for inclusion in prompt."""
        ref_path = self.vault_path / ".claude/skills/dbrain-processor/references/todoist.md"
        if ref_path.exists():
            return ref_path.read_text()
        return ""

    def _get_session_context(self, user_id: int) -> str:
        """Get today's session context for Claude.

        Args:
            user_id: Telegram user ID

        Returns:
            Recent session entries formatted for inclusion in prompt.
        """
        if user_id == 0:
            return ""

        session = SessionStore(self.vault_path)
        today_entries = session.get_today(user_id)
        if not today_entries:
            return ""

        lines = ["=== TODAY'S SESSION ==="]
        for entry in today_entries[-10:]:
            ts = entry.get("ts", "")[11:16]  # HH:MM from ISO
            entry_type = entry.get("type", "unknown")
            text = entry.get("text", "")[:80]
            if text:
                lines.append(f"{ts} [{entry_type}] {text}")
        lines.append("=== END SESSION ===\n")
        return "\n".join(lines)

    def _clean_output(self, output: str) -> str:
        """Strip Claude artifacts: '---' wrappers and known preamble phrases.

        Claude sometimes wraps its HTML output like:
            HTML для Telegram
            ---
            <actual content>
            ---
            Готовые HTML для вставки в Телеграм

        Or adds a preamble like:
            Теперь генерируя финальный HTML отчет
            <actual content>
        """
        import re

        text = output.strip()

        # Strip --- separator wrapper: take content between first and last ---
        if "\n---\n" in text or text.startswith("---\n"):
            lines = text.split("\n")
            sep_indices = [i for i, ln in enumerate(lines) if ln.strip() == "---"]
            if len(sep_indices) >= 2:
                text = "\n".join(lines[sep_indices[0] + 1 : sep_indices[-1]]).strip()
            elif len(sep_indices) == 1:
                idx = sep_indices[0]
                before = "\n".join(lines[:idx]).strip()
                after = "\n".join(lines[idx + 1 :]).strip()
                if re.match(r"^[📅📊✅❌<🧠📝✨💡🎯🪞]", before):
                    # before IS the HTML content — discard Claude commentary after ---
                    text = before
                else:
                    # before is preamble — use what's after ---
                    text = after

        # Strip known preamble lines (Claude commentary before the actual HTML)
        preamble_patterns = [
            r"^Теперь генерирую финальный HTML[ -]отчёт[.:\s]*",
            r"^Теперь генерирую финальный HTML[ -]отчет[.:\s]*",
            r"^Теперь генерируя финальный HTML[ -]отчёт[.:\s]*",
            r"^Теперь генерируя финальный HTML[ -]отчет[.:\s]*",
            r"^HTML для Telegram[:\s]*",
            r"^Вот HTML для Telegram[:\s]*",
            r"^Вот сырой HTML для Telegram[:\s]*",
            r"^Вот готовый HTML[:\s]*",
            r"^Готовые HTML для вставки в Телеграм[:\s]*",
            r"^HTML отчёт[:\s]*",
            r"^HTML отчет[:\s]*",
        ]
        for pattern in preamble_patterns:
            text = re.sub(pattern, "", text, flags=re.IGNORECASE).strip()

        return text

    def _html_to_markdown(self, html: str) -> str:
        """Convert Telegram HTML to Obsidian Markdown."""
        import re

        text = html
        # <b>text</b> → **text**
        text = re.sub(r"<b>(.*?)</b>", r"**\1**", text)
        # <i>text</i> → *text*
        text = re.sub(r"<i>(.*?)</i>", r"*\1*", text)
        # <code>text</code> → `text`
        text = re.sub(r"<code>(.*?)</code>", r"`\1`", text)
        # <s>text</s> → ~~text~~
        text = re.sub(r"<s>(.*?)</s>", r"~~\1~~", text)
        # Remove <u> (no Markdown equivalent, just keep text)
        text = re.sub(r"</?u>", "", text)
        # <a href="url">text</a> → [text](url)
        text = re.sub(r'<a href="([^"]+)">([^<]+)</a>', r"[\2](\1)", text)

        return text

    def _save_weekly_summary(self, report_html: str, week_date: date) -> Path:
        """Save weekly summary to vault/summaries/YYYY-WXX-summary.md."""
        # Calculate ISO week number
        year, week, _ = week_date.isocalendar()
        filename = f"{year}-W{week:02d}-summary.md"
        summary_path = self.vault_path / "summaries" / filename

        # Convert HTML to Markdown for Obsidian
        content = self._html_to_markdown(report_html)

        # Add frontmatter
        frontmatter = f"""---
date: {week_date.isoformat()}
type: weekly-summary
week: {year}-W{week:02d}
---

"""
        summary_path.write_text(frontmatter + content)
        logger.info("Weekly summary saved to %s", summary_path)
        return summary_path

    def _update_weekly_moc(self, summary_path: Path) -> None:
        """Add link to new summary in MOC-weekly.md."""
        moc_path = self.vault_path / "MOC" / "MOC-weekly.md"
        if moc_path.exists():
            content = moc_path.read_text()
            link = f"- [[summaries/{summary_path.name}|{summary_path.stem}]]"
            # Insert after "## Previous Weeks" if not already there
            if summary_path.stem not in content:
                content = content.replace(
                    "## Previous Weeks\n",
                    f"## Previous Weeks\n\n{link}\n",
                )
                moc_path.write_text(content)
                logger.info("Updated MOC-weekly.md with link to %s", summary_path.stem)

    def process_daily(self, day: date | None = None) -> dict[str, Any]:
        """Process daily file with Claude.

        Args:
            day: Date to process (default: today)

        Returns:
            Processing report as dict
        """
        if day is None:
            day = date.today()

        daily_file = self.vault_path / "daily" / f"{day.isoformat()}.md"

        if not daily_file.exists():
            logger.warning("No daily file for %s", day)
            return {
                "error": f"No daily file for {day}",
                "processed_entries": 0,
            }

        # Load skill content directly (@ references don't work in --print mode)
        skill_content = self._load_skill_content()

        prompt = f"""Сегодня {day}. Выполни ежедневную обработку.

=== SKILL INSTRUCTIONS ===
{skill_content}
=== END SKILL ===

ПЕРВЫМ ДЕЛОМ: вызови mcp__todoist__user-info чтобы убедиться что MCP работает.

CRITICAL MCP RULE:
- ТЫ ИМЕЕШЬ ДОСТУП к mcp__todoist__* tools — ВЫЗЫВАЙ ИХ НАПРЯМУЮ
- НИКОГДА не пиши "MCP недоступен" или "добавь вручную"
- Для задач: вызови mcp__todoist__add-tasks tool
- Если tool вернул ошибку — покажи ТОЧНУЮ ошибку в отчёте

CRITICAL OUTPUT FORMAT:
- Return ONLY raw HTML for Telegram (parse_mode=HTML)
- NO markdown: no **, no ## , no ```, no tables
- Start directly with 📊 <b>Обработка за {day}</b>
- Allowed tags: <b>, <i>, <code>, <s>, <u>
- If entries already processed, return status report in same HTML format"""

        return self._ask(prompt, wrap=True, request_id=f"maint-daily-{day.isoformat()}")

    def execute_prompt(self, user_prompt: str, user_id: int = 0) -> dict[str, Any]:
        """Execute arbitrary prompt with Claude.

        Args:
            user_prompt: User's natural language request
            user_id: Telegram user ID for session context

        Returns:
            Execution report as dict
        """
        today = date.today()

        # Load context
        todoist_ref = self._load_todoist_reference()
        session_context = self._get_session_context(user_id)

        prompt = f"""Ты - персональный ассистент d-brain.

CONTEXT:
- Текущая дата: {today}
- Vault path: {self.vault_path}

{session_context}=== TODOIST REFERENCE ===
{todoist_ref}
=== END REFERENCE ===

ПЕРВЫМ ДЕЛОМ: вызови mcp__todoist__user-info чтобы убедиться что MCP работает.

CRITICAL MCP RULE:
- ТЫ ИМЕЕШЬ ДОСТУП к mcp__todoist__* tools — ВЫЗЫВАЙ ИХ НАПРЯМУЮ
- НИКОГДА не пиши "MCP недоступен" или "добавь вручную"
- Если tool вернул ошибку — покажи ТОЧНУЮ ошибку в отчёте

USER REQUEST:
{user_prompt}

CRITICAL OUTPUT FORMAT:
- Return ONLY raw HTML for Telegram (parse_mode=HTML)
- NO markdown: no **, no ##, no ```, no tables, no -
- Start with emoji and <b>header</b>
- Allowed tags: <b>, <i>, <code>, <s>, <u>
- Be concise - Telegram has 4096 char limit

EXECUTION:
1. Analyze the request
2. Call MCP tools directly (mcp__todoist__*, read/write files)
3. Return HTML status report with results"""

        # Live user request (/do) — NOT a maintenance turn, so it is a valid
        # steering target if the user sends a follow-up mid-answer.
        return self._ask(prompt, wrap=True, request_id=f"do-{user_id or 'anon'}")

    def execute_food_prompt(self, description: str, user_id: int = 0) -> dict[str, Any]:
        """Record a food entry to vault/daily/{today}.md via Claude.

        Claude evaluates КБЖУ from the description (text or photo path) and
        appends a structured line to the daily note. The caller shows only a
        short "🍽️ Записано" confirmation — Claude's reply is not forwarded.

        Args:
            description: Free-text description or "Фото еды: vault/attachments/..."
            user_id: Telegram user ID (used for request_id uniqueness)

        Returns:
            {"report": "🍽️ Записано", "processed_entries": 1} on success,
            or {"error": ..., "processed_entries": 0} on failure.
        """
        from d_brain.config import get_settings

        settings = get_settings()
        today = date.today().isoformat()

        profile = (
            f"Рост: {settings.nutrition_height_cm} см, "
            f"Вес: {settings.nutrition_weight_kg} кг, "
            f"Возраст: {settings.nutrition_age} лет, "
            f"{settings.nutrition_gender}. "
            f"Активность: {settings.nutrition_activity}. "
            f"Цель: {settings.nutrition_goal}. "
            f"Суточная норма: {settings.nutrition_daily_kcal} ккал | "
            f"Б:{settings.nutrition_daily_protein}г "
            f"Ж:{settings.nutrition_daily_fat}г "
            f"У:{settings.nutrition_daily_carbs}г"
        )

        prompt = f"""Ты нутрициолог-ассистент. Запиши приём пищи в дневник.

Профиль пользователя: {profile}

Данные о приёме пищи: {description}

Инструкция:
1. Оцени КБЖУ: калории (целое число), белки/жиры/углеводы (1 знак после запятой)
2. Определи тип приёма пищи: завтрак | обед | ужин | перекус
3. Запиши результат в конец файла daily/{today}.md, добавив строку в формате:
   ## HH:MM [food]
   🍽️ {{meal_type}}: {{краткое описание}} — {{kcal}} ккал | Б{{protein}} Ж{{fat}} У{{carbs}}
   (где HH:MM — текущее время по Москве)
4. Ответь ОДНОЙ строкой подтверждения без markdown, например:
   OK: перекус, 320 ккал

ВАЖНО: используй Write или Edit инструмент для записи. Работаешь в cwd=vault.
Ответь строго одной строкой — без объяснений, без markdown."""

        result = self._ask(prompt, wrap=True, request_id=f"food-{user_id or 'anon'}")
        if result.get("error"):
            return {"error": result["error"], "processed_entries": 0}
        return {"report": "🍽️ Записано", "processed_entries": 1}

    def execute_work_add(
        self,
        content: str,
        source_hint: str,
        attachment_path: str | None,
        user_id: int = 0,
    ) -> dict[str, Any]:
        """Save a work material to ~/.dbrain/work/ via Claude.

        Claude determines name/type/slug, writes raw + digest files, updates
        commitments.md and index.md (all absolute paths, NOT vault-relative).

        Returns:
            {"title": str, "metrics": int, "commitments": int, "report": str}
            or {"error": str}
        """
        from d_brain.config import get_settings
        from d_brain.services.work_memory import MAX_DIGEST_CHARS, WorkMemory

        settings = get_settings()
        mem = WorkMemory(settings.work_dir)
        mem.ensure_dirs()

        today = date.today().isoformat()
        month = date.today().strftime("%Y-%m")
        base = str(mem.base_dir)

        # Pre-create month dirs so Claude can write without mkdir
        (mem.base_dir / "raw" / month).mkdir(parents=True, exist_ok=True)
        (mem.base_dir / "digest" / month).mkdir(parents=True, exist_ok=True)

        # Truncate content for prompt; raw is always the full text
        content_for_prompt = content
        truncation_note = ""
        if len(content) > MAX_DIGEST_CHARS:
            content_for_prompt = content[:MAX_DIGEST_CHARS]
            truncation_note = (
                "\n\n[Примечание: текст обрезан до 50 000 символов для анализа; "
                "raw-файл содержит полный текст]"
            )

        source_line = f"\nИСТОЧНИК (приоритет для названия): {source_hint}" if source_hint else ""

        attachment_block = ""
        if attachment_path:
            is_pdf = attachment_path.lower().endswith(".pdf")
            if is_pdf:
                attachment_block = (
                    f"\n\nПРИЛОЖЕНИЕ — абсолютный путь к PDF: {attachment_path}\n"
                    "Прочитай документ целиком, включая все страницы. "
                    "Визуально просматривай каждый слайд: извлекай числа с графиков, "
                    "схем и таблиц — не ограничивайся текстовым слоем. "
                    "Для каждого графика укажи оси, диапазоны значений и динамику."
                )
            else:
                attachment_block = (
                    f"\n\nПРИЛОЖЕНИЕ — абсолютный путь к файлу: {attachment_path}\n"
                    "Прочитай его через vision для извлечения метрик и содержимого."
                )

        prompt = f"""Сохрани рабочий материал в базу рабочего контекста.

СЕГОДНЯ: {today}
БАЗА ДАННЫХ (АБСОЛЮТНЫЕ пути — НЕ vault-относительные, т.к. cwd=vault):
  raw:         {base}/raw/{month}/
  digest:      {base}/digest/{month}/
  commitments: {mem.commitments_path}
  index:       {mem.index_path}
{source_line}

СОДЕРЖИМОЕ МАТЕРИАЛА:
{content_for_prompt}{truncation_note}{attachment_block}

ЗАДАЧА — выполни строго по шагам:

1. Определи НАЗВАНИЕ и ТИП материала (встреча | дашборд | отчёт | прочее).
   Если источник указан выше — используй его как приоритет для названия.

2. Придумай SLUG: только латиница/цифры/дефисы, из названия, максимум 40 символов.
   Пример: "quarterly-review-june", "standup-2026-06-26", "dashboard-retention"

3. Сохрани RAW файл — Write инструментом по абсолютному пути:
   {base}/raw/{month}/{today}-SLUG.md
   Содержимое = весь переданный текст материала (полностью, без сокращений).

4. Сгенерируй ДАЙДЖЕСТ и сохрани Write инструментом по абсолютному пути:
   {base}/digest/{month}/{today}-SLUG.md

   Строгая структура дайджеста (пустые секции → "—"):
   # НАЗВАНИЕ — {today}
   Тип: ТИП
   Источник: {source_hint if source_hint else "авто-определение"}

   ## Метрики
   - название: значение (период если есть)

   ## Решения

   ## Договорённости
   - кто — что — срок

   ## Риски

   ## Контекст

5. Если в материале есть ДОГОВОРЁННОСТИ — добавь их в конец файла:
   {mem.commitments_path}
   Формат каждой строки: "- {today} | кто — что — срок"
   Если файл не существует — создай его с заголовком:
   "# Договорённости\\n\\n"
   Если новых договорённостей нет — файл НЕ ТРОГАЙ.

6. Добавь одну строку в конец файла {mem.index_path}:
   "{today} | ТИП | SLUG | тег1, тег2, ..."
   Если файл не существует — создай его с заголовком:
   "# Индекс рабочих материалов\\n\\nдата | тип | slug | теги\\n"

ВАЖНО: используй Write/Edit инструменты. Все пути АБСОЛЮТНЫЕ.
Работаешь в cwd=vault — но файлы work находятся ВНЕ vault.

Ответь СТРОГО ОДНОЙ строкой без объяснений:
OK | НАЗВАНИЕ | метрик:N | договорённостей:M"""

        result = self._ask(prompt, wrap=True, request_id=f"work-add-{user_id or 'anon'}")
        if result.get("error"):
            return {"error": result["error"]}

        # Parse the one-line response: "OK | название | метрик:N | договорённостей:M"
        reply = (result.get("report") or "").strip()
        for line in reply.splitlines():
            line = line.strip()
            if line.startswith("OK |"):
                try:
                    parts = [p.strip() for p in line.split("|")]
                    if len(parts) >= 4:
                        title = parts[1]
                        metrics = int(parts[2].split(":")[-1])
                        commitments = int(parts[3].split(":")[-1])
                        return {
                            "report": line,
                            "title": title,
                            "metrics": metrics,
                            "commitments": commitments,
                        }
                except (ValueError, IndexError):
                    logger.warning("Failed to parse work_add response line: %r", line)

        logger.warning("work_add: no OK line in reply: %r", reply[:200])
        return {"report": reply, "title": "материал", "metrics": 0, "commitments": 0}

    def execute_work_ask(self, question: str, user_id: int = 0) -> dict[str, Any]:
        """Search work memory and answer a question.

        Claude reads index.md, greps digests, and answers with source attribution.

        Returns:
            {"report": HTML answer} or {"error": str}
        """
        from d_brain.config import get_settings
        from d_brain.services.work_memory import WorkMemory

        settings = get_settings()
        mem = WorkMemory(settings.work_dir)
        today = date.today().isoformat()

        prompt = f"""Ответь на вопрос по сохранённым рабочим материалам.

СЕГОДНЯ: {today}
БАЗА РАБОЧЕГО КОНТЕКСТА (абсолютные пути):
  Индекс:         {mem.index_path}
  Дайджесты:      {mem.base_dir}/digest/
  Договорённости: {mem.commitments_path}

ВОПРОС: {question}

ЗАДАЧА:
1. Прочитай {mem.index_path} — получи обзор доступных материалов и дат.
2. Используй Grep/Glob/Read для поиска релевантных дайджестов в {mem.base_dir}/digest/.
3. Если вопрос про договорённости или задолженности — прочитай {mem.commitments_path}.
4. Ответь кратко и по делу, ОБЯЗАТЕЛЬНО укажи источник (название материала, дата).
5. Если ответа в сохранённых материалах нет — честно скажи:
   "не нашёл в сохранённых материалах"

ФОРМАТ ОТВЕТА (строго Telegram HTML):
- Только теги: <b>, <i>, <code>
- Начни с краткого ответа по существу
- В конце укажи: <i>Источник: название (дата)</i>
- Если источников несколько — перечисли
- Максимум 3000 символов
- Только raw HTML, без markdown, без пояснений перед тегами"""

        return self._ask(prompt, wrap=True, request_id=f"work-ask-{user_id or 'anon'}")

    def execute_role_switch_preview(self, correction: str = "") -> dict[str, Any]:
        """Generate a preview of what the role switch would do — without applying it.

        Reads vault/.session/new-role-intake.md (accumulated new-role context),
        current MEMORY.md, and goals/*.md. Returns a human-readable HTML summary
        of what will be transferred, archived, and rewritten.

        Args:
            correction: Optional user correction to incorporate into the preview.

        Returns:
            {"report": HTML preview} or {"error": str}
        """
        today = date.today().isoformat()
        intake_path = self.vault_path / ".session" / "new-role-intake.md"

        correction_block = ""
        if correction:
            correction_block = f"\n\nПРАВКА ПОЛЬЗОВАТЕЛЯ (учти при формировании превью):\n{correction}\n"

        prompt = f"""Ты — ассистент Second Brain. Сформируй ПРЕВЬЮ переноса роли (НЕ применяй изменения).

СЕГОДНЯ: {today}
VAULT: {self.vault_path}

ШАГ 1 — Прочитай накопленный контекст новой роли:
  {intake_path}

ШАГ 2 — Прочитай текущее состояние Second Brain:
  {self.vault_path}/MEMORY.md
  {self.vault_path}/goals/0-vision-3y.md
  {self.vault_path}/goals/1-yearly-2026.md
  {self.vault_path}/goals/2-monthly.md
  {self.vault_path}/goals/3-weekly.md
{correction_block}
ШАГ 3 — Сформируй ПРЕВЬЮ (только анализ, без записи файлов):

1. ЧТО ВОЙДЁТ В НОВЫЙ MEMORY.md:
   - Перечисли ключевые факты о новой роли (команда, предметная область, метрики, люди)
   - Что из старого MEMORY.md останется, что будет заменено

2. ЧТО БУДЕТ ЗААРХИВИРОВАНО:
   - Пункты старого MEMORY.md, которые теряют актуальность
   - Старые goals, которые не переносятся

3. ЧЕРНОВИКИ НОВЫХ ЦЕЛЕЙ (под новую роль):
   - 3-weekly: 3 цели на ближайшие 3 недели
   - 2-monthly: 2–3 приоритета на месяц
   - 1-yearly: годовые цели под новую предметную область
   - 0-vision: скорректированное видение (если новая роль меняет вектор)

4. ЧТО ОСТАЛОСЬ НЕЯСНЫМ (если чего-то не хватает в контексте) — кратко.

ФОРМАТ ОТВЕТА — строго Telegram HTML:
- Разделы через <b>заголовки</b>
- Теги: <b>, <i>, <code>
- Максимум 3500 символов
- Только raw HTML, без markdown, без пояснений до/после
- Начни с: 📋 <b>Превью переноса роли</b>"""

        return self._ask(prompt, wrap=True, request_id="role-switch-preview")

    def execute_role_switch_apply(self) -> dict[str, Any]:
        """Execute the actual role switch: archive old context, write new MEMORY + goals.

        Archives current MEMORY.md and goals/*.md to goals/archive/ before
        overwriting. Vault/.session/new-role-intake.md is left untouched as
        the original source.

        Returns:
            {"report": confirmation text} or {"error": str}
        """
        today = date.today().isoformat()
        intake_path = self.vault_path / ".session" / "new-role-intake.md"
        archive_dir = self.vault_path / "goals" / "archive"

        prompt = f"""Ты — ассистент Second Brain. ВЫПОЛНИ перенос роли — реально запиши файлы.

СЕГОДНЯ: {today}
VAULT: {self.vault_path}

═══ ШАГ 1: ПРОЧИТАЙ ИСТОЧНИКИ ═══

Прочитай накопленный контекст новой роли:
  {intake_path}

Прочитай текущее состояние:
  {self.vault_path}/MEMORY.md
  {self.vault_path}/goals/0-vision-3y.md
  {self.vault_path}/goals/1-yearly-2026.md
  {self.vault_path}/goals/2-monthly.md
  {self.vault_path}/goals/3-weekly.md

═══ ШАГ 2: АРХИВИРУЙ СТАРОЕ ═══

Директория архива: {archive_dir}
Убедись что она существует (создай если нет).

2a. Скопируй (Write) текущий MEMORY.md в архив:
    {archive_dir}/MEMORY-old-role-{today}.md
    (содержимое = полный текущий MEMORY.md)

2b. Скопируй (Write) каждый файл goals/*.md в архив с префиксом даты:
    {archive_dir}/goals-0-vision-{today}.md
    {archive_dir}/goals-1-yearly-{today}.md
    {archive_dir}/goals-2-monthly-{today}.md
    {archive_dir}/goals-3-weekly-{today}.md

═══ ШАГ 3: ПЕРЕЗАПИШИ MEMORY.md ═══

Write файл {self.vault_path}/MEMORY.md — ПОЛНОСТЬЮ НОВОЕ содержимое под новую роль.
Формат — стандартный MEMORY.md Second Brain:
  # Memory Index
  - [Запись](file.md) — краткое описание

Включи все релевантные факты из new-role-intake.md.
Старые факты про предыдущую роль — НЕ переноси (они в архиве).
Нейтральные факты (предпочтения, стиль работы) — сохрани.

═══ ШАГ 4: ПЕРЕЗАПИШИ GOALS ═══

Write каждый файл goals/*.md — НОВОЕ содержимое под новую роль:

{self.vault_path}/goals/3-weekly.md — 3 цели на ближайшие 3 недели в новой роли
{self.vault_path}/goals/2-monthly.md — 2–3 приоритета на месяц
{self.vault_path}/goals/1-yearly-2026.md — годовые цели под новую предметную область
{self.vault_path}/goals/0-vision-3y.md — скорректированное 3-летнее видение

═══ ШАГ 5: ПОДТВЕРДИ ═══

После записи всех файлов ответь ОДНОЙ строкой подтверждения:
OK | MEMORY.md обновлён | goals обновлены | архив: {archive_dir}

ВАЖНО:
- Все пути абсолютные, используй Write/Edit инструменты
- НЕ трогай {intake_path} (оставить как исходник)
- Работаешь в cwd=vault, но goals/archive/ — это vault-относительный путь"""

        result = self._ask(prompt, wrap=True, request_id="role-switch-apply")
        if result.get("error"):
            return {"error": result["error"]}
        # Normalize confirmation to a clean message
        reply = (result.get("report") or "").strip()
        return {"report": reply, "processed_entries": 1}

    def generate_weekly(self) -> dict[str, Any]:
        """Generate weekly digest with Claude.

        Returns:
            Weekly digest report as dict
        """
        today = date.today()

        prompt = f"""Сегодня {today}. Сгенерируй недельный дайджест.

ПЕРВЫМ ДЕЛОМ: вызови mcp__todoist__user-info чтобы убедиться что MCP работает.

CRITICAL MCP RULE:
- ТЫ ИМЕЕШЬ ДОСТУП к mcp__todoist__* tools — ВЫЗЫВАЙ ИХ НАПРЯМУЮ
- НИКОГДА не пиши "MCP недоступен" или "добавь вручную"
- Для выполненных задач: вызови mcp__todoist__find-completed-tasks tool
- Если tool вернул ошибку — покажи ТОЧНУЮ ошибку в отчёте

WORKFLOW:
1. Собери данные за неделю (daily файлы в vault/daily/, completed tasks через MCP)
2. Проанализируй прогресс по целям (goals/3-weekly.md)
3. Определи победы и вызовы
4. Сгенерируй HTML отчёт

CRITICAL OUTPUT FORMAT:
- Return ONLY raw HTML for Telegram (parse_mode=HTML)
- NO markdown: no **, no ##, no ```, no tables
- Start with 📅 <b>Недельный дайджест</b>
- Allowed tags: <b>, <i>, <code>, <s>, <u>
- Be concise - Telegram has 4096 char limit"""

        result = self._ask(prompt, wrap=True, request_id="maint-weekly")
        if result.get("report"):
            # Save to summaries/ and update MOC
            try:
                summary_path = self._save_weekly_summary(result["report"], today)
                self._update_weekly_moc(summary_path)
            except Exception as e:
                logger.warning("Failed to save weekly summary: %s", e)
        return result
