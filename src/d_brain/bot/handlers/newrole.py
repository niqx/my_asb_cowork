"""Handler for /newrole and /switchrole commands.

/newrole (+ "🆕 Новая роль" button) — открывает режим сбора контекста.
  text/voice → append-only в vault/.session/new-role-intake.md (без Claude, просто файл)
  🛑 Завершить → выход

/switchrole — управляемый перенос (НЕ кнопка, разовая команда):
  1. Проверяет файл накопителя.
  2. Генерирует превью через processor.execute_role_switch_preview().
  3. Показывает превью + клавиатуру решения.
  4. ✅ Применить перенос → execute_role_switch_apply() — архивирует старое, пишет новое.
  5. ✏️ Не так — уточню → принимает правку → переформирует превью.
  6. ❌ Отмена → ничего не меняет.
"""

from __future__ import annotations

import asyncio
import html
import logging
from datetime import datetime
from pathlib import Path

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from d_brain.bot.keyboards import (
    get_main_keyboard,
    get_newrole_keyboard,
    get_role_switch_decision_keyboard,
)
from d_brain.bot.states import NewRoleState
from d_brain.config import get_settings
from d_brain.services.runtime import get_processor
from d_brain.services.transcription import DeepgramTranscriber

router = Router(name="newrole")
logger = logging.getLogger(__name__)

_EXIT_TRIGGERS = {"🛑 Завершить", "🛑 Завершить сессию", "/stop"}


def _get_intake_path(vault_path: Path) -> Path:
    return vault_path / ".session" / "new-role-intake.md"


async def _append_to_intake(intake_path: Path, text: str) -> None:
    """Append a dated entry to the accumulator file (create if missing)."""
    intake_path.parent.mkdir(parents=True, exist_ok=True)
    if not intake_path.exists():
        intake_path.write_text("# Онбординг новой роли (черновик контекста)\n\n", encoding="utf-8")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"\n## {now}\n{text}\n"
    with intake_path.open("a", encoding="utf-8") as f:
        f.write(entry)


async def _extract_text_or_voice(message: Message, bot: Bot) -> str | None:
    """Extract content from text or voice message."""
    if message.voice:
        await message.chat.do(action="typing")
        settings = get_settings()
        transcriber = DeepgramTranscriber(settings.deepgram_api_key)
        try:
            file = await bot.get_file(message.voice.file_id)
            if not file.file_path:
                await message.answer("❌ Не удалось скачать голосовое")
                return None
            file_bytes = await bot.download_file(file.file_path)
            if not file_bytes:
                await message.answer("❌ Не удалось скачать голосовое")
                return None
            text = await transcriber.transcribe(file_bytes.read())
        except Exception as e:
            logger.exception("Failed to transcribe voice in /newrole")
            await message.answer(f"❌ Не удалось транскрибировать: {html.escape(str(e))}")
            return None
        if not text:
            await message.answer("❌ Не удалось распознать речь")
            return None
        await message.answer(f"🎤 <i>{html.escape(text)}</i>")
        return text

    if message.text:
        return message.text

    await message.answer("❌ Отправь текст или голосовое")
    return None


# ─── Entry point ──────────────────────────────────────────────────────────────

async def open_newrole_session(message: Message, state: FSMContext) -> None:
    """Enter new role context intake mode (called from /newrole and button)."""
    await state.set_state(NewRoleState.collecting)
    await message.answer(
        "🆕 <b>Контекст новой роли</b>\n"
        "Кидай всё про новую команду и роль — текстом или голосом. "
        "Чем занимаешься, кто ключевые люди, за какие метрики отвечаешь, "
        "цели, что важно. Можно потоком, можно по кусочкам в разные дни.\n"
        "📥 Всё складываю в один файл. 🛑 Завершить когда закончишь.",
        reply_markup=get_newrole_keyboard(),
    )


@router.message(Command("newrole"))
async def cmd_newrole(message: Message, state: FSMContext) -> None:
    """Handle /newrole command."""
    await open_newrole_session(message, state)


# ─── collecting ───────────────────────────────────────────────────────────────

@router.message(NewRoleState.collecting)
async def newrole_collecting(message: Message, bot: Bot, state: FSMContext) -> None:
    """Receive text/voice chunk and append to intake file. No Claude call."""
    text = (message.text or "").strip()

    if text in _EXIT_TRIGGERS or text.startswith("/"):
        await state.clear()
        await message.answer("✅ Сбор контекста новой роли завершён.", reply_markup=get_main_keyboard())
        return

    content = await _extract_text_or_voice(message, bot)
    if not content:
        return

    settings = get_settings()
    intake_path = _get_intake_path(Path(settings.vault_path))
    await _append_to_intake(intake_path, content)

    await message.answer("📥 Записал в контекст новой роли")


