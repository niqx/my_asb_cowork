"""Cascade input router — interprets user response while a cascade
proposal is pending and routes it to apply/re-run/cancel.

Designed to be called from the top of voice/text handlers BEFORE the
regular reflection/daily flow runs.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from pathlib import Path
from typing import Literal

from aiogram import Bot

from d_brain.bot.keyboards import get_main_keyboard
from d_brain.services.cascade import CascadeService
from d_brain.services.cascade_applier import CascadeApplier
from d_brain.services.git import VaultGit

logger = logging.getLogger(__name__)

Outcome = Literal["applied", "cancelled", "feedback_recorded", "not_active", "error"]

_BTN_ACCEPT = "✅ Принять"
_BTN_FEEDBACK = "✏️ Внести правки"
_BTN_CANCEL = "❌ Отмена"


async def handle_cascade_reply(
    *, vault_path: Path, bot: Bot, user_id: int, text: str
) -> Outcome:
    """If cascade is pending, route based on exact button text. Returns outcome.

    Only explicit button presses are intercepted — free-form text falls through
    to the regular diary handler (was the bug: any text >30 chars was captured).
    """
    cascade = CascadeService(vault_path)

    # Auto-clear silently if deadline passed
    if cascade.is_expired():
        cascade.clear()
        return "not_active"

    state = cascade.get()
    if state is None or state.get("stage") in {"applied", "cancelled"}:
        return "not_active"

    t = (text or "").strip()

    # --- Exact button matching ---
    if t == _BTN_ACCEPT:
        return await _apply(cascade, vault_path, bot, user_id)

    if t == _BTN_CANCEL:
        cascade.clear()
        await bot.send_message(
            chat_id=user_id,
            text="🚫 Каскадное ревью отменено. Файлы целей не тронул.",
            reply_markup=get_main_keyboard(),
        )
        return "cancelled"

    if t == _BTN_FEEDBACK:
        cascade.enter_feedback_mode()
        await bot.send_message(
            chat_id=user_id,
            text="✏️ Опиши правки текстом или голосом — что поменять в предложении.",
        )
        return "feedback_recorded"

    # --- Feedback mode: free-form text is actual feedback ---
    if cascade.is_in_feedback_mode():
        cascade.record_feedback(t)
        await bot.send_message(
            chat_id=user_id,
            text="📝 Принял правки, пересчитываю предложение... это займёт минуту-две.",
        )
        asyncio.create_task(_rerun_cascade(vault_path))
        return "feedback_recorded"

    # awaiting_decision but not a button press -> not cascade-related
    return "not_active"


async def _apply(
    cascade: CascadeService, vault_path: Path, bot: Bot, user_id: int
) -> Outcome:
    state = cascade.get()
    if state is None:
        return "error"
    proposal = state.get("proposal") or {}
    try:
        applier = CascadeApplier(vault_path)
        result = await asyncio.to_thread(applier.apply, proposal)
    except Exception as e:
        logger.exception("Cascade apply failed")
        await bot.send_message(
            chat_id=user_id,
            text=f"❌ Не смог применить предложение: <code>{str(e)[:300]}</code>",
        )
        return "error"

    cascade.mark_applied()
    cascade.clear()

    summary_lines = ["✅ <b>Каскад целей обновлён</b>", ""]
    w = result.get("weekly")
    if w:
        summary_lines.append(f"• Неделя: <code>{w.get('next_week_id')}</code> — записал в goals/3-weekly.md")
    m = result.get("monthly")
    if m:
        summary_lines.append(f"• Месяц: <code>{m.get('period')}</code> — записал в goals/2-monthly.md")
    y = result.get("yearly")
    if y:
        summary_lines.append(f"• Год: <code>{y.get('period')}</code> — записал в goals/1-yearly-{y.get('period')}.md")
    summary_lines.append("")
    summary_lines.append("Старые версии в goals/archive/. Запушу в git.")

    try:
        git = VaultGit(vault_path)
        await asyncio.to_thread(git.commit_and_push, "chore: cascade goals refresh")
    except Exception as e:
        logger.warning("Git commit failed: %s", e)
        summary_lines.append(f"<i>Git push не прошёл: {str(e)[:200]}</i>")

    await bot.send_message(
        chat_id=user_id,
        text="\n".join(summary_lines),
        reply_markup=get_main_keyboard(),
    )
    return "applied"


async def _rerun_cascade(vault_path: Path) -> None:
    """Spawn cascade_review.py to re-run with the new feedback."""
    project_dir = vault_path.parent
    script = project_dir / "scripts" / "cascade_review.py"
    try:
        await asyncio.to_thread(
            subprocess.run,
            [sys.executable, str(script)],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
    except Exception:
        logger.exception("Cascade rerun failed")
