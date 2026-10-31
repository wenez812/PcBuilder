"""Клавиатуры главного меню, профиля и общие кнопки навигации."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


class Cb:
    """Значения callback_data. Все короче 64 байт (лимит Telegram)."""

    MAIN = "menu:main"
    BUILD = "menu:build"
    UPGRADE = "menu:upgrade"
    COMPAT = "menu:compat"
    FPS = "menu:fps"
    PRICES = "menu:prices"
    BUILDS = "menu:builds"
    PROFILE = "menu:profile"
    HELP = "menu:help"
    ADMIN = "menu:admin"
    PROFILE_HISTORY = "profile:history"

    PROFILE_TOGGLE_NOTIFY = "profile:notify"
    PROFILE_DELETE = "profile:delete"
    PROFILE_DELETE_CONFIRM = "profile:delete_yes"


def nav_row(*, back: str | None = None, main: bool = True) -> list[InlineKeyboardButton]:
    row: list[InlineKeyboardButton] = []
    if back:
        row.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=back))
    if main:
        row.append(InlineKeyboardButton(text="🏠 Главное меню", callback_data=Cb.MAIN))
    return row


def main_menu_kb(is_admin: bool = False) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🖥 Собрать ПК", callback_data=Cb.BUILD)
    b.button(text="🔧 Улучшить мой ПК", callback_data=Cb.UPGRADE)
    b.button(text="⚙️ Проверить совместимость", callback_data=Cb.COMPAT)
    b.button(text="🎮 FPS в играх", callback_data=Cb.FPS)
    b.button(text="💰 Сравнить цены", callback_data=Cb.PRICES)
    b.button(text="📦 Мои сборки", callback_data=Cb.BUILDS)
    b.button(text="👤 Профиль", callback_data=Cb.PROFILE)
    b.button(text="❓ Помощь", callback_data=Cb.HELP)
    layout = [1, 1, 1, 2, 2, 1]
    if is_admin:
        b.button(text="🛠 Админ-панель", callback_data=Cb.ADMIN)
        layout.append(1)
    b.adjust(*layout)
    return b.as_markup()


def back_to_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[nav_row()])


def profile_kb(notifications_enabled: bool) -> InlineKeyboardMarkup:
    toggle_text = "🔕 Выключить уведомления" if notifications_enabled else "🔔 Включить уведомления"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=toggle_text, callback_data=Cb.PROFILE_TOGGLE_NOTIFY)],
            [InlineKeyboardButton(text="📜 История подборов", callback_data=Cb.PROFILE_HISTORY)],
            [InlineKeyboardButton(text="🗑 Удалить мои данные", callback_data=Cb.PROFILE_DELETE)],
            nav_row(),
        ]
    )


def confirm_delete_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да, удалить всё", callback_data=Cb.PROFILE_DELETE_CONFIRM)],
            nav_row(back=Cb.PROFILE),
        ]
    )
