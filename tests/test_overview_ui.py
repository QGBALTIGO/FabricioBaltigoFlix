from types import SimpleNamespace

from app.bot.smart import (
    _overview_list_copy,
    _overview_summary_lines,
)


def _buckets(**counts):
    keys = (
        "out_for_delivery",
        "near",
        "attention",
        "transit",
        "delivered_today",
    )
    return {
        key: [
            SimpleNamespace()
            for _ in range(counts.get(key, 0))
        ]
        for key in keys
    }


def test_overview_hides_zero_categories_and_uses_natural_copy():
    lines = _overview_summary_lines(
        _buckets(
            transit=1,
            delivered_today=1,
        )
    )

    text = "\n".join(lines)

    assert lines == [
        "🚚 <b>1 em trânsito</b>",
        "✅ <b>1 entregue hoje</b>",
    ]
    assert "0" not in text
    assert "encomenda(s)" not in text
    assert "Prioridades" not in text


def test_overview_plural_copy_is_natural():
    lines = _overview_summary_lines(
        _buckets(
            out_for_delivery=2,
            near=3,
            attention=2,
            transit=4,
            delivered_today=5,
        )
    )

    assert "🛵 <b>2 saíram para entrega</b>" in lines
    assert "📍 <b>3 chegaram à região de destino</b>" in lines
    assert "⚠️ <b>2 precisam de atenção</b>" in lines
    assert "🚚 <b>4 em trânsito</b>" in lines
    assert "✅ <b>5 entregues hoje</b>" in lines


def test_overview_derived_lists_use_singular_and_plural():
    title, description = _overview_list_copy(
        "transit",
        1,
    )
    assert title == "🚚 <b>Em trânsito</b>"
    assert description == "1 encomenda segue em movimentação."

    title, description = _overview_list_copy(
        "delivered_today",
        2,
    )
    assert title == "✅ <b>Entregues hoje</b>"
    assert description == "2 encomendas foram entregues hoje."
