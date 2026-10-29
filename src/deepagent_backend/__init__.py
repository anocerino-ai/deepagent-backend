"""Remote storage backends for Deep Agents.

This umbrella package ships the shared helpers and lazily re-exports every
backend, so the import path stays the same whichever backend you installed::

    pip install "deepagent-backend[s3]"

    from deepagent_backend import S3Backend, S3Config

Each backend lives in its own distribution (``deepagent-backend-s3``,
``deepagent-backend-redis``, ...) and pulls in only the client libraries it needs.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

from deepagent_backend.common import run_async_safely

if TYPE_CHECKING:  # pragma: no cover
    from deepagent_backend_azure_blob import AzureBlobBackend, AzureBlobConfig
    from deepagent_backend_gcs import GCSBackend, GCSConfig
    from deepagent_backend_mongodb import MongoDBBackend, MongoDBConfig
    from deepagent_backend_postgres import PostgresBackend, PostgresConfig
    from deepagent_backend_redis import RedisBackend, RedisConfig
    from deepagent_backend_s3 import S3Backend, S3Config

__all__ = [
    "AzureBlobBackend",
    "AzureBlobConfig",
    "GCSBackend",
    "GCSConfig",
    "MongoDBBackend",
    "MongoDBConfig",
    "PostgresBackend",
    "PostgresConfig",
    "RedisBackend",
    "RedisConfig",
    "S3Backend",
    "S3Config",
    "run_async_safely",
]

# public name -> (backend module, pip extra that installs it)
_LAZY: dict[str, tuple[str, str]] = {
    "AzureBlobBackend": ("deepagent_backend_azure_blob", "azure-blob"),
    "AzureBlobConfig": ("deepagent_backend_azure_blob", "azure-blob"),
    "GCSBackend": ("deepagent_backend_gcs", "gcs"),
    "GCSConfig": ("deepagent_backend_gcs", "gcs"),
    "MongoDBBackend": ("deepagent_backend_mongodb", "mongodb"),
    "MongoDBConfig": ("deepagent_backend_mongodb", "mongodb"),
    "PostgresBackend": ("deepagent_backend_postgres", "postgres"),
    "PostgresConfig": ("deepagent_backend_postgres", "postgres"),
    "RedisBackend": ("deepagent_backend_redis", "redis"),
    "RedisConfig": ("deepagent_backend_redis", "redis"),
    "S3Backend": ("deepagent_backend_s3", "s3"),
    "S3Config": ("deepagent_backend_s3", "s3"),
}


def __getattr__(name: str) -> Any:
    try:
        module_name, extra = _LAZY[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None

    try:
        module = import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name != module_name:
            raise  # a real dependency of the backend is broken: surface it as-is
        raise ImportError(
            f"{name} requires the '{extra}' backend. "
            f'Install it with: pip install "deepagent-backend[{extra}]"'
        ) from exc

    value = getattr(module, name)
    globals()[name] = value  # cache for subsequent lookups
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
