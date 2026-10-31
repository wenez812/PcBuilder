from bot.keyboards import Cb, confirm_delete_kb, main_menu_kb, profile_kb


def _buttons(markup):
    return [b for row in markup.inline_keyboard for b in row]


def test_main_menu_has_all_required_buttons():
    texts = [b.text for b in _buttons(main_menu_kb())]
    assert texts == [
        "🖥 Собрать ПК",
        "🔧 Улучшить мой ПК",
        "⚙️ Проверить совместимость",
        "🎮 FPS в играх",
        "💰 Сравнить цены",
        "📦 Мои сборки",
        "👤 Профиль",
        "❓ Помощь",
    ]


def test_callback_data_within_telegram_limit():
    for markup in (main_menu_kb(), profile_kb(True), profile_kb(False), confirm_delete_kb()):
        for button in _buttons(markup):
            assert len(button.callback_data.encode()) <= 64


def test_admin_button_only_for_admins():
    assert Cb.ADMIN not in {b.callback_data for b in _buttons(main_menu_kb())}
    assert Cb.ADMIN in {b.callback_data for b in _buttons(main_menu_kb(is_admin=True))}


def test_submenus_have_navigation():
    for markup in (profile_kb(True), confirm_delete_kb()):
        callbacks = {b.callback_data for b in _buttons(markup)}
        assert Cb.MAIN in callbacks
    assert Cb.PROFILE in {b.callback_data for b in _buttons(confirm_delete_kb())}
