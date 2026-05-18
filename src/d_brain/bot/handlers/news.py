"""Handler for /news command — show today's news as a compact digest with links."""

import json
import logging
from pathlib import Path

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from d_brain.config import get_settings

router = Router(name="news")
logger = logging.getLogger(__name__)

_MAX_MSG = 4000  # Telegram limit safety margin


@router.message(Command("news"))
async def cmd_news(message: Message) -> None:
    """Handle /news command — display today's news as a digest with links."""
    settings = get_settings()
    news_path = settings.vault_path / ".session" / "morning-news.json"

    if not news_path.exists():
        await message.answer("📰 Новостей на сегодня нет. Запустится утром следующего дня.")
        return

    try:
        data = json.loads(news_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error("Failed to read morning-news.json: %s", e)
        await message.answer("❌ Ошибка чтения новостей.")
        return

    articles = data.get("articles", [])
    if not articles:
        await message.answer("📰 Список новостей пуст.")
        return

    date_str = data.get("date", "сегодня")
    lines = [f"📰 <b>Новости — {date_str}</b>\n"]

    for art in articles:
        title = art.get("title_ru") or art.get("title", "Без названия")
        source = art.get("source", "")
        url = art.get("url", "")
        summary = art.get("summary", "")

        if url:
            line = f'• <a href="{url}">{title}</a>'
        else:
            line = f"• {title}"

        if source:
            line += f" <i>({source})</i>"

        if summary:
            line += f"\n  {summary}"

        lines.append(line)

    text = "\n".join(lines)

    # Split if over Telegram limit
    if len(text) <= _MAX_MSG:
        await message.answer(text)
    else:
        # Send in chunks preserving line boundaries
        header = lines[0]
        chunk_lines = [header]
        for line in lines[1:]:
            candidate = "\n".join(chunk_lines + [line])
            if len(candidate) > _MAX_MSG:
                await message.answer("\n".join(chunk_lines))
                chunk_lines = [line]
            else:
                chunk_lines.append(line)
        if chunk_lines:
            await message.answer("\n".join(chunk_lines))
