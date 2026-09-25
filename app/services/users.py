from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.errors import ValidationError
from app.events import emit, mutation
from app.models import AccessRole, User


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == email.strip().lower()))


def list_internal_users(db: Session) -> list[User]:
    return list(
        db.scalars(
            select(User).where(User.user_type == "internal", User.status == "active").order_by(User.display_name)
        )
    )


def list_client_users(db: Session) -> list[User]:
    return list(db.scalars(select(User).where(User.user_type == "client_external", User.status == "active")
                           .order_by(User.display_name)))


def is_allowed_internal_email(email: str) -> bool:
    return email.strip().lower().endswith("@" + settings.allowed_domain)


@mutation("user_created")
def create_internal_user(db: Session, actor: User | None, *, email: str, display_name: str | None,
                         is_admin: bool | None = None, roles: list[str] | None = None,
                         is_contractor: bool = False) -> User:
    """Create an internal user. ADMIN_EMAILS decides admin when is_admin isn't given
    (CLAUDE.md §2 row 16 bootstrap). Without explicit roles (by name), the user gets the
    default access roles (Delivery, out of the box)."""
    email = email.strip().lower()
    if not is_allowed_internal_email(email):
        raise ValidationError(f"Only @{settings.allowed_domain} accounts can be internal users")
    if is_admin is None:
        is_admin = email in settings.admin_emails
    if roles is None:
        granted = list(db.scalars(select(AccessRole).where(AccessRole.is_default.is_(True))))
    else:
        granted = list(db.scalars(select(AccessRole).where(AccessRole.name.in_(roles))))
        missing = set(roles) - {r.name for r in granted}
        if missing:
            raise ValidationError(f"Unknown access role(s): {', '.join(sorted(missing))}")
    user = User(email=email, display_name=display_name or email.split("@")[0], user_type="internal",
                is_admin=is_admin, is_contractor=is_contractor, access_roles=granted)
    db.add(user)
    db.flush()
    emit(db, entity_type="user", entity_id=user.id, event_type="user_created",
         actor=actor or user, payload={"email": email, "is_admin": is_admin, "is_contractor": is_contractor,
                                       "roles": [r.name for r in granted]})
    return user


def login_or_bootstrap(db: Session, *, email: str, display_name: str | None) -> User:
    """Resolve a verified internal login to a user row, creating it on first login."""
    user = get_user_by_email(db, email)
    if user is None:
        user = create_internal_user(db, None, email=email, display_name=display_name)
    if user.status != "active" or user.user_type != "internal":
        raise ValidationError("This account is not active")
    return user
