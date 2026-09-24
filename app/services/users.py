from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.errors import ValidationError
from app.events import emit, mutation
from app.models import User


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == email.strip().lower()))


def list_internal_users(db: Session) -> list[User]:
    return list(
        db.scalars(
            select(User).where(User.user_type == "internal", User.status == "active").order_by(User.display_name)
        )
    )


def is_allowed_internal_email(email: str) -> bool:
    return email.strip().lower().endswith("@" + settings.allowed_domain)


@mutation("user_created")
def create_internal_user(db: Session, actor: User | None, *, email: str, display_name: str | None,
                         role: str | None = None) -> User:
    """Create an internal user. Without an explicit role, ADMIN_EMAILS decides
    admin vs. the default 'analyst' (CLAUDE.md §2 row 16 bootstrap)."""
    email = email.strip().lower()
    if not is_allowed_internal_email(email):
        raise ValidationError(f"Only @{settings.allowed_domain} accounts can be internal users")
    if role is None:
        role = "admin" if email in settings.admin_emails else "analyst"
    user = User(email=email, display_name=display_name or email.split("@")[0], user_type="internal", role=role)
    db.add(user)
    db.flush()
    emit(db, entity_type="user", entity_id=user.id, event_type="user_created",
         actor=actor or user, payload={"email": email, "role": role})
    return user


def login_or_bootstrap(db: Session, *, email: str, display_name: str | None) -> User:
    """Resolve a verified internal login to a user row, creating it on first login."""
    user = get_user_by_email(db, email)
    if user is None:
        user = create_internal_user(db, None, email=email, display_name=display_name)
    if user.status != "active" or user.user_type != "internal":
        raise ValidationError("This account is not active")
    return user
