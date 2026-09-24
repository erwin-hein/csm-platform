import re
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.access import get_visible_client, require_ops_or_admin, visible_clients_stmt
from app.errors import ValidationError
from app.events import emit, mutation
from app.models import Client, ClientAlias, ClientContact, User

ALIAS_TYPES = ("name", "acronym", "slack_slug")


def list_visible_clients(db: Session, user: User) -> list[Client]:
    return list(db.scalars(visible_clients_stmt(user).order_by(Client.name)))


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "client"


def _unique_slug(db: Session, base: str) -> str:
    slug, n = base, 2
    while db.scalar(select(Client.id).where(Client.slug == slug)):
        slug, n = f"{base}-{n}", n + 1
    return slug


def _parse_domains(domains: str | list[str]) -> list[str]:
    items = domains.split(",") if isinstance(domains, str) else domains
    out = []
    for d in items:
        d = d.strip().lower().removeprefix("@")
        if not d:
            continue
        if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", d):
            raise ValidationError(f"'{d}' doesn't look like a domain")
        if d not in out:
            out.append(d)
    return out


@mutation("client_created")
def create_client(db: Session, actor: User, *, name: str, domains: str | list[str] = "") -> Client:
    require_ops_or_admin(actor)
    name = name.strip()
    if not name:
        raise ValidationError("Client name is required")
    client = Client(name=name, slug=_unique_slug(db, _slugify(name)), domains=_parse_domains(domains))
    db.add(client)
    db.flush()
    emit(db, entity_type="client", entity_id=client.id, event_type="client_created", actor=actor,
         payload={"name": client.name, "slug": client.slug, "domains": client.domains})
    return client


@mutation("client_alias_added")
def add_alias(db: Session, actor: User, client_id: uuid.UUID, *, alias: str, alias_type: str) -> ClientAlias:
    require_ops_or_admin(actor)
    client = get_visible_client(db, actor, client_id)
    alias = alias.strip()
    if not alias:
        raise ValidationError("Alias is required")
    if alias_type not in ALIAS_TYPES:
        raise ValidationError(f"Alias type must be one of {', '.join(ALIAS_TYPES)}")
    row = ClientAlias(client_id=client.id, alias=alias, alias_type=alias_type)
    db.add(row)
    db.flush()
    emit(db, entity_type="client", entity_id=client.id, event_type="client_alias_added", actor=actor,
         payload={"alias_id": row.id, "alias": alias, "alias_type": alias_type})
    return row


@mutation("client_contact_added")
def add_contact(db: Session, actor: User, client_id: uuid.UUID, *, name: str, email: str = "",
                title: str = "") -> ClientContact:
    require_ops_or_admin(actor)
    client = get_visible_client(db, actor, client_id)
    if not name.strip():
        raise ValidationError("Contact name is required")
    row = ClientContact(client_id=client.id, name=name.strip(), email=email.strip().lower() or None,
                        title=title.strip() or None, source="manual")
    db.add(row)
    db.flush()
    emit(db, entity_type="client", entity_id=client.id, event_type="client_contact_added", actor=actor,
         payload={"contact_id": row.id, "name": row.name, "email": row.email})
    return row
