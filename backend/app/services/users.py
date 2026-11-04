"""User provisioning, authentication and tenant-scoped user administration.

Every function accepts ``tenant_id`` as an explicit argument. The route layer
always supplies ``AuthenticatedContext.user.tenant_id`` (from PostgreSQL), never
a value taken from the request. This makes it difficult to accidentally write an
unscoped query.
"""

import logging
from collections import defaultdict
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    UnauthorizedError,
)
from app.core.passwords import validate_password_strength
from app.core.security import (
    hash_password,
    password_needs_rehash,
    perform_dummy_verify,
    verify_password,
)
from app.models import Organization, Role, User, UserRole
from app.services.organizations import get_organization_or_raise

logger = logging.getLogger(__name__)


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _resolve_roles(db: Session, tenant_id: UUID, role_names: list[str]) -> list[Role]:
    """Resolve role *names* inside one tenant, rejecting anything unknown.

    Names are resolved to IDs server-side. A caller can never supply a role ID
    from another tenant, because IDs are never accepted and names are filtered
    by ``tenant_id``.
    """
    if not role_names:
        return []

    unique_names = list(dict.fromkeys(role_names))
    roles = list(
        db.scalars(
            select(Role).where(
                Role.tenant_id == tenant_id,
                Role.role_name.in_(unique_names),
            )
        ).all()
    )
    found = {role.role_name for role in roles}
    missing = sorted(set(unique_names) - found)
    if missing:
        # 400 (not 404) avoids confirming whether a same-named role exists in
        # another tenant.
        raise BadRequestError(
            "Unknown role(s) for this organization: " + ", ".join(missing)
        )
    return roles


def _ensure_identity_available(
    db: Session, tenant_id: UUID, email: str, username: str
) -> None:
    """Friendly pre-check; the unique constraints remain the real guarantee."""
    existing = db.scalar(
        select(User.user_id).where(
            User.tenant_id == tenant_id,
            or_(User.email == email, User.username == username),
        )
    )
    if existing is not None:
        raise ConflictError(
            "A user with this email or username already exists in this organization"
        )


def create_user(
    db: Session,
    *,
    tenant_id: UUID,
    username: str,
    email: str,
    password: str,
    role_names: list[str] | None = None,
    is_active: bool = True,
) -> User:
    """Create a user inside ``tenant_id``.

    Used by the admin API and the CLI bootstrap script. It is intentionally not
    reachable from a public request without an authenticated ADMIN context.
    """
    organization = get_organization_or_raise(db, tenant_id, require_active=True)

    # Enforce the policy here as well as in Pydantic so the CLI cannot create a
    # weak-password account by bypassing the HTTP layer.
    try:
        validate_password_strength(password)
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc

    normalized_email = normalize_email(email)
    roles = _resolve_roles(db, organization.tenant_id, role_names or [])
    _ensure_identity_available(
        db, organization.tenant_id, normalized_email, username.strip()
    )

    user = User(
        tenant_id=organization.tenant_id,
        username=username.strip(),
        email=normalized_email,
        password_hash=hash_password(password),
        is_active=is_active,
    )
    db.add(user)

    try:
        db.flush()  # assign user_id
        db.add_all(
            UserRole(
                user_id=user.user_id,
                role_id=role.role_id,
                tenant_id=organization.tenant_id,  # single tenant value for both FKs
            )
            for role in roles
        )
        db.commit()
    except IntegrityError as exc:
        # Covers races that slip past the pre-check, plus the composite
        # tenant-consistency constraints (defence in depth).
        db.rollback()
        logger.warning("User creation failed integrity check for tenant %s", tenant_id)
        raise ConflictError(
            "A user with this email or username already exists in this organization"
        ) from exc

    db.refresh(user)
    logger.info("Created user user_id=%s tenant_id=%s", user.user_id, user.tenant_id)
    return user


def register_public_user(
    db: Session,
    *,
    tenant_id: UUID,
    username: str,
    email: str,
    password: str,
) -> User:
    """Self-service signup: inactive, role-less, tenant-scoped.

    This is the critical difference between a demo and a secure product. A public
    caller can request membership of an active tenant, but cannot activate the
    account or choose a role. An administrator must approve it.
    """
    return create_user(
        db,
        tenant_id=tenant_id,
        username=username,
        email=email,
        password=password,
        role_names=[],
        is_active=False,
    )


def authenticate_user(
    db: Session,
    *,
    tenant_id: UUID,
    email: str,
    password: str,
) -> User:
    """Verify credentials and account/tenant state.

    All failures return the same generic error so an attacker cannot enumerate
    accounts, roles or tenant state.
    """
    row = db.execute(
        select(User, Organization)
        .join(Organization, Organization.tenant_id == User.tenant_id)
        .where(
            User.tenant_id == tenant_id,
            User.email == normalize_email(email),
        )
    ).first()

    if row is None:
        # Equalise response time with the "wrong password" path.
        perform_dummy_verify(password)
        raise UnauthorizedError("Invalid credentials")

    user, organization = row

    if not verify_password(password, user.password_hash):
        raise UnauthorizedError("Invalid credentials")

    # Account state is checked AFTER the password so an attacker cannot use the
    # response time/behaviour to discover whether an address is registered.
    if not user.is_active or not organization.is_active:
        raise UnauthorizedError("Invalid credentials")

    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
        db.commit()

    return user


