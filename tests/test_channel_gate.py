from types import SimpleNamespace

from app.bot.handlers import _is_channel_member


def test_required_channel_membership_statuses():
    assert _is_channel_member(
        SimpleNamespace(status="member")
    )
    assert _is_channel_member(
        SimpleNamespace(status="administrator")
    )
    assert _is_channel_member(
        SimpleNamespace(status="creator")
    )


def test_required_channel_restricted_membership():
    assert _is_channel_member(
        SimpleNamespace(
            status="restricted",
            is_member=True,
        )
    )
    assert not _is_channel_member(
        SimpleNamespace(
            status="restricted",
            is_member=False,
        )
    )


def test_required_channel_rejects_left_and_banned():
    assert not _is_channel_member(
        SimpleNamespace(status="left")
    )
    assert not _is_channel_member(
        SimpleNamespace(status="kicked")
    )
    assert not _is_channel_member(
        SimpleNamespace(status="banned")
    )
