from app.bot.keyboards import history_keyboard


def test_history_keyboard_has_navigation_and_back():
    markup = history_keyboard(
        sub_id=7,
        page=1,
        total_pages=3,
    )

    rows = markup.inline_keyboard

    assert rows[0][0].callback_data == "history:7:0"
    assert rows[0][1].text == "2/3"
    assert rows[0][2].callback_data == "history:7:2"
    assert rows[1][0].callback_data == "back:7"
    assert rows[1][0].text == "⬅️ Voltar ao rastreio"
