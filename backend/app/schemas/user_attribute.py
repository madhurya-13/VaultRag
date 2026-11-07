"""User ABAC attribute schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import Field, field_validator

from app.core.security_constants import ATTRIBUTE_NAMES
from app.schemas.common import StrictRequestModel


class UserAttributeUpsertRequest(StrictRequestModel):
    attributes: dict[str, str] = Field(min_length=1, max_length=len(ATTRIBUTE_NAMES))

    @field_validator("attributes")
    @classmethod
    def _validate_names_and_values(cls, value: dict[str, str]) -> dict[str, str]:
        unknown = sorted(set(value) - ATTRIBUTE_NAMES)
        if unknown:
            raise ValueError("Unknown attribute(s): " + ", ".join(unknown))
        for name, attribute_value in value.items():
            if not isinstance(attribute_value, str) or not attribute_value.strip():
                raise ValueError(f"Attribute {name} must be a non-blank string")
            if len(attribute_value.strip()) > 255:
                raise ValueError(f"Attribute {name} is too long")
        return value


class UserAttributeResponse(StrictRequestModel):
    attribute_name: str
    attribute_value: str
    updated_at: datetime


class UserAttributesResponse(StrictRequestModel):
    tenant_id: UUID
    user_id: UUID
    attributes: list[UserAttributeResponse]
