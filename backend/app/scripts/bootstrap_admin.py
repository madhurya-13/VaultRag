"""Create the first ADMIN for an existing organization.

This is an out-of-band operator action. It is deliberately NOT an HTTP endpoint:
whoever can run this script already controls the server or database.

Usage (from ``backend/`` with the virtualenv active):

    python -m app.scripts.bootstrap_admin `
        --tenant-id <TENANT_UUID> `
        --username admin `
        --email admin@technova.com

You will be prompted for the password without echoing it. Passing
``--password`` is supported for automation but leaves the secret in shell
history / process listings, so avoid it on shared machines.
"""

import argparse
import getpass
import sys
from uuid import UUID

from sqlalchemy import select

from app.core.database import SessionLocal, set_tenant_context
from app.core.exceptions import AppError
from app.models import Organization
from app.services.bootstrap import provision_initial_admin


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Provision the initial ADMIN user for an existing VaultRAG tenant."
    )
    parser.add_argument("--tenant-id", required=True, type=UUID, help="Organization tenant_id")
    parser.add_argument("--username", required=True, help="Admin username")
    parser.add_argument("--email", required=True, help="Admin email (unique within the tenant)")
    parser.add_argument(
        "--password",
        required=False,
        help="Optional. Omit to be prompted securely (recommended).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    password = args.password
    if not password:
        password = getpass.getpass("Password for the initial admin: ")
        confirmation = getpass.getpass("Confirm password: ")
        if password != confirmation:
            print("Error: passwords do not match", file=sys.stderr)
            return 2

    with SessionLocal() as db:
        # RLS requires a tenant context even for the operator CLI. The tenant ID
        # is an explicit operator argument, never request input.
        set_tenant_context(db, args.tenant_id)
        organization = db.scalar(
            select(Organization).where(Organization.tenant_id == args.tenant_id)
        )
        if organization is None:
            print(f"Error: organization {args.tenant_id} does not exist", file=sys.stderr)
            return 1

        try:
            admin = provision_initial_admin(
                db,
                tenant_id=args.tenant_id,
                username=args.username,
                email=args.email,
                password=password,
            )
            db.commit()
        except AppError as exc:
            db.rollback()
            print(f"Error: {exc.detail}", file=sys.stderr)
            return 1
        except Exception:
            db.rollback()
            raise

    print("Initial admin provisioned successfully.")
    print(f"  user_id   : {admin.user_id}")
    print(f"  tenant_id : {admin.tenant_id}")
    print(f"  email     : {admin.email}")
    print("  role      : ADMIN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
