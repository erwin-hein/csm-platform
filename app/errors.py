class ServiceError(Exception):
    """Base for errors a service function raises to its caller."""

    status_code = 400


class ValidationError(ServiceError):
    status_code = 400


class NotFound(ServiceError):
    # Also used for "exists but you can't see it" — never leak existence across scope.
    status_code = 404


class Forbidden(ServiceError):
    # Visible to the actor, but they can't act on it (e.g. a 'viewer' membership).
    status_code = 403
