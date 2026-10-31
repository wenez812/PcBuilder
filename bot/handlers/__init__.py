from aiogram import Router

from bot.handlers import admin, build, builds, compat, fps, misc, picker, prices, profile, start, upgrade


def build_main_router() -> Router:
    """Каждый вызов создаёт новые роутеры, поэтому Dispatcher можно собирать повторно (тесты).

    Порядок важен: команды и меню -> админка -> общие сценарии -> запасные обработчики.
    """
    router = Router(name="main")
    router.include_routers(
        start.build_router(),
        admin.build_router(),
        admin.build_denied_router(),
        picker.build_router(),
        compat.build_router(),
        build.build_router(),
        builds.build_router(),
        upgrade.build_router(),
        fps.build_router(),
        prices.build_router(),
        profile.build_router(),
        misc.build_router(),
    )
    return router
