class DomainError(Exception):
    """Base class for expected domain failures."""


class InvalidTokenError(DomainError):
    """Raised when a login token does not match the configured token."""