# ─── /switchrole ──────────────────────────────────────────────────────────────

@router.message(Command("switchrole"))
async def cmd_switchrole(message: Message, state: FSMContext) -> None:
    """/switchrole — проверяет накопитель, генерирует превью, ждёт решения."""
    settings = get_settings()
    intake_path = _get_intake_path(Path(settings.vault_path))

    if not intake_path.exists() or intake_path.stat().st_size < 50:
        await message.answer(
            "Файл контекста новой роли пуст. "
            "Сначала накидай контекст через /newrole.",
            reply_markup=get_main_keyboard(),
        )
        return

    await state.set_state(NewRoleState.confirming_switch)
    await state.update_data(awaiting_correction=False)

    status_msg = await message.answer(
        "⏳ Анализирую накопленный контекст и готовлю превью переноса… "
        "(это займёт 1–2 минуты)"
    )
    await _generate_and_show_preview(message, state, status_msg, correction="")


# ─── confirming_switch ────────────────────────────────────────────────────────

@router.message(NewRoleState.confirming_switch)
async def newrole_confirming(message: Message, bot: Bot, state: FSMContext) -> None:
    """Handle user decision on role-switch preview."""
    text = (message.text or "").strip()

    if text == "✅ Применить перенос":
        await _apply_role_switch(message, state)
        return

    if text == "❌ Отмена":
        await state.clear()
        await message.answer("🚫 Перенос отменён. Ничего не изменилось.", reply_markup=get_main_keyboard())
        return

    if text == "✏️ Не так — уточню":
        await state.update_data(awaiting_correction=True)
        await message.answer(
            "Что поправить? Опиши, и я пересоберу превью.",
            reply_markup=get_role_switch_decision_keyboard(),
        )
        return

    # Any other text: check if we're collecting a correction
    data = await state.get_data()
    if data.get("awaiting_correction"):
        await state.update_data(awaiting_correction=False)
        status_msg = await message.answer("⏳ Пересобираю превью с учётом правки…")
        await _generate_and_show_preview(message, state, status_msg, correction=text)
    else:
        await message.answer("Выбери действие:", reply_markup=get_role_switch_decision_keyboard())


# ─── Helpers ──────────────────────────────────────────────────────────────────

async def _generate_and_show_preview(
    message: Message,
    state: FSMContext,
    status_msg: Message,
    correction: str,
) -> None:
    """Call processor to generate preview and show it with decision keyboard."""
    settings = get_settings()
    processor = get_processor(settings)

    result = await asyncio.to_thread(processor.execute_role_switch_preview, correction)

    if result.get("error"):
        await state.clear()
        await status_msg.edit_text(f"❌ {html.escape(result['error'])}")
        await message.answer("Вернулся в главное меню.", reply_markup=get_main_keyboard())
        return

    preview_html = result.get("report", "(превью не сформировано)")
    label = "обновлено" if correction else ""
    header = f"<b>📋 Превью переноса роли{' (' + label + ')' if label else ''}</b>\n\n"

    try:
        await status_msg.edit_text(
            header + preview_html + "\n\nКак поступить с этим переносом?",
            reply_markup=get_role_switch_decision_keyboard(),
        )
    except Exception:
        # Message too long or edit failed — send as new message
        await message.answer(
            header + preview_html + "\n\nКак поступить с этим переносом?",
            reply_markup=get_role_switch_decision_keyboard(),
        )


async def _apply_role_switch(message: Message, state: FSMContext) -> None:
    """Execute the real role switch: archive old, write new MEMORY + goals."""
    status_msg = await message.answer(
        "⏳ Выполняю перенос… (это займёт 1–3 минуты)"
    )
    settings = get_settings()
    processor = get_processor(settings)

    result = await asyncio.to_thread(processor.execute_role_switch_apply)
    await state.clear()

    if result.get("error"):
        await status_msg.edit_text(f"❌ Ошибка переноса: {html.escape(result['error'])}")
        await message.answer("Ничего не изменилось. Попробуй снова.", reply_markup=get_main_keyboard())
        return

    await status_msg.edit_text(
        "✅ Перенос выполнен. Старый контекст в архиве. "
        "MEMORY.md и goals обновлены под новую роль.",
        reply_markup=get_main_keyboard(),
    )
