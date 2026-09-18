"""Typed failures raised by the StarDojo adapter."""


class StarDojoError(RuntimeError):
    """Base class for adapter failures."""


class ConfigurationError(StarDojoError):
    """The local adapter configuration is unsafe or invalid."""


class ConnectionFailed(StarDojoError):
    """A connection could not be established."""


class ResponseTimeout(StarDojoError):
    """The response deadline expired; a mutation may have an unknown outcome."""


class ProtocolError(StarDojoError):
    """The peer violated the StarDojo framing or encoding protocol."""


class ResponseTooLarge(ProtocolError):
    """The response exceeded the configured byte limit."""


class ObservationError(StarDojoError):
    """Structured observation validation failed."""


class EnvironmentNotReady(StarDojoError):
    """The expected farmer or game state did not appear before the deadline."""


class MovementBlocked(StarDojoError):
    """A movement destination failed the pre-move occupancy policy."""


class MovementInvariantError(StarDojoError):
    """Observed movement did not match StarDojo's reported outcome."""
