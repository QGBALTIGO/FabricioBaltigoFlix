from app.providers.correios_direct import CorreiosDirectProvider
from app.providers.jadlog_direct import JadlogDirectProvider
from app.providers.total_express_direct import TotalExpressDirectProvider


def test_direct_provider_detection():
    assert CorreiosDirectProvider.can_handle("AA123456789BR")
    assert not CorreiosDirectProvider.can_handle("123456789")

    assert JadlogDirectProvider.can_handle("12345678901234")
    assert not JadlogDirectProvider.can_handle("AA123456789BR")

    assert TotalExpressDirectProvider.can_handle("BR621368086XP")
    assert TotalExpressDirectProvider.can_handle("TE123456789012")
    assert not TotalExpressDirectProvider.can_handle("AA123456789BR")
