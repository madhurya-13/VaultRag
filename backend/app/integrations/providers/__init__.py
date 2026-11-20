"""Built-in provider connectors.

Every module imported here registers itself with the registry at import time.
A deployment that does not configure a provider still sees it in
``GET /integrations/providers`` with ``available: false`` and a reason, so the
UI can explain what is missing instead of failing at connect time.
"""

from app.integrations.providers.google_drive import connector as google_drive
from app.integrations.providers.local_storage import connector as local_storage
from app.integrations.providers.sharepoint import connector as sharepoint

__all__ = ["google_drive", "local_storage", "sharepoint"]
