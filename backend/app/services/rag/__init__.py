"""Advanced retrieval and reasoning services.

Modules in this package build on the Milestone 4 pipeline. They never create a
new authorization path: every candidate still passes
``app.services.retrieval_authorization.filter_authorized_chunks`` before any
chunk text is loaded.
"""

from __future__ import annotations

__all__ = []
