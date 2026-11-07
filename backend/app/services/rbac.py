"""Central RBAC capability checks.

Routes should not contain scattered ``if "ADMIN" in roles`` branches. They use
these helpers or the FastAPI dependencies in ``app.dependencies.authentication``.
Document access is intentionally not decided here: it depends on per-document
grants and is evaluated by the authorization engine.
"""

from app.core.security_constants import ROLE_CAPABILITIES


def has_capability(roles: tuple[str, ...] | list[str], capability: str) -> bool:
    return any(capability in ROLE_CAPABILITIES.get(role, frozenset()) for role in roles)


def has_any_capability(
    roles: tuple[str, ...] | list[str], capabilities: set[str] | frozenset[str]
) -> bool:
    return any(has_capability(roles, capability) for capability in capabilities)
