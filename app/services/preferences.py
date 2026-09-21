from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    SubscriptionPreference,
    UserPreference,
)

PROBLEM_STATUSES = {
    "customs",
    "available_for_pickup",
    "delivery_failed",
    "exception",
    "returned",
}


async def get_user_preference(
    session: AsyncSession,
    user_id: int,
    *,
    create: bool = True,
) -> UserPreference | None:
    pref = await session.get(
        UserPreference,
        int(user_id),
    )
    if pref is None and create:
        pref = UserPreference(
            user_id=int(user_id),
        )
        session.add(pref)
        await session.flush()
    return pref


async def get_subscription_preference(
    session: AsyncSession,
    subscription_id: int,
    *,
    create: bool = True,
) -> SubscriptionPreference | None:
    pref = await session.get(
        SubscriptionPreference,
        int(subscription_id),
    )
    if pref is None and create:
        pref = SubscriptionPreference(
            subscription_id=int(subscription_id),
        )
        session.add(pref)
        await session.flush()
    return pref


def custom_alert_allows(
    pref: SubscriptionPreference | None,
    status: str,
) -> bool:
    if pref is None or not pref.custom_alerts_enabled:
        return True

    normalized = str(status or "unknown")

    if normalized == "out_for_delivery":
        return bool(pref.alert_out_for_delivery)

    if normalized == "delivered":
        return bool(pref.alert_delivered)

    if normalized in PROBLEM_STATUSES:
        return bool(pref.alert_problems)

    return bool(pref.alert_intermediate)


def _safe_zone(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except Exception:
        return ZoneInfo("UTC")


def quiet_window(
    pref: UserPreference | None,
    timezone_name: str,
    *,
    now: datetime | None = None,
) -> tuple[bool, datetime | None]:
    if pref is None or not pref.quiet_hours_enabled:
        return False, None

    tz = _safe_zone(timezone_name)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    local_now = current.astimezone(tz)

    start = max(0, min(1439, int(pref.quiet_start_minute)))
    end = max(0, min(1439, int(pref.quiet_end_minute)))
    minute = local_now.hour * 60 + local_now.minute

    if start == end:
        return False, None

    if start < end:
        quiet = start <= minute < end
        if not quiet:
            return False, None
        end_date = local_now.date()
    else:
        quiet = minute >= start or minute < end
        if not quiet:
            return False, None
        end_date = (
            local_now.date() + timedelta(days=1)
            if minute >= start
            else local_now.date()
        )

    end_local = datetime.combine(
        end_date,
        time(
            hour=end // 60,
            minute=end % 60,
        ),
        tzinfo=tz,
    )
    return True, end_local.astimezone(timezone.utc)


def quiet_label(pref: UserPreference | None) -> str:
    if pref is None or not pref.quiet_hours_enabled:
        return "Desativado"

    def fmt(value: int) -> str:
        value = max(0, min(1439, int(value)))
        return f"{value // 60:02d}:{value % 60:02d}"

    return (
        f"{fmt(pref.quiet_start_minute)}–"
        f"{fmt(pref.quiet_end_minute)}"
    )


async def apply_intake_metadata(
    session: AsyncSession,
    subscription_id: int,
    *,
    store_name: str | None = None,
    order_number: str | None = None,
    product_name: str | None = None,
    source: str | None = None,
) -> SubscriptionPreference:
    pref = await get_subscription_preference(
        session,
        int(subscription_id),
        create=True,
    )
    assert pref is not None

    if store_name:
        pref.store_name = str(store_name)[:120]
    if order_number:
        pref.order_number = str(order_number)[:120]
    if product_name:
        pref.product_name = str(product_name)[:180]
    if source:
        pref.source = str(source)[:40]

    await session.flush()
    return pref
