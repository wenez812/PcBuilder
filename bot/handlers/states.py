"""Состояния FSM для пошаговых сценариев."""
from aiogram.fsm.state import State, StatesGroup


class BuildStates(StatesGroup):
    custom_budget = State()
    phrase = State()
    save_name = State()


class PickerStates(StatesGroup):
    search = State()


class MyBuildsStates(StatesGroup):
    rename = State()


class UpgradeStates(StatesGroup):
    budget = State()


class AdminStates(StatesGroup):
    components_file = State()
    prices_file = State()
    benchmarks_file = State()
    broadcast_text = State()
    search = State()
