"""ORM models for the PoC slice of CLAUDE.md §3.

Only the tables in PoC scope live here: identity & access, client, engagement +
type system, deliverables, and the events spine. The Alembic migration is the
authoritative DDL (it also carries triggers, CHECK constraints and reference-data
seeds); these models mirror it for the service layer.
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

PIPELINE_STATUSES = ["not_started", "in_progress", "internal_validation", "external_validation", "done"]
ENGAGEMENT_STATUSES = ["active", "paused", "complete", "cancelled"]
USER_TYPES = ["internal", "client_external"]
INTERNAL_ROLES = ["analyst", "contractor", "ops", "admin"]
MEMBERSHIP_ROLES = ["owner", "collaborator", "viewer"]
VIEWER_SCOPES = ["full", "assigned_only"]
REVIEW_VERDICTS = ["accepted", "blocked", "rejected"]
UAT_STATUS = "external_validation"  # client review verdicts can only be given while a deliverable is in UAT


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _created_at() -> Mapped[datetime]:
    # Python-side default so rows created in one transaction still order by creation
    # (Postgres now() is fixed for the whole transaction).
    return mapped_column(DateTime(timezone=True), server_default=func.now(),
                         default=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------- identity & access


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text)
    user_type: Mapped[str] = mapped_column(Enum(*USER_TYPES, name="user_type"), nullable=False)
    role: Mapped[str | None] = mapped_column(Enum(*INTERNAL_ROLES, name="internal_role"))
    status: Mapped[str] = mapped_column(Text, server_default="active", default="active")
    created_at: Mapped[datetime] = _created_at()

    @property
    def label(self) -> str:
        return self.display_name or self.email

    @property
    def bypasses_membership(self) -> bool:
        # CLAUDE.md §2 row 6: ops/admin bypass membership checks for portfolio-wide views.
        return self.user_type == "internal" and self.role in ("ops", "admin")


class EngagementMembership(Base):
    __tablename__ = "engagement_memberships"
    __table_args__ = (
        # At most one 'owner' membership per engagement — the owner membership *is* the
        # engagement's owner; there is no separate owner column (CLAUDE.md §6).
        Index(
            "uq_engagement_memberships_one_owner",
            "engagement_id",
            unique=True,
            postgresql_where=text("role = 'owner'"),
        ),
    )

    engagement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("engagements.id"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True)
    role: Mapped[str] = mapped_column(Enum(*MEMBERSHIP_ROLES, name="membership_role"), nullable=False)
    # Only meaningful for client_external viewers (CLAUDE.md §3 Portal): 'full' = project lead,
    # 'assigned_only' = sees only deliverables they're client owner of (plus parents, for context).
    viewer_scope: Mapped[str] = mapped_column(Text, server_default="full", default="full")

    user: Mapped[User] = relationship(lazy="joined")
    engagement: Mapped["Engagement"] = relationship(back_populates="memberships")


# ---------------------------------------------------------------- client


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    domains: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}", default=list)
    created_at: Mapped[datetime] = _created_at()

    engagements: Mapped[list["Engagement"]] = relationship(back_populates="client", order_by="Engagement.created_at")
    aliases: Mapped[list["ClientAlias"]] = relationship(order_by="ClientAlias.alias")
    contacts: Mapped[list["ClientContact"]] = relationship(order_by="ClientContact.name")


class ClientAlias(Base):
    __tablename__ = "client_aliases"

    id: Mapped[uuid.UUID] = _uuid_pk()
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id"))
    alias: Mapped[str] = mapped_column(Text, nullable=False)
    alias_type: Mapped[str] = mapped_column(Text, nullable=False)  # 'name' | 'acronym' | 'slack_slug'


class ClientContact(Base):
    __tablename__ = "client_contacts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id"))
    name: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    slack_user_id: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(Text)  # 'manual' | 'slack_channel'
    status: Mapped[str] = mapped_column(Text, server_default="active", default="active")


# ---------------------------------------------------------------- engagement + type system


class EngagementType(Base):
    __tablename__ = "engagement_types"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    display_name: Mapped[str | None] = mapped_column(Text)
    storage_mode: Mapped[str] = mapped_column(Text, nullable=False)
    stage_vocab: Mapped[list[dict]] = mapped_column(JSONB, nullable=False)

    kinds: Mapped[list["DeliverableKind"]] = relationship(order_by="DeliverableKind.kind")

    @property
    def stage_keys(self) -> list[str]:
        return [s["key"] for s in self.stage_vocab]

    def stage_label(self, key: str) -> str:
        for s in self.stage_vocab:
            if s["key"] == key:
                return s["label"]
        return key


class Engagement(Base):
    __tablename__ = "engagements"
    __table_args__ = (
        # Lets deliverables carry a composite FK proving their denormalized
        # engagement_type_key matches the parent engagement's type.
        UniqueConstraint("id", "type_key", name="uq_engagements_id_type"),
        CheckConstraint(
            "status IN ('active','paused','complete','cancelled')", name="ck_engagements_status"
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id"))
    type_key: Mapped[str] = mapped_column(Text, ForeignKey("engagement_types.key"))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    stage: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, server_default="active", default="active")
    # The next five columns are part of the §3 table but belong to features outside the
    # PoC (Slack, Harvest, health). They exist in the schema; nothing reads or writes them.
    slack_channel_id: Mapped[str | None] = mapped_column(Text)
    internal_slack_channel_id: Mapped[str | None] = mapped_column(Text)
    harvest_project_id: Mapped[str | None] = mapped_column(Text)
    expected_scope: Mapped[dict | None] = mapped_column(JSONB)
    health: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSONB, server_default="{}", default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()

    client: Mapped[Client] = relationship(back_populates="engagements")
    type: Mapped[EngagementType] = relationship(lazy="joined")
    memberships: Mapped[list[EngagementMembership]] = relationship(back_populates="engagement")
    deliverables: Mapped[list["Deliverable"]] = relationship(
        back_populates="engagement", order_by="Deliverable.created_at",
        primaryjoin="Engagement.id == foreign(Deliverable.engagement_id)",
    )

    @property
    def owner(self) -> User | None:
        """The user holding the engagement's (single) 'owner' membership, if any."""
        return next((m.user for m in self.memberships if m.role == "owner"), None)

    @property
    def stage_label(self) -> str:
        return self.type.stage_label(self.stage)

    @property
    def stage_index(self) -> int:
        keys = self.type.stage_keys
        return keys.index(self.stage) if self.stage in keys else -1


