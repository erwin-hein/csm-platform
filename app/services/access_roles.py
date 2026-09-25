"""Module access administration (CLAUDE.md §3 Identity & access). Admin only.

Access roles are named bundles of per-module grants ('use' | 'manage'). A user holds any
number of roles and gets the highest level any of them grants per module, so every
combination of modules is expressible without per-user toggles, and a new module is one
`modules` row plus grants on the roles that should have it.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.access import MODULES, require_admin
from app.errors import NotFound, ValidationError
from app.events import emit, mutation
from app.models import ACCESS_LEVELS, AccessRole, AccessRoleGrant, Module, User


def list_modules(db: Session) -> list[Module]:
    return list(db.scalars(select(Module).order_by(Module.sort_order)))


def list_access_roles(db: Session) -> list[AccessRole]:
    return list(db.scalars(select(AccessRole).order_by(AccessRole.name)))


def _role(db: Session, role_id: uuid.UUID) -> AccessRole:
    role = db.get(AccessRole, role_id)
    if role is None:
        raise NotFound("Access role not found")
    return role


@mutation("access_role_created")
def create_access_role(db: Session, actor: User, *, name: str, description: str = "") -> AccessRole:
    require_admin(actor)
    name = name.strip()
    if not name:
        raise ValidationError("Role name is required")
    if db.scalar(select(AccessRole.id).where(AccessRole.name == name)):
        raise ValidationError(f"A role called '{name}' already exists")
    role = AccessRole(name=name, description=description.strip() or None)
    db.add(role)
    db.flush()
    emit(db, entity_type="access_role", entity_id=role.id, event_type="access_role_created", actor=actor,
         payload={"name": name})
    return role


@mutation("access_role_updated")
def update_access_role(db: Session, actor: User, role_id: uuid.UUID, *, name: str | None = None,
                       description: str | None = None, is_default: bool | None = None) -> AccessRole:
    require_admin(actor)
    role = _role(db, role_id)
    changes = {}
    if name is not None and name.strip() != role.name:
        name = name.strip()
        if not name:
            raise ValidationError("Role name is required")
        if db.scalar(select(AccessRole.id).where(AccessRole.name == name, AccessRole.id != role.id)):
            raise ValidationError(f"A role called '{name}' already exists")
        changes["name"], role.name = [role.name, name], name
    if description is not None and (description.strip() or None) != role.description:
        changes["description"], role.description = [role.description, description.strip() or None], \
            description.strip() or None
    if is_default is not None and is_default != role.is_default:
        changes["is_default"], role.is_default = [role.is_default, is_default], is_default
    if not changes:
        raise ValidationError("Nothing to change")
    emit(db, entity_type="access_role", entity_id=role.id, event_type="access_role_updated", actor=actor,
         payload=changes)
    return role


@mutation("access_role_grant_changed")
def set_role_grant(db: Session, actor: User, role_id: uuid.UUID, *, module_key: str,
                   level: str | None) -> AccessRole:
    """Set a role's level on one module; None removes the grant."""
    require_admin(actor)
    role = _role(db, role_id)
    if module_key not in MODULES or db.get(Module, module_key) is None:
        raise ValidationError(f"Unknown module '{module_key}'")
    if level is not None and level not in ACCESS_LEVELS:
        raise ValidationError(f"Level must be one of {', '.join(ACCESS_LEVELS)}, or none")
    grant = next((g for g in role.grants if g.module_key == module_key), None)
    previous = grant.level if grant else None
    if previous == level:
        raise ValidationError("Nothing to change")
    if level is None:
        role.grants.remove(grant)
    elif grant is None:
        role.grants.append(AccessRoleGrant(module_key=module_key, level=level))
    else:
        grant.level = level
    db.flush()
    emit(db, entity_type="access_role", entity_id=role.id, event_type="access_role_grant_changed", actor=actor,
         payload={"role": role.name, "module": module_key, "from": previous, "to": level})
    return role


def _internal_user(db: Session, user_id: uuid.UUID) -> User:
    user = db.get(User, user_id)
    if user is None or user.user_type != "internal":
        raise NotFound("User not found")
    return user


@mutation("user_access_changed")
def set_user_roles(db: Session, actor: User, user_id: uuid.UUID, *, role_ids: list[uuid.UUID]) -> User:
    require_admin(actor)
    user = _internal_user(db, user_id)
    roles = [_role(db, rid) for rid in dict.fromkeys(role_ids)]
    before = sorted(r.name for r in user.access_roles)
    after = sorted(r.name for r in roles)
    if before == after:
        raise ValidationError("Nothing to change")
    user.access_roles = roles
    db.flush()
    emit(db, entity_type="user", entity_id=user.id, event_type="user_access_changed", actor=actor,
         payload={"email": user.email, "from": before, "to": after})
    return user


@mutation("user_flags_changed")
def set_user_flags(db: Session, actor: User, user_id: uuid.UUID, *, is_admin: bool | None = None,
                   is_contractor: bool | None = None) -> User:
    require_admin(actor)
    user = _internal_user(db, user_id)
    changes = {}
    if is_admin is not None and is_admin != user.is_admin:
        if user.id == actor.id and not is_admin:
            raise ValidationError("You can't remove your own admin access")
        changes["is_admin"], user.is_admin = [user.is_admin, is_admin], is_admin
    if is_contractor is not None and is_contractor != user.is_contractor:
        changes["is_contractor"], user.is_contractor = [user.is_contractor, is_contractor], is_contractor
    if not changes:
        raise ValidationError("Nothing to change")
    emit(db, entity_type="user", entity_id=user.id, event_type="user_flags_changed", actor=actor,
         payload={"email": user.email, **changes})
    return user
