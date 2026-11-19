"""Governance writes against PostgreSQL: split failure isolation.

``split_alias`` creates a node, which can violate the unique node-identity
constraint. That failure must roll back only its own savepoint: an operator's
transaction may already contain other pending work, and aborting the whole
session would silently discard it.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.core.exceptions import BadRequestError
from app.models import EntityAlias, KnowledgeNode
from app.services.graph.governance import split_alias
from app.services.graph.normalization import identity_key, normalize_name
from app.tests.helpers import provision_admin, register_organization


def _node(tenant_id: UUID, name: str) -> KnowledgeNode:
    return KnowledgeNode(
        tenant_id=tenant_id,
        node_type="ORGANIZATION",
        canonical_name=name,
        display_name=name,
        normalized_name=normalize_name(name)[:255],
        identity_key=identity_key("ORGANIZATION", name),
        extraction_confidence=0.9,
        mention_count=1,
    )


def test_split_alias_failure_keeps_the_callers_transaction(client, db, tenant_context):
    organization = register_organization(client, f"Governance {uuid4()}")
    tenant_id = UUID(organization["tenant_id"])
    tenant_context(db, tenant_id)

    # The alias already belongs to a different node, so splitting it out would
    # collide with that node's identity key.
    existing = _node(tenant_id, "Acme Inc")
    source = _node(tenant_id, "Acme Holdings")
    later_work = _node(tenant_id, "Marker Holdings")
    db.add_all([existing, source, later_work])
    db.flush()
    db.add(
        EntityAlias(
            tenant_id=tenant_id,
            node_id=source.node_id,
            alias="Acme Inc",
            normalized_alias=normalize_name("Acme Inc"),
            source="MANUAL",
            extraction_confidence=1.0,
        )
    )
    db.flush()

    with pytest.raises(BadRequestError):
        split_alias(
            db,
            tenant_id=tenant_id,
            actor_user_id=uuid4(),
            node_id=source.node_id,
            alias="Acme Inc",
            reason="duplicate identity regression test",
        )

    # The session is still usable and the earlier unit of work is intact.
    db.commit()
    tenant_context(db, tenant_id)
    assert (
        db.scalar(
            select(KnowledgeNode.node_id).where(
                KnowledgeNode.tenant_id == tenant_id,
                KnowledgeNode.node_id == later_work.node_id,
            )
        )
        == later_work.node_id
    )


def test_split_alias_moves_only_supported_evidence(client, db, tenant_context):
    organization = register_organization(client, f"Governance {uuid4()}")
    tenant_id = UUID(organization["tenant_id"])
    tenant_context(db, tenant_id)

    # Governance rows carry an actor foreign key to a tenant user.
    admin = provision_admin(
        db, tenant_id, email=f"split-{uuid4()}@example.com", username="split_admin"
    )
    # The provisioning helper commits, which clears the transaction-local tenant.
    tenant_context(db, tenant_id)
    original = _node(tenant_id, "Quill Holdings")
    db.add(original)
    db.flush()
    db.add(
        EntityAlias(
            tenant_id=tenant_id,
            node_id=original.node_id,
            alias="Quill Inc",
            normalized_alias=normalize_name("Quill Inc"),
            source="MANUAL",
            extraction_confidence=1.0,
        )
    )
    db.flush()

    result = split_alias(
        db,
        tenant_id=tenant_id,
        actor_user_id=admin.user_id,
        node_id=original.node_id,
        alias="Quill Inc",
        reason="manual split",
    )
    db.commit()

    assert result["action"] == "SPLIT"
    tenant_context(db, tenant_id)
    new_node = db.scalar(
        select(KnowledgeNode).where(
            KnowledgeNode.tenant_id == tenant_id,
            KnowledgeNode.node_id == result["target_node_id"],
        )
    )
    assert new_node is not None
    assert new_node.display_name == "Quill Inc"
