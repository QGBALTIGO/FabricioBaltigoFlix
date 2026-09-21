from app.bot.rich import _markup_payload


class DummyMarkup:
    def to_dict(self):
        return {"inline_keyboard": [[{"text": "Teste", "callback_data": "x"}]]}


def test_rich_markup_serialization():
    assert _markup_payload(DummyMarkup()) == {
        "inline_keyboard": [[{"text": "Teste", "callback_data": "x"}]]
    }
