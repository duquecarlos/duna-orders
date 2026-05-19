class StorageError(Exception):
    """Base class for storage-layer errors."""


class StorageConfigError(StorageError):
    """Raised when storage configuration is missing or invalid."""


class StorageAuthError(StorageError):
    """Raised when storage authentication fails."""


class StorageBackendError(StorageError):
    """Raised when the storage backend fails unexpectedly."""
