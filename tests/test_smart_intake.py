from types import SimpleNamespace

from app import intake


def test_extracts_correios_code_and_store_metadata_from_message():
    text = """Shopee
Pedido #778899
Produto: Teclado mecânico sem fio
Seu código de rastreio é AB123456789BR
"""

    items = intake.extract_tracking_candidates(
        text,
        source="forwarded_message",
    )

    assert items
    candidate = items[0]
    assert candidate.number == "AB123456789BR"
    assert candidate.store_name == "Shopee"
    assert candidate.order_number == "778899"
    assert candidate.product_name == "Teclado mecânico sem fio"
    assert candidate.nickname == "Shopee • Teclado mecânico sem fio"
    assert candidate.confidence == 1.0


def test_extracts_generic_code_only_with_tracking_context():
    items = intake.extract_tracking_candidates(
        "J&T: rastreio JD0146000123456789",
    )

    assert items
    assert items[0].number == "JD0146000123456789"


def test_does_not_turn_plain_order_number_into_tracking():
    items = intake.extract_tracking_candidates(
        "Seu pedido 123456789012 foi aprovado na loja.",
    )

    assert items == []


def test_broad_token_requires_strong_tracking_context():
    assert (
        intake.extract_tracking_candidates(
            "Referência ABCD123456789 concluída."
        )
        == []
    )

    items = intake.extract_tracking_candidates(
        "Sua encomenda está em trânsito. Código ABCD123456789."
    )
    assert items
    assert items[0].number == "ABCD123456789"


def test_ocr_pipeline_returns_engine_text(monkeypatch):
    class FakeEngine:
        def __call__(self, path):
            assert path
            return SimpleNamespace(
                txts=(
                    "Shopee",
                    "Código de rastreio AB123456789BR",
                )
            )

    monkeypatch.setattr(
        intake,
        "_get_ocr_engine",
        lambda: FakeEngine(),
    )

    text = intake.ocr_text_from_image_bytes(
        b"not-a-real-image-needed-by-fake-engine",
        suffix=".png",
    )

    assert "Shopee" in text
    assert "AB123456789BR" in text
