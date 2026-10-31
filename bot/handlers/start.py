"""/start (в т.ч. ссылки на сборки), /menu, /help и возврат в главное меню."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.handlers.builds import show_shared
from bot.keyboards import Cb, back_to_menu_kb, main_menu_kb
from bot.models import User
from bot.services import user_service
from bot.utils import texts
from bot.utils.telegram import show
from config import Settings


def build_router() -> Router:
    router = Router(name="start")

    @router.message(CommandStart())
    async def cmd_start(message: Message, command: CommandObject, state: FSMContext, session: AsyncSession,
                        db_user: User, settings: Settings) -> None:
        await state.clear()
        await user_service.log_usage(session, db_user, "start")
        args = (command.args or "").strip()
        if args.startswith("b_"):
            await show_shared(message, session, args[2:])
            return
        await message.answer(texts.WELCOME, reply_markup=main_menu_kb(settings.is_admin(message.from_user.id)))

    @router.message(Command("menu"))
    async def cmd_menu(message: Message, state: FSMContext, session: AsyncSession, db_user: User,
                       settings: Settings) -> None:
        await state.clear()
        await user_service.log_usage(session, db_user, "menu")
        await message.answer(texts.MAIN_MENU, reply_markup=main_menu_kb(settings.is_admin(message.from_user.id)))

    @router.callback_query(F.data == Cb.MAIN)
    async def cb_main_menu(callback: CallbackQuery, state: FSMContext, settings: Settings) -> None:
        await state.clear()
        await callback.answer()
        await show(callback, texts.MAIN_MENU, main_menu_kb(settings.is_admin(callback.from_user.id)))

    @router.message(Command("help"))
    async def cmd_help(message: Message, session: AsyncSession, db_user: User) -> None:
        await user_service.log_usage(session, db_user, "help")
        await message.answer(texts.HELP, reply_markup=back_to_menu_kb())

    @router.callback_query(F.data == Cb.HELP)
    async def cb_help(callback: CallbackQuery, session: AsyncSession, db_user: User) -> None:
        await callback.answer()
        await user_service.log_usage(session, db_user, "help")
        await show(callback, texts.HELP, back_to_menu_kb())

    return router
