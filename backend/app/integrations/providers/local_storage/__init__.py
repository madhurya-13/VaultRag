"""Local-filesystem reference connector.

It exists for three reasons:

1. It is a complete, dependency-free implementation of the connector contract,
   so the framework and the sync engine can be tested without Google or
   Microsoft credentials or network access.
2. It documents the expected behaviour of a real connector: capability
   declaration, cursor-based incremental discovery, size-capped download with a
   server-computed digest, and explicit refusal of unsupported operations.
3. It is useful in air-gapped deployments that mount an existing document share
   read-only.

It deliberately does **not** manage permissions: a local directory has no ACL
model, so ``GET_PERMISSIONS`` is not declared and asking for it fails loudly.
Ingested documents therefore get no external grants.
"""

from app.integrations.providers.local_storage.connector import (
    LocalStorageConnector,
)

__all__ = ["LocalStorageConnector"]
