"""Handler for /food command — silent nutrition tracking session.

Flow:
  /food [text]  → open session (+ process inline args if given)
  text/voice    → processor.execute_food_prompt() → "🍽️ Записано"
  photo         → save to vault, pass path to execute_food_prompt → "🍽️ Записано"
  🛑 Завершить  → close session

Claude writes КБЖУ directly to vault/daily/{today}.md and returns a one-line
confirmation; the bot shows only "🍽️ Записано" to the user (silent logging).
"""

from __future__ import annotations

import asyncio
import html
import logging
from datetime import datetime

from aiogram import Bot, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from d_brain.bot.keyboards import get_conversation_keyboard, get_main_keyboard
from d_brain.bot.states import FoodCommandState
from d_brain.config import get_settings
from d_brain.services.runtime import get_processor
from d_brain.services.storage import VaultStorage
from d_brain.services.transcription import DeepgramTranscriber

router = Router(name="food")
logger = logging.getLogger(__name__)

_EXIT_TRIGGERS = {"🛑 Завершить", "🛑 Завершить сессию", "/stop"}


# ─── Entry point ──────────────────────────────────────────────────────────────

@router.message(Command("food"))
async def cmd_food(message: Message, command: CommandObject, state: FSMContext) -> None:
    """Handle /food: open nutrition tracking session (optionally with inline text)."""
    user_id = message.from_user.id if message.from_user else 0
    if command.args:
        await _record_food_entry(message, command.args, user_id)
        await _enter_food_conversation(message, state)
        return
    await state.set_state(FoodCommandState.waiting_for_input)
    await message.answer(
        "🍽️ <b>Учёт питания</b>\n\n"
        "Отправь фото блюда, голосовое или текстовое описание — запишу КБЖУ.\n"
        "🛑 Завершить, когда закончишь.",
        reply_markup=get_conversation_keyboard(),
    )


# ─── waiting_for_input ────────────────────────────────────────────────────────

@router.message(FoodCommandState.waiting_for_input, lambda m: m.photo is not None)
async def food_waiting_photo(message: Message, bot: Bot, state: FSMContext) -> None:
    """Handle photo as the first food entry."""
    user_id = message.from_user.id if message.from_user else 0
    description = await _save_photo_and_describe(message, bot)
    if description:
        await _record_food_entry(message, description, user_id)
    await _enter_food_conversation(message, state)


@router.message(FoodCommandState.waiting_for_input)
async def food_waiting_input(message: Message, bot: Bot, state: FSMContext) -> None:
    """Handle text/voice as the first food entry."""
    prompt = await _extract_prompt(message, bot)
    if not prompt:
        return  # stay in waiting_for_input until usable input arrives
    user_id = message.from_user.id if message.from_user else 0
    await _record_food_entry(message, prompt, user_id)
    await _enter_food_conversation(message, state)


# ─── in_conversation ──────────────────────────────────────────────────────────

@router.message(FoodCommandState.in_conversation, lambda m: m.photo is not None)
async def food_conv_photo(message: Message, bot: Bot, state: FSMContext) -> None:
    """Handle additional photo entries during the session."""
    user_id = message.from_user.id if message.from_user else 0
    description = await _save_photo_and_describe(message, bot)
    if description:
        await _record_food_entry(message, description, user_id)
    # stay in in_conversation; keyboard already shown


@router.message(FoodCommandState.in_conversation)
async def food_conv_input(message: Message, bot: Bot, state: FSMContext) -> None:
    """Handle text/voice follow-ups and the exit trigger."""
    text = (message.text or "").strip()
    if text in _EXIT_TRIGGERS:
        await state.clear()
        await message.answer("✅ Учёт питания завершён.", reply_markup=get_main_keyboard())
        return
    if text.startswith("/"):
        await state.clear()
        await message.answer("Вышел из режима питания.", reply_markup=get_main_keyboard())
        return
    prompt = await _extract_prompt(message, bot)
    if not prompt:
        return
    user_id = message.from_user.id if message.from_user else 0
    await _record_food_entry(message, prompt, user_id)
    # stay in in_conversation


# ─── Helpers ──────────────────────────────────────────────────────────────────

async def _enter_food_conversation(message: Message, state: FSMContext) -> None:
    """Transition to in_conversation and show the persistent keyboard."""
    await state.set_state(FoodCommandState.in_conversation)
    await message.answer(
        "💬 Добавляй ещё — фото, голос или текст. 🛑 Завершить, когда всё записал.",
        reply_markup=get_conversation_keyboard(),
    )


async def _record_food_entry(message: Message, description: str, user_id: int = 0) -> None:
    """Send food description to Claude; show brief confirmation (not full reply)."""
    status_msg = await message.answer("⏳ Записываю...")
    processor = get_processor(get_settings())

    async def run_with_progress() -> dict:
        task = asyncio.create_task(
            asyncio.to_thread(processor.execute_food_prompt, description, user_id)
        )
        elapsed = 0
        while not task.done():
            await asyncio.sleep(30)
            elapsed += 30
            if not task.done():
                try:
                    await status_msg.edit_text(f"⏳ Записываю... ({elapsed}s)")
                except Exception:
                    pass
        return await task

    result = await run_with_progress()
    try:
        if result.get("error"):
            await status_msg.edit_text(f"❌ {html.escape(result['error'])}")
        else:
            await status_msg.edit_text("🍽️ Записано")
    except Exception:
        pass


async def _extract_prompt(message: Message, bot: Bot) -> str | None:
    """Extract text from a voice or text message (mirrors do.py helper)."""
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
            prompt = await transcriber.transcribe(file_bytes.read())
        except Exception as e:
            logger.exception("Failed to transcribe voice for /food")
            await message.answer(f"❌ Не удалось транскрибировать: {html.escape(str(e))}")
            return None
        if not prompt:
            await message.answer("❌ Не удалось распознать речь")
            return None
        await message.answer(f"🎤 <i>{html.escape(prompt)}</i>")
        return prompt

    if message.text:
        return message.text

    await message.answer("❌ Отправь текст, голосовое или фото")
    return None


async def _save_photo_and_describe(message: Message, bot: Bot) -> str | None:
    """Save photo to vault/attachments and return a description string for Claude."""
    if not message.photo:
        return None
    settings = get_settings()
    storage = VaultStorage(settings.vault_path)
    photo = message.photo[-1]
    try:
        file = await bot.get_file(photo.file_id)
        if not file.file_path:
            await message.answer("❌ Не удалось скачать фото")
            return None
        file_bytes = await bot.download_file(file.file_path)
        if not file_bytes:
            await message.answer("❌ Не удалось скачать фото")
            return None
        extension = "jpg"
        if "." in file.file_path:
            extension = file.file_path.rsplit(".", 1)[-1]
        timestamp = datetime.fromtimestamp(message.date.timestamp())
        day = timestamp.date()
        attachments_dir = storage.get_attachments_dir(day)
        filename = f"food-{timestamp.strftime('%H%M%S')}-{message.message_id}.{extension}"
        file_path = attachments_dir / filename
        file_path.write_bytes(file_bytes.read())
        relative_path = f"attachments/{day.isoformat()}/{filename}"
        description = f"Фото еды: {relative_path}"
        if message.caption:
            description += f" ({message.caption})"
        return description
    except Exception as e:
        logger.exception("Failed to save food photo")
        await message.answer(f"❌ Не удалось сохранить фото: {html.escape(str(e))}")
        return None
