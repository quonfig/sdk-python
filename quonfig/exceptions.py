class QuonfigError(Exception):
    """Base class for all Quonfig SDK errors."""


class QuonfigKeyNotFoundError(QuonfigError):
    """Key missing, on_no_default="error", no default provided."""


class QuonfigInitError(QuonfigError):
    """``init()`` could not load any config (``on_init_failure="fail"``, the
    default).

    ``reason`` says why:

    * ``"unavailable"``: every delivery leg failed (refused, DNS, 5xx, ...)
      through the whole ``init_timeout_ms`` window.
    * ``"unauthorized"``: every leg rejected the SDK key (401/403); raised at
      once, without waiting out the timeout.
    * ``"timeout"``: no leg answered at all before ``init_timeout_ms``
      (raised as the ``QuonfigInitTimeoutError`` subclass).

    ``leg_errors`` is the ``primary=<host>: <error>; secondary=...`` summary
    (hosts only; the SDK key is never included). A client object that the
    caller keeps after catching this keeps retrying in the background and
    heals once delivery answers.
    """

    def __init__(self, message: str, *, reason: str = "unavailable", leg_errors: str = "") -> None:
        super().__init__(message)
        self.reason = reason
        self.leg_errors = leg_errors


class QuonfigInitTimeoutError(QuonfigInitError):
    """``init()`` got no answer from any delivery leg within ``init_timeout_ms``."""

    def __init__(self, message: str, *, reason: str = "timeout", leg_errors: str = "") -> None:
        super().__init__(message, reason=reason, leg_errors=leg_errors)


class QuonfigNotInitializedError(QuonfigError):
    """A getter was called before init() completed."""


class QuonfigEnvVarNotSetError(QuonfigError):
    """A provided (ENV_VAR) config references an unset variable."""


class QuonfigDecryptionError(QuonfigError):
    """AES-256-GCM decryption of a confidential value failed."""


class QuonfigValueTypeError(QuonfigError):
    """A value does not conform to the expected wire representation for its type.

    Example: a ``json``-typed value arriving as a stringified JSON payload instead
    of a native dict/list/number/bool/None.
    """


class QuonfigEnvVarCoerceError(QuonfigKeyNotFoundError):
    """An ENV_VAR-provided value cannot be coerced to the config's valueType.

    Raised only when no default was given and ``on_no_default="error"``
    (the ``unable_to_coerce_env_var`` contract). Subclasses
    ``QuonfigKeyNotFoundError`` so existing handlers keep catching it.
    """
