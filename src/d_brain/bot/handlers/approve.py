"""Handler for /approve command — apply voice corrections to weekly goals."""

import asyncio
import logging
import os
import re
import subprocess
from pathlib import Path

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from d_brain.config import get_settings
from d_brain.services.git import VaultGit
from d_brain.services.goals import GoalsService

router = Router(name="approve")
logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 600


def _apply_goal_corrections(settings, week: str, corrections_path: Path) -> str:
    """Call Claude to apply user corrections to 3-weekly.md."""
    mcp_config = settings.vault_path.parent / "mcp-config.json"
    goals_path = settings.vault_path / "goals" / "3-weekly.md"

    prompt = f"""Примени правки пользователя к плану на неделю {week}.

ФАЙЛЫ:
- Текущий план: {goals_path}
- Правки пользователя: {corrections_path}

ЗАДАЧА:
1. Прочитай текущий план из {goals_path}
2. Прочитай правки из {corrections_path}
3. Примени все правки: добавь/удали/измени пункты согласно голосовым/текстовым комментариям
4. Перезапиши файл {goals_path} с изменениями (сохрани формат frontmatter и структуру)

CRITICAL OUTPUT FORMAT:
- Return ONLY raw HTML for Telegram (parse_mode=HTML)
- Start with ✅ <b>Цели недели {week} обновлены</b>
- Перечисли что изменилось (добавлено/удалено/изменено)
- Allowed tags: <b>, <i>, <code>, <s>, <u>"""

    env = os.environ.copy()
    if settings.todoist_api_key:
        env["TODOIST_API_KEY"] = settings.todoist_api_key

    result = subprocess.run(
        [
            "claude",
            "--print",
            "--model", "claude-sonnet-4-6",
            "--dangerously-skip-permissions",
            "--mcp-config",
            str(mcp_config),
            "-p",
            prompt,
        ],
        cwd=str(settings.vault_path.parent),
        capture_output=True,
        text=True,
        timeout=DEFAULT_TIMEOUT,
        check=False,
        env=env,
    )

    if result.returncode != 0:
        return f"❌ Ошибка применения правок: {result.stderr[:300]}"

    output = result.stdout.strip()
    for pattern in [r"^HTML для Telegram[:\s]*", r"^Вот HTML для Telegram[:\s]*", r"^Вот готовый HTML[:\s]*"]:
        output = re.sub(pattern, "", output, flags=re.IGNORECASE).strip()
    return output


@router.message(Command("approve"))
async def cmd_approve(message: Message) -> None:
    """Handle /approve — accept weekly goals (with or without corrections)."""
    settings = get_settings()
    goals = GoalsService(settings.vault_path)

    week = goals.get_pending_week()
    if not week:
        await message.answer("ℹ️ Нет активной сессии постановки целей.")
        return

    if not goals.has_corrections(week):
        # No corrections — approve as-is
        goals.clear(week)
        await asyncio.to_thread(
            VaultGit(settings.vault_path).commit_and_push, f"chore: goals {week} approved"
        )
        await message.answer(f"✅ <b>Цели на {week} приняты!</b>")
        return

    # Apply corrections via Claude
    status_msg = await message.answer("⏳ Применяю правки к целям...")
    corrections_path = goals.get_corrections_path(week)

    output = await asyncio.to_thread(
        _apply_goal_corrections, settings, week, corrections_path
    )

    goals.clear(week)
    await asyncio.to_thread(
        VaultGit(settings.vault_path).commit_and_push, f"chore: goals {week} approved"
    )

    try:
        await status_msg.edit_text(output)
    except Exception:
        await status_msg.edit_text(output, parse_mode=None)
