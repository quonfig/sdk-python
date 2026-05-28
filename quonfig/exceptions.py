class QuonfigError(Exception):
    """Base class for all Quonfig SDK errors."""


class QuonfigKeyNotFoundError(QuonfigError):
    """Key missing, on_no_default="error", no default provided."""


class QuonfigInitTimeoutError(QuonfigError):
    """init() did not complete within init_timeout_ms milliseconds."""


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
