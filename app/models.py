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
    Column,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    Table,
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
ACCESS_LEVELS = ["use", "manage"]  # per-module access, lowest first (CLAUDE.md §3 Identity & access)
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


class Module(Base):
    """A registered area of the app (engagements, opportunities, team, ...). Adding a module is
    one row here plus grants on the roles that should have it."""

    __tablename__ = "modules"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, server_default="0", default=0)


class AccessRoleGrant(Base):
    __tablename__ = "access_role_grants"
    __table_args__ = (CheckConstraint("level IN ('use', 'manage')", name="ck_access_role_grants_level"),)

    role_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("access_roles.id", ondelete="CASCADE"),
                                               primary_key=True)
    module_key: Mapped[str] = mapped_column(Text, ForeignKey("modules.key"), primary_key=True)
    level: Mapped[str] = mapped_column(Text, nullable=False)


user_access_roles = Table(
    "user_access_roles", Base.metadata,
    Column("user_id", UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True),
    Column("role_id", UUID(as_uuid=True), ForeignKey("access_roles.id", ondelete="CASCADE"), primary_key=True),
)


class AccessRole(Base):
    """A named bundle of module grants. Users hold any number of roles; their effective level
    per module is the highest any of them grants."""

    __tablename__ = "access_roles"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # Granted to every new internal user on first login.
    is_default: Mapped[bool] = mapped_column(Boolean, server_default="false", default=False)
    created_at: Mapped[datetime] = _created_at()

    grants: Mapped[list[AccessRoleGrant]] = relationship(lazy="selectin", cascade="all, delete-orphan")

    @property
    def levels(self) -> dict[str, str]:
        return {g.module_key: g.level for g in self.grants}


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text)
    user_type: Mapped[str] = mapped_column(Enum(*USER_TYPES, name="user_type"), nullable=False)
    # Admin bypasses every module and record check, and is the only one who can edit access.
    is_admin: Mapped[bool] = mapped_column(Boolean, server_default="false", default=False)
    # An employment fact, not an access level: contractors can't grant client access.
    is_contractor: Mapped[bool] = mapped_column(Boolean, server_default="false", default=False)
    status: Mapped[str] = mapped_column(Text, server_default="active", default="active")
    created_at: Mapped[datetime] = _created_at()

    access_roles: Mapped[list[AccessRole]] = relationship(secondary=user_access_roles, lazy="selectin",
                                                          order_by="AccessRole.name")

    @property
    def label(self) -> str:
        return self.display_name or self.email

    def module_level(self, module: str) -> str | None:
        """'manage', 'use' or None: the highest level any of the user's roles grants."""
        if self.user_type != "internal" or self.status != "active":
            return None
        if self.is_admin:
            return "manage"
        best = None
        for role in self.access_roles:
            level = role.levels.get(module)
            if level and (best is None or ACCESS_LEVELS.index(level) > ACCESS_LEVELS.index(best)):
                best = level
        return best

    def can(self, module: str, level: str = "use") -> bool:
        have = self.module_level(module)
        return have is not None and ACCESS_LEVELS.index(have) >= ACCESS_LEVELS.index(level)

    @property
    def bypasses_membership(self) -> bool:
        """Sees every engagement regardless of team membership: engagements:manage (or admin)."""
        return self.can("engagements", "manage")

    @property
    def access_label(self) -> str:
        if self.is_admin:
            return "admin"
        names = [r.name for r in self.access_roles] or ["no access"]
        return ", ".join(names) + (" · contractor" if self.is_contractor else "")


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
    # The deliverable kind that stands for this type's stages (migration → phase). When set,
    # stage state is derived from those deliverables and several stages can be active at
    # once; when NULL (quickstart), the engagement's stage is set by hand.
    stage_kind: Mapped[str | None] = mapped_column(Text)

    kinds: Mapped[list["DeliverableKind"]] = relationship(order_by="DeliverableKind.kind")

    @property
    def stages_derived(self) -> bool:
        return self.stage_kind is not None

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
    # Hand-set stage, only for types whose stages aren't derived (stage_kind IS NULL); NULL otherwise.
    stage: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="active", default="active")
    # The next five columns are part of the §3 table but belong to features outside the
    # PoC (Slack, Harvest, health). They exist in the schema; nothing reads or writes them.
    slack_channel_id: Mapped[str | None] = mapped_column(Text)
    internal_slack_channel_id: Mapped[str | None] = mapped_column(Text)
    harvest_project_id: Mapped[str | None] = mapped_column(Text)
    expected_scope: Mapped[dict | None] = mapped_column(JSONB)
    health: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSONB, server_default="{}", default=dict)
    # The won opportunity this engagement delivers, if any (bridged, never merged).
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("opportunities.id"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()

    client: Mapped[Client] = relationship(back_populates="engagements")
    type: Mapped[EngagementType] = relationship(lazy="joined")
    memberships: Mapped[list[EngagementMembership]] = relationship(back_populates="engagement")
    opportunity: Mapped["Opportunity | None"] = relationship(back_populates="engagements")
    deliverables: Mapped[list["Deliverable"]] = relationship(
        back_populates="engagement", order_by="Deliverable.created_at",
        primaryjoin="Engagement.id == foreign(Deliverable.engagement_id)",
    )

    @property
    def owner(self) -> User | None:
        """The user holding the engagement's (single) 'owner' membership, if any."""
        return next((m.user for m in self.memberships if m.role == "owner"), None)

    @property
    def stage_states(self) -> list:
        """Per-stage state (done / active / upcoming / empty), for either kind of type."""
        from sqlalchemy.orm import object_session

        from app.services.stages import derive_stage_states
        return derive_stage_states(object_session(self), self)

    @property
    def stage_label(self) -> str:
        """The active stage(s), e.g. 'Semantic-layer Parity + Dashboard Build'."""
        from app.services.stages import derive_stage_label
        return derive_stage_label(self.stage_states)


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
    # Parents of this kind (e.g. dashboard) may track most of their children as per-status
    # counts instead of listing every one (deliverables.child_counts).
    bulk_child_counts: Mapped[bool] = mapped_column(Boolean, server_default="false", default=False)


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
    # For stage-kind deliverables (e.g. migration phases): the stage this one stands for.
    stage_key: Mapped[str | None] = mapped_column(Text)
    # For bulk-count parents: {pipeline_status: n} for children that aren't listed individually.
    child_counts: Mapped[dict | None] = mapped_column(JSONB)
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


# ---------------------------------------------------------------- opportunities (CRM)


PRICING_MODELS = {  # key -> (label, unit)
    "fixed_bid": ("Fixed bid", "project"),
    "time_and_materials": ("Time & materials", "hour"),
    "retainer": ("Retainer", "month"),
}
FORECAST_CATEGORIES = {"pipeline": "Pipeline", "best_case": "Best case", "commit": "Commit",
                       "closed": "Closed", "omitted": "Omitted"}
OVERRIDABLE_FORECAST_CATEGORIES = ["pipeline", "best_case", "commit", "omitted"]


class OpportunityStage(Base):
    __tablename__ = "opportunity_stages"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    default_probability: Mapped[int] = mapped_column(Integer, nullable=False)
    forecast_category: Mapped[str] = mapped_column(Text, nullable=False)
    is_closed: Mapped[bool] = mapped_column(Boolean, default=False)
    is_won: Mapped[bool] = mapped_column(Boolean, default=False)


class Product(Base):
    __tablename__ = "products"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    pricing_model: Mapped[str] = mapped_column(Text, nullable=False)
    default_unit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    engagement_type_key: Mapped[str | None] = mapped_column(Text, ForeignKey("engagement_types.key"))
    active: Mapped[bool] = mapped_column(Boolean, server_default="true", default=True)
    created_at: Mapped[datetime] = _created_at()

    engagement_type: Mapped[EngagementType | None] = relationship()

    @property
    def pricing_label(self) -> str:
        return PRICING_MODELS[self.pricing_model][0]

    @property
    def unit(self) -> str:
        return PRICING_MODELS[self.pricing_model][1]


class OpportunityLineItem(Base):
    __tablename__ = "opportunity_line_items"
    __table_args__ = (CheckConstraint("quantity > 0"), CheckConstraint("unit_price >= 0"))

    id: Mapped[uuid.UUID] = _uuid_pk()
    opportunity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("opportunities.id"))
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id"))
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()

    product: Mapped[Product] = relationship(lazy="joined")

    @property
    def total(self) -> Decimal:
        return self.quantity * self.unit_price


