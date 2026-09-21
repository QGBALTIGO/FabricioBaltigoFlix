from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    language: Mapped[str] = mapped_column(String(16), default="pt-BR")
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    subscriptions: Mapped[list["Subscription"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Shipment(Base):
    __tablename__ = "shipments"
    __table_args__ = (
        Index(
            "ix_shipment_poll_active_registered",
            "is_active",
            "status",
            "registered_at",
        ),
        Index(
            "ix_shipment_last_event_at",
            "last_event_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tracking_number: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    provider: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    provider_tracking_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    carrier_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    carrier_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="unknown", index=True)
    status_raw: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_location: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_event_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    extra_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    subscriptions: Mapped[list["Subscription"]] = relationship(
        back_populates="shipment", cascade="all, delete-orphan"
    )
    events: Mapped[list["TrackingEvent"]] = relationship(
        back_populates="shipment", cascade="all, delete-orphan"
    )


class Subscription(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (
        UniqueConstraint("user_id", "shipment_id", name="uq_user_shipment"),
        Index("ix_subscription_user_active", "user_id", "is_active"),
        Index(
            "ix_subscription_shipment_notify",
            "shipment_id",
            "is_active",
            "notifications_enabled",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    shipment_id: Mapped[int] = mapped_column(
        ForeignKey("shipments.id", ondelete="CASCADE"), index=True
    )
    nickname: Mapped[str | None] = mapped_column(String(120), nullable=True)
    notify_level: Mapped[str] = mapped_column(String(20), default="important")
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    user: Mapped[User] = relationship(back_populates="subscriptions")
    shipment: Mapped[Shipment] = relationship(back_populates="subscriptions")


class TrackingEvent(Base):
    __tablename__ = "tracking_events"
    __table_args__ = (
        UniqueConstraint("shipment_id", "event_hash", name="uq_shipment_eventhash"),
        Index("ix_event_shipment_time", "shipment_id", "event_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shipment_id: Mapped[int] = mapped_column(
        ForeignKey("shipments.id", ondelete="CASCADE"), index=True
    )
    event_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(50), default="unknown")
    status_raw: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")
    location: Mapped[str | None] = mapped_column(String(500), nullable=True)
    event_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    shipment: Mapped[Shipment] = relationship(back_populates="events")


class NotificationLog(Base):
    __tablename__ = "notification_logs"
    __table_args__ = (
        UniqueConstraint(
            "subscription_id",
            "kind",
            "dedupe_key",
            name="uq_notification_log_dedupe",
        ),
        Index("ix_notification_kind_created", "kind", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subscription_id: Mapped[int] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(40), index=True)
    dedupe_key: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class ProviderHealth(Base):
    __tablename__ = "provider_health"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    quarantined_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_failure_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)


class PollingState(Base):
    __tablename__ = "polling_states"

    shipment_id: Mapped[int] = mapped_column(
        ForeignKey("shipments.id", ondelete="CASCADE"),
        primary_key=True,
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_check_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)


class UserPreference(Base):
    __tablename__ = "user_preferences"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    quiet_hours_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )
    quiet_start_minute: Mapped[int] = mapped_column(
        Integer,
        default=23 * 60,
    )
    quiet_end_minute: Mapped[int] = mapped_column(
        Integer,
        default=7 * 60,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
    )


class SubscriptionPreference(Base):
    __tablename__ = "subscription_preferences"

    subscription_id: Mapped[int] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    custom_alerts_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )
    alert_out_for_delivery: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
    )
    alert_delivered: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
    )
    alert_problems: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
    )
    alert_intermediate: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
    )
    store_name: Mapped[str | None] = mapped_column(
        String(120),
        nullable=True,
    )
    order_number: Mapped[str | None] = mapped_column(
        String(120),
        nullable=True,
    )
    product_name: Mapped[str | None] = mapped_column(
        String(180),
        nullable=True,
    )
    category: Mapped[str | None] = mapped_column(
        String(80),
        nullable=True,
    )
    source: Mapped[str] = mapped_column(
        String(40),
        default="manual",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
    )


class DeferredNotification(Base):
    __tablename__ = "deferred_notifications"
    __table_args__ = (
        UniqueConstraint(
            "subscription_id",
            "event_id",
            name="uq_deferred_notification_event",
        ),
        Index(
            "ix_deferred_notification_deliver_after",
            "deliver_after",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )
    subscription_id: Mapped[int] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="CASCADE"),
        index=True,
    )
    event_id: Mapped[int] = mapped_column(
        ForeignKey("tracking_events.id", ondelete="CASCADE"),
        index=True,
    )
    event_count: Mapped[int] = mapped_column(
        Integer,
        default=1,
    )
    deliver_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
    )
