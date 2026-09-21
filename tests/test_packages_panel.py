from types import SimpleNamespace

from app.bot.handlers import (
    _add_package_help_text,
    _my_packages_text,
)
from app.bot.keyboards import (
    add_package_help_keyboard,
    list_keyboard,
)


def _sub():
    return SimpleNamespace(
        id=7,
        nickname="Teclado gamer",
        shipment=SimpleNamespace(
            status="in_transit",
            carrier_name="Correios",
            tracking_number="AB123456789BR",
        ),
    )


def test_my_packages_text_singular():
    text = _my_packages_text(1)

    assert "📦 <b>Meus pacotes</b>" in text
    assert "<b>1 encomenda</b>" in text
    assert "1 rastreio(s)" not in text


def test_active_list_has_add_package_button():
    markup = list_keyboard(
        [_sub()],
        page=0,
        total_pages=1,
        list_kind="active",
    )

    texts = [
        button.text
        for row in markup.inline_keyboard
        for button in row
    ]

    assert "📦 Teclado gamer" in texts
    assert "➕ Adicionar encomenda" in texts


def test_add_package_help_is_clear_and_has_back():
    text = _add_package_help_text()
    markup = add_package_help_keyboard()

    assert "AB123456789BR" in text
    assert "Teclado gamer" in text
    assert "nome é opcional" in text
    assert (
        markup.inline_keyboard[0][0].callback_data
        == "packages:back"
    )