class Opportunity(Base):
    __tablename__ = "opportunities"

    id: Mapped[uuid.UUID] = _uuid_pk()
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    stage_key: Mapped[str] = mapped_column(Text, ForeignKey("opportunity_stages.key"), nullable=False)
    probability: Mapped[int | None] = mapped_column(Integer)          # override; NULL = the stage's default
    forecast_category: Mapped[str | None] = mapped_column(Text)       # override; NULL = the stage's default
    close_date: Mapped[date] = mapped_column(Date, nullable=False)
    next_step: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(Text)
    lost_reason: Mapped[str | None] = mapped_column(Text)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()

    client: Mapped[Client] = relationship(lazy="joined")
    owner: Mapped[User | None] = relationship(lazy="joined")
    stage: Mapped[OpportunityStage] = relationship(lazy="joined")
    line_items: Mapped[list[OpportunityLineItem]] = relationship(lazy="selectin",
                                                                 order_by="OpportunityLineItem.created_at")
    engagements: Mapped[list[Engagement]] = relationship(back_populates="opportunity", order_by="Engagement.created_at")

    @property
    def amount(self) -> Decimal:
        """Always the sum of the line items; there is no stored amount to drift."""
        return sum((li.total for li in self.line_items), Decimal(0))

    @property
    def is_closed(self) -> bool:
        return self.stage.is_closed

    @property
    def is_won(self) -> bool:
        return self.stage.is_won

    @property
    def effective_probability(self) -> int:
        """Closed stages are fixed (won 100, lost 0); open ones take the override if set."""
        if self.stage.is_closed or self.probability is None:
            return self.stage.default_probability
        return self.probability

    @property
    def effective_forecast_category(self) -> str:
        if self.stage.is_closed or self.forecast_category is None:
            return self.stage.forecast_category
        return self.forecast_category

    @property
    def weighted_amount(self) -> Decimal:
        return self.amount * self.effective_probability / 100

    @property
    def is_overdue(self) -> bool:
        return not self.is_closed and self.close_date < date.today()


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
