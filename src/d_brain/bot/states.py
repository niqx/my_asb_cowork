"""Bot FSM states."""

from aiogram.fsm.state import State, StatesGroup


class DoCommandState(StatesGroup):
    """States for the /do request flow."""

    waiting_for_input = State()  # Waiting for the first voice/text after /do
    in_conversation = State()    # Follow-up turns continue the same session


class EditModeState(StatesGroup):
    """States for edit mode (batch corrections)."""

    collecting = State()   # Collecting voice/text edit instructions
    confirming = State()   # Waiting for user to confirm preview


class AgentSessionState(StatesGroup):
    """States for interactive Claude session."""

    in_session = State()          # Active session, waiting for user commands
    awaiting_permission = State() # Claude paused, waiting for user approval


class SettingsState(StatesGroup):
    """States for Settings menu flows."""

    waiting_for_city = State()  # Waiting for user to type a new city name


class FoodCommandState(StatesGroup):
    """States for the /food nutrition tracking session."""

    waiting_for_input = State()  # Waiting for first food entry (text/voice/photo)
    in_conversation = State()    # Session open; each new entry is silently recorded


class WorkAddState(StatesGroup):
    """States for the work context adding session (➕ Работа)."""

    waiting_for_input = State()  # Waiting for first material (photo/doc/voice/text)
    in_session = State()         # Session open; each new material is processed


class WorkAskState(StatesGroup):
    """States for the work context querying session (❓ Спросить)."""

    waiting_for_question = State()  # Waiting for first question
    in_session = State()            # Session open; follow-up questions continue
