"""Button handlers for reply keyboard."""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

router = Router(name="buttons")


@router.message(F.text == "📅 Неделя")
async def btn_weekly(message: Message) -> None:
    """Handle Weekly button."""
    from d_brain.bot.handlers.weekly import cmd_weekly

    await cmd_weekly(message)


@router.message(F.text == "✨ Запрос")
async def btn_do(message: Message, state: FSMContext) -> None:
    """Handle Do button - open interactive Claude session."""
    from d_brain.bot.handlers.do import open_session

    await open_session(message, state)


@router.message(F.text == "✏️ Правки")
async def btn_edit(message: Message, state: FSMContext) -> None:
    """Handle Edit button - enter edit mode."""
    from d_brain.bot.handlers.edit import enter_edit_mode

    await enter_edit_mode(message, state)


@router.message(F.text == "❓ Помощь")
async def btn_help(message: Message) -> None:
    """Handle Help button."""
    from d_brain.bot.handlers.commands import cmd_help

    await cmd_help(message)


@router.message(F.text == "⚙️ Настройки")
async def btn_settings(message: Message) -> None:
    """Handle Settings button."""
    from d_brain.bot.handlers.commands import cmd_settings

    await cmd_settings(message)


@router.message(F.text == "🔧 Улучшить")
async def btn_improve(message: Message) -> None:
    """Handle Improve button - shortcut to /improve."""
    from d_brain.bot.handlers.improve import cmd_improve

    await cmd_improve(message)


@router.message(F.text == "🍽️ Еда")
async def btn_food(message: Message, state: FSMContext) -> None:
    """Handle Food button - open nutrition tracking session."""
    from d_brain.bot.handlers.food import open_food_session

    await open_food_session(message, state)


@router.message(F.text == "➕ Работа")
async def btn_work_add(message: Message, state: FSMContext) -> None:
    """Handle Work Add button - open work context saving session."""
    from d_brain.bot.handlers.work import open_work_add_session

    await open_work_add_session(message, state)


@router.message(F.text == "❓ Спросить")
async def btn_work_ask(message: Message, state: FSMContext) -> None:
    """Handle Work Ask button - open work context querying session."""
    from d_brain.bot.handlers.work import open_work_ask_session

    await open_work_ask_session(message, state)
