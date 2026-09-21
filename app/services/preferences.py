from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SubscriptionPreference

PROBLEM_STATUSES = {
    "customs",
    "available_for_pickup",
    "delivery_failed",
    "exception",
    "returned",
}


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
