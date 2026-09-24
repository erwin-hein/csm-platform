"""The event bus write path — CLAUDE.md §2 row 5 / §3 "Events — the spine".

Every mutating service function appends its event through `emit()`, into the same
session (and therefore the same transaction) as the mutation itself. `@mutation`
turns that convention into a runtime guard: a decorated function that returns
without having emitted its declared event raises instead of silently committing an
un-evented write.

No handlers subscribe yet (digests, rules, notifications are out of PoC scope); the
spine is demonstrated by every write landing here.
"""

import functools
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.models import Event, User

# service function qualname -> declared event_type; read by the drift-guard test.
MUTATIONS: dict[str, str] = {}

_PENDING_KEY = "pending_event_types"


def emit(
    db: Session,
    *,
    entity_type: str,
    entity_id: uuid.UUID | None,
    event_type: str,
    actor: User | None,
    payload: dict[str, Any] | None = None,
) -> Event:
    event = Event(
        entity_type=entity_type,
        entity_id=entity_id,
        event_type=event_type,
        actor_user_id=actor.id if actor else None,
        payload=_jsonable(payload or {}),
    )
    db.add(event)
    db.info.setdefault(_PENDING_KEY, []).append(event_type)
    return event


def mutation(event_type: str) -> Callable:
    """Declare that a service function mutates state and must emit `event_type`."""

    def decorator(fn: Callable) -> Callable:
        MUTATIONS[f"{fn.__module__}.{fn.__qualname__}"] = event_type

        @functools.wraps(fn)
        def wrapper(db: Session, *args, **kwargs):
            before = len(db.info.get(_PENDING_KEY, []))
            result = fn(db, *args, **kwargs)
            emitted = db.info.get(_PENDING_KEY, [])[before:]
            if event_type not in emitted:
                raise RuntimeError(f"{fn.__qualname__} mutated state without emitting '{event_type}'")
            db.flush()
            return result

        wrapper.__mutation_event__ = event_type
        return wrapper

    return decorator


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)  # UUIDs, dates, Decimals
