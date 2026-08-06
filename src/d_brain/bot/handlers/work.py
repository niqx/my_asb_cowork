"""Handler for work context saving (➕ Работа) and querying (❓ Спросить).

ДОБАВЛЕНИЕ (open_work_add_session):
  Принимает фото/документ/голос/текст → extracts text → processor.execute_work_add
  → подтверждение "✓ Записал: название | метрик: N, договорённостей: M"
  Обработка ПОСЛЕДОВАТЕЛЬНАЯ через asyncio.Lock (защита от race на index/commitments).

ВОПРОС (open_work_ask_session):
  Принимает текст/голос → processor.execute_work_ask → ответ с указанием источника
  Остаётся в сессии для follow-up вопросов.
"""

from __future__ import annotations

import asyncio
import html
import logging

from aiogram import Bot, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from d_brain.bot.keyboards import get_conversation_keyboard, get_main_keyboard
from d_brain.bot.states import WorkAddState, WorkAskState
from d_brain.config import get_settings
from d_brain.services.runtime import get_processor
from d_brain.services.transcription import DeepgramTranscriber

router = Router(name="work")
logger = logging.getLogger(__name__)

_EXIT_TRIGGERS = {"🛑 Завершить", "🛑 Завершить сессию", "/stop"}

# Module-level lock: ensures index.md / commitments.md writes never race.
_work_lock = asyncio.Lock()
# Running counters for "обрабатываю N из M" indicator (resets on restart).
_work_total = 0
_work_done = 0


# ─── Entry points ─────────────────────────────────────────────────────────────

async def open_work_add_session(message: Message, state: FSMContext) -> None:
    """Enter the work context saving flow."""
    await state.set_state(WorkAddState.waiting_for_input)
    await message.answer(
        "➕ <b>Рабочий контекст</b>\n\n"
        "Кидай транскрипции, скриншоты дашбордов, документы — сохраню и структурирую.\n"
        "🛑 Завершить.",
        reply_markup=get_conversation_keyboard(),
    )


async def open_work_ask_session(message: Message, state: FSMContext) -> None:
    """Enter the work context querying flow."""
    await state.set_state(WorkAskState.waiting_for_question)
    await message.answer(
        "❓ <b>Спросить по работе</b>\n\n"
        "Задай вопрос — поищу в сохранённых материалах.\n"
        "🛑 Завершить.",
        reply_markup=get_conversation_keyboard(),
    )


# ─── WorkAddState.waiting_for_input ───────────────────────────────────────────

@router.message(WorkAddState.waiting_for_input, lambda m: m.photo is not None)
async def work_add_waiting_photo(message: Message, bot: Bot, state: FSMContext) -> None:
    await state.set_state(WorkAddState.in_session)
    await _handle_work_photo(message, bot)


@router.message(WorkAddState.waiting_for_input, lambda m: m.document is not None)
async def work_add_waiting_document(message: Message, bot: Bot, state: FSMContext) -> None:
    await state.set_state(WorkAddState.in_session)
    await _handle_work_document(message, bot)


