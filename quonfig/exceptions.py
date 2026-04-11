class QuonfigError(Exception):
    """Base class for all Quonfig SDK errors."""


class QuonfigKeyNotFoundError(QuonfigError):
    """Key missing, on_no_default="error", no default provided."""


class QuonfigInitTimeoutError(QuonfigError):
    """init() did not complete within init_timeout seconds."""


class QuonfigNotInitializedError(QuonfigError):
    """A getter was called before init() completed."""


class QuonfigEnvVarNotSetError(QuonfigError):
    """A provided (ENV_VAR) config references an unset variable."""


class QuonfigDecryptionError(QuonfigError):
    """AES-256-GCM decryption of a confidential value failed."""
