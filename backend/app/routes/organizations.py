"""Organization registration and current-tenant endpoints."""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.dependencies.authentication import AuthenticatedContext, get_current_auth
from app.schemas.organization import (
    OrganizationRegisterRequest,
    OrganizationRegisterResponse,
    OrganizationResponse,
)
from app.services.organizations import get_role_names, register_organization

router = APIRouter(prefix="/organizations", tags=["Organizations"])


@router.post(
    "/register",
    response_model=OrganizationRegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new tenant and create its default roles",
)
def register(
    payload: OrganizationRegisterRequest,
    db: Session = Depends(get_db),
) -> OrganizationRegisterResponse:
    """Create a tenant.

    This endpoint intentionally does NOT create a user or an administrator. The
    caller receives only the new tenant ID and role catalogue. Initial admin
    provisioning happens out-of-band through the CLI (see README), which prevents
    privilege assignment through a public HTTP request.
    """
    organization = register_organization(db, payload.organization_name)
    return OrganizationRegisterResponse(
        tenant_id=organization.tenant_id,
        organization_name=organization.organization_name,
        is_active=organization.is_active,
        created_at=organization.created_at,
        roles=get_role_names(db, organization.tenant_id),
    )


@router.get(
    "/me",
    response_model=OrganizationResponse,
    summary="Return the authenticated user's organization",
)
def my_organization(
    context: AuthenticatedContext = Depends(get_current_auth),
) -> OrganizationResponse:
    """Tenant information is read from the authenticated database record."""
    organization = context.organization
    return OrganizationResponse(
        tenant_id=organization.tenant_id,
        organization_name=organization.organization_name,
        is_active=organization.is_active,
        created_at=organization.created_at,
    )