@router.message(WorkAddState.waiting_for_input)
async def work_add_waiting_input(message: Message, bot: Bot, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if text in _EXIT_TRIGGERS or text.startswith("/"):
        await state.clear()
        await message.answer("✅ Рабочий контекст закрыт.", reply_markup=get_main_keyboard())
        return
    prompt = await _extract_voice_or_text(message, bot)
    if not prompt:
        return
    await state.set_state(WorkAddState.in_session)
    await _process_work_item(
        message, prompt, source_hint=message.caption or "", attachment_path=None
    )


# ─── WorkAddState.in_session ──────────────────────────────────────────────────

@router.message(WorkAddState.in_session, lambda m: m.photo is not None)
async def work_add_session_photo(message: Message, bot: Bot, state: FSMContext) -> None:
    await _handle_work_photo(message, bot)


@router.message(WorkAddState.in_session, lambda m: m.document is not None)
async def work_add_session_document(message: Message, bot: Bot, state: FSMContext) -> None:
    await _handle_work_document(message, bot)


@router.message(WorkAddState.in_session)
async def work_add_session_input(message: Message, bot: Bot, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if text in _EXIT_TRIGGERS or text.startswith("/"):
        await state.clear()
        await message.answer("✅ Рабочий контекст закрыт.", reply_markup=get_main_keyboard())
        return
    prompt = await _extract_voice_or_text(message, bot)
    if not prompt:
        return
    await _process_work_item(
        message, prompt, source_hint=message.caption or "", attachment_path=None
    )


# ─── WorkAskState.waiting_for_question ────────────────────────────────────────

@router.message(WorkAskState.waiting_for_question)
async def work_ask_question(message: Message, bot: Bot, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if text in _EXIT_TRIGGERS or text.startswith("/"):
        await state.clear()
        await message.answer("✅ Режим вопросов закрыт.", reply_markup=get_main_keyboard())
        return
    question = await _extract_voice_or_text(message, bot)
    if not question:
        return
    await state.set_state(WorkAskState.in_session)
    await _ask_work_question(message, question)


# ─── WorkAskState.in_session ──────────────────────────────────────────────────

@router.message(WorkAskState.in_session)
async def work_ask_followup(message: Message, bot: Bot, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if text in _EXIT_TRIGGERS or text.startswith("/"):
        await state.clear()
        await message.answer("✅ Режим вопросов закрыт.", reply_markup=get_main_keyboard())
        return
    question = await _extract_voice_or_text(message, bot)
    if not question:
        return
    # Stay in in_session — follow-up questions loop here
    await _ask_work_question(message, question)


# ─── Core add processing ──────────────────────────────────────────────────────

async def _handle_work_photo(message: Message, bot: Bot) -> None:
    """Save photo to work attachments and queue it for processing."""
    if not message.photo:
        return
    settings = get_settings()
    from d_brain.services.work_memory import WorkMemory
    mem = WorkMemory(settings.work_dir)
    mem.ensure_dirs()

    photo = message.photo[-1]
    try:
        file = await bot.get_file(photo.file_id)
        if not file.file_path:
            await message.answer("❌ Не удалось скачать фото")
            return
        file_bytes = await bot.download_file(file.file_path)
        if not file_bytes:
            await message.answer("❌ Не удалось скачать фото")
            return
        ext = "jpg"
        if "." in (file.file_path or ""):
            ext = file.file_path.rsplit(".", 1)[-1]
        attachment_path = mem.save_attachment(file_bytes.read(), ext, message.message_id)
    except Exception as e:
        logger.exception("Failed to save work photo")
        await message.answer(f"❌ Не удалось сохранить фото: {html.escape(str(e))}")
        return

    caption = message.caption or ""
    content = f"Скриншот/фото рабочего материала{': ' + caption if caption else '.'}"
    source_hint = caption or "скриншот"
    await _process_work_item(
        message, content, source_hint=source_hint, attachment_path=str(attachment_path)
    )


async def _handle_work_document(message: Message, bot: Bot) -> None:
    """Extract text from document and queue it for processing."""
    if not message.document:
        return

    # Import from document.py — reuse existing _extract_text without duplicating
    from d_brain.bot.handlers.document import _detect_extension, _extract_text

    doc = message.document
    mime = doc.mime_type or ""
    filename = doc.file_name or "document"
    ext = _detect_extension(filename, mime)
    if not ext:
        await message.answer(
            f"⚠️ Формат не поддерживается: <code>{html.escape(filename)}</code>\n"
            "Поддерживаются: .txt .md .pdf .xlsx .docx"
        )
        return

    # Telegram doesn't allow bots to download files larger than 20 MB
    if doc.file_size and doc.file_size > 20 * 1024 * 1024:
        await message.answer(
            "⚠️ Файл больше 20 МБ — Telegram не отдаёт такие боту.\n"
            "Пришли версию полегче или выжимку."
        )
        return

    try:
        file = await bot.get_file(doc.file_id)
        if not file.file_path:
            await message.answer("❌ Не удалось скачать документ")
            return
        file_bytes_io = await bot.download_file(file.file_path)
        if not file_bytes_io:
            await message.answer("❌ Не удалось скачать документ")
            return
        data = file_bytes_io.read()
        text = _extract_text(data, ext)
    except Exception as e:
        logger.exception("Failed to extract document text: %s", filename)
        await message.answer(f"❌ Ошибка обработки документа: {html.escape(str(e))}")
        return

    if not text.strip():
        await message.answer(f"⚠️ Не удалось извлечь текст из: {html.escape(filename)}")
        return

    caption = message.caption or ""
    source_hint = caption or filename
    content = f"{caption}\n\n{text}".strip() if caption else text

    attachment_path = None
    if ext == ".pdf":
        settings = get_settings()
        from d_brain.services.work_memory import WorkMemory
        mem = WorkMemory(settings.work_dir)
        mem.ensure_dirs()
        path = mem.save_attachment(data, "pdf", message.message_id)
        attachment_path = str(path)

    await _process_work_item(message, content, source_hint=source_hint, attachment_path=attachment_path)


async def _process_work_item(
    message: Message,
    content: str,
    source_hint: str,
    attachment_path: str | None,
) -> None:
    """Process one work item; sequential via module-level lock.

    Shows "📥 В очереди: обрабатываю N из M" if another item is in progress.
    """
    global _work_total, _work_done

    _work_total += 1
    my_num = _work_total

    if _work_lock.locked():
        status_msg = await message.answer(
            f"📥 В очереди: обрабатываю {_work_done + 1} из {_work_total}..."
        )
    else:
        status_msg = await message.answer("⏳ Обрабатываю...")

    async with _work_lock:
        n = _work_done + 1
        m = _work_total
        try:
            await status_msg.edit_text(f"⏳ Обрабатываю {n} из {m}...")
        except Exception:
            pass

        user_id = message.from_user.id if message.from_user else 0
        processor = get_processor(get_settings())

        task = asyncio.create_task(
            asyncio.to_thread(
                processor.execute_work_add,
                content,
                source_hint,
                attachment_path,
                user_id,
            )
        )

        elapsed = 0
        while not task.done():
            await asyncio.sleep(30)
            elapsed += 30
            if not task.done():
                try:
                    await status_msg.edit_text(f"⏳ Обрабатываю {n} из {m}... ({elapsed}s)")
                except Exception:
                    pass

        result = await task
        _work_done += 1

        try:
            if result.get("error"):
                await status_msg.edit_text(f"❌ {html.escape(result['error'])}")
            else:
                title = html.escape(result.get("title") or "материал")
                metrics = result.get("metrics", 0)
                commitments = result.get("commitments", 0)
                await status_msg.edit_text(
                    f"✓ Записал: <b>{title}</b> | "
                    f"метрик: {metrics}, договорённостей: {commitments}"
                )
        except Exception:
            logger.debug("Could not edit status message", exc_info=True)


# ─── Core ask processing ──────────────────────────────────────────────────────

async def _ask_work_question(message: Message, question: str) -> None:
    """Send question to Claude and display the answer."""
    status_msg = await message.answer("🔍 Ищу в рабочих материалах...")
    user_id = message.from_user.id if message.from_user else 0
    processor = get_processor(get_settings())

    task = asyncio.create_task(
        asyncio.to_thread(processor.execute_work_ask, question, user_id)
    )

    elapsed = 0
    while not task.done():
        await asyncio.sleep(30)
        elapsed += 30
        if not task.done():
            try:
                await status_msg.edit_text(f"🔍 Ищу в рабочих материалах... ({elapsed}s)")
            except Exception:
                pass

    result = await task
    try:
        await status_msg.delete()
    except Exception:
        pass

    if result.get("error"):
        await message.answer(f"❌ {html.escape(result['error'])}")
    else:
        answer = result.get("report") or "Нет ответа"
        await message.answer(answer)


# ─── Shared voice/text extractor ─────────────────────────────────────────────

async def _extract_voice_or_text(message: Message, bot: Bot) -> str | None:
    """Extract text from voice or text message (mirrors food.py pattern)."""
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
            logger.exception("Failed to transcribe voice for work")
            await message.answer(f"❌ Не удалось транскрибировать: {html.escape(str(e))}")
            return None
        if not prompt:
            await message.answer("❌ Не удалось распознать речь")
            return None
        await message.answer(f"🎤 <i>{html.escape(prompt)}</i>")
        return prompt

    if message.text:
        return message.text

    await message.answer("❌ Отправь текст, голосовое, фото или документ")
    return None