# ---------------------------------------------------------------- deliverables


class DeliverableKind(Base):
    __tablename__ = "deliverable_kinds"
    __table_args__ = (
        # PoC addition (see CLAUDE.md §5): which kind may parent this one, e.g.
        # migration.tile -> dashboard. NULL = a root kind.
        ForeignKeyConstraint(
            ["engagement_type_key", "parent_kind"],
            ["deliverable_kinds.engagement_type_key", "deliverable_kinds.kind"],
            name="fk_deliverable_kinds_parent_kind",
        ),
    )

    engagement_type_key: Mapped[str] = mapped_column(Text, ForeignKey("engagement_types.key"), primary_key=True)
    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    display_name: Mapped[str | None] = mapped_column(Text)
    parent_kind: Mapped[str | None] = mapped_column(Text)
    # Whether client_external users can ever see deliverables of this kind. A type with no
    # client-visible kinds has no client view at all.
    client_visible: Mapped[bool] = mapped_column(Boolean, server_default="false", default=False)


class Deliverable(Base):
    __tablename__ = "deliverables"
    __table_args__ = (
        ForeignKeyConstraint(
            ["engagement_type_key", "kind"],
            ["deliverable_kinds.engagement_type_key", "deliverable_kinds.kind"],
            name="fk_deliverables_kind",
        ),
        ForeignKeyConstraint(
            ["engagement_id", "engagement_type_key"],
            ["engagements.id", "engagements.type_key"],
            name="fk_deliverables_engagement_type",
        ),
        CheckConstraint(
            "pipeline_status IN ('not_started','in_progress','internal_validation','external_validation','done')",
            name="ck_deliverables_pipeline_status",
        ),
        CheckConstraint("parent_id IS NULL OR parent_id <> id", name="ck_deliverables_no_self_parent"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    engagement_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("engagements.id"))
    engagement_type_key: Mapped[str] = mapped_column(Text)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("deliverables.id"))
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    pipeline_status: Mapped[str] = mapped_column(Text, server_default="not_started", default="not_started")
    blocked: Mapped[bool] = mapped_column(Boolean, server_default="false", default=False)
    blocked_reason: Mapped[str | None] = mapped_column(Text)
    not_applicable: Mapped[bool] = mapped_column(Boolean, server_default="false", default=False)
    internal_assignee_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    client_owner_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    priority: Mapped[str | None] = mapped_column(Text)
    target_date: Mapped[date | None] = mapped_column(Date)
    hours_estimated: Mapped[Decimal | None] = mapped_column(Numeric)
    created_at: Mapped[datetime] = _created_at()

    engagement: Mapped[Engagement] = relationship(
        back_populates="deliverables", primaryjoin="foreign(Deliverable.engagement_id) == Engagement.id"
    )
    parent: Mapped["Deliverable | None"] = relationship(remote_side="Deliverable.id", back_populates="children")
    children: Mapped[list["Deliverable"]] = relationship(back_populates="parent", order_by="Deliverable.created_at")
    internal_assignee: Mapped[User | None] = relationship(foreign_keys=[internal_assignee_user_id])
    client_owner: Mapped[User | None] = relationship(foreign_keys=[client_owner_user_id])
    activity: Mapped[list["DeliverableActivity"]] = relationship(
        order_by="DeliverableActivity.created_at.desc()"
    )


class DeliverableBlocker(Base):
    __tablename__ = "deliverable_blockers"
    __table_args__ = (CheckConstraint("blocked_id <> blocker_id", name="ck_deliverable_blockers_no_self_loop"),)

    blocked_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("deliverables.id"), primary_key=True)
    blocker_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("deliverables.id"), primary_key=True)


class DeliverableActivity(Base):
    __tablename__ = "deliverable_activity"

    id: Mapped[uuid.UUID] = _uuid_pk()
    deliverable_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("deliverables.id"))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    kind: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()

    actor: Mapped[User | None] = relationship()


class DeliverableComment(Base):
    """The client-facing thread. Structurally separate from deliverable_activity (internal
    notes), so internal chatter can't leak to a client through a mis-set flag."""

    __tablename__ = "deliverable_comments"

    id: Mapped[uuid.UUID] = _uuid_pk()
    deliverable_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("deliverables.id"))
    author_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _created_at()

    author: Mapped[User] = relationship()


class DeliverableClientReview(Base):
    """Append-only verdict history; the current verdict is the latest row."""

    __tablename__ = "deliverable_client_reviews"
    __table_args__ = (
        CheckConstraint("verdict IN ('accepted','blocked','rejected')", name="ck_deliverable_client_reviews_verdict"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    deliverable_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("deliverables.id"))
    reviewer_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    verdict: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _created_at()

    reviewer: Mapped[User] = relationship()


# ---------------------------------------------------------------- events — the spine


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    entity_type: Mapped[str | None] = mapped_column(Text)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    event_type: Mapped[str | None] = mapped_column(Text)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    payload: Mapped[dict | None] = mapped_column(JSONB)

    actor: Mapped[User | None] = relationship()