def get_tenant_user(db: Session, tenant_id: UUID, user_id: UUID) -> User:
    """Fetch a user only if it belongs to the supplied tenant.

    A user in another tenant produces the same 404 as a non-existent user, so
    the endpoint cannot be used to enumerate cross-tenant IDs.
    """
    user = db.scalar(
        select(User).where(
            User.tenant_id == tenant_id,
            User.user_id == user_id,
        )
    )
    if user is None:
        raise NotFoundError("User not found")
    return user


def list_tenant_users(
    db: Session, tenant_id: UUID, *, limit: int = 50, offset: int = 0
) -> list[User]:
    return list(
        db.scalars(
            select(User)
            .where(User.tenant_id == tenant_id)  # mandatory tenant scope
            .order_by(User.created_at, User.user_id)
            .limit(limit)
            .offset(offset)
        ).all()
    )


def role_names_for_user(db: Session, tenant_id: UUID, user_id: UUID) -> list[str]:
    return list(
        db.scalars(
            select(Role.role_name)
            .join(
                UserRole,
                and_(
                    UserRole.role_id == Role.role_id,
                    UserRole.tenant_id == Role.tenant_id,
                ),
            )
            .where(
                UserRole.tenant_id == tenant_id,
                UserRole.user_id == user_id,
            )
            .order_by(Role.role_name)
        ).all()
    )


def role_names_by_user_ids(
    db: Session, tenant_id: UUID, user_ids: list[UUID]
) -> dict[UUID, list[str]]:
    """Batch role lookup to avoid an N+1 query when listing users."""
    result: dict[UUID, list[str]] = defaultdict(list)
    if not user_ids:
        return result

    rows = db.execute(
        select(UserRole.user_id, Role.role_name)
        .join(
            Role,
            and_(
                UserRole.role_id == Role.role_id,
                UserRole.tenant_id == Role.tenant_id,
            ),
        )
        .where(
            UserRole.tenant_id == tenant_id,
            UserRole.user_id.in_(user_ids),
        )
        .order_by(Role.role_name)
    ).all()

    for user_id, role_name in rows:
        result[user_id].append(role_name)
    return result


def _get_role_in_tenant(db: Session, tenant_id: UUID, role_name: str) -> Role:
    role = db.scalar(
        select(Role).where(
            Role.tenant_id == tenant_id,
            Role.role_name == role_name,
        )
    )
    if role is None:
        raise NotFoundError("Role not found in this organization")
    return role


def assign_role(
    db: Session, *, tenant_id: UUID, user_id: UUID, role_name: str
) -> None:
    """Grant a role to a user in the same tenant."""
    get_tenant_user(db, tenant_id, user_id)  # tenant-scoped existence check
    role = _get_role_in_tenant(db, tenant_id, role_name)

    existing = db.get(UserRole, {"user_id": user_id, "role_id": role.role_id})
    if existing is not None:
        raise ConflictError("User already has this role")

    db.add(
        UserRole(
            user_id=user_id,
            role_id=role.role_id,
            tenant_id=tenant_id,
        )
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        # If the composite FK fired, the request was cross-tenant despite the
        # application checks; treat it as a bad request, never grant anything.
        logger.error(
            "Rejected role assignment user=%s role=%s tenant=%s",
            user_id,
            role.role_id,
            tenant_id,
        )
        raise BadRequestError("Role assignment violates tenant boundaries") from exc


def revoke_role(
    db: Session,
    *,
    tenant_id: UUID,
    user_id: UUID,
    role_name: str,
    actor_user_id: UUID,
) -> None:
    """Remove a role. Prevents an admin from removing their own last ADMIN role."""
    get_tenant_user(db, tenant_id, user_id)
    role = _get_role_in_tenant(db, tenant_id, role_name)

    link = db.get(UserRole, {"user_id": user_id, "role_id": role.role_id})
    if link is None:
        raise NotFoundError("User does not have this role")

    if role.role_name == "ADMIN" and user_id == actor_user_id:
        admin_count = db.scalar(
            select(func.count())
            .select_from(UserRole)
            .join(
                Role,
                and_(
                    UserRole.role_id == Role.role_id,
                    UserRole.tenant_id == Role.tenant_id,
                ),
            )
            .where(
                UserRole.tenant_id == tenant_id,
                Role.role_name == "ADMIN",
            )
        )
        if (admin_count or 0) <= 1:
            raise BadRequestError("Cannot remove your own last ADMIN role")

    db.delete(link)
    db.commit()
    logger.info("Revoked role=%s from user=%s", role.role_name, user_id)


def set_user_active(
    db: Session,
    *,
    tenant_id: UUID,
    user_id: UUID,
    is_active: bool,
    actor_user_id: UUID,
) -> User:
    """Activate/deactivate a user inside the tenant."""
    user = get_tenant_user(db, tenant_id, user_id)

    if user.user_id == actor_user_id and not is_active:
        raise BadRequestError("You cannot deactivate your own account")

    user.is_active = is_active
    db.commit()
    db.refresh(user)
    logger.info("Set user=%s is_active=%s", user.user_id, is_active)
    return user
