"""Compatibility entry point; prefer app.ingestion.cli.audit_manifests."""

from app.ingestion.cli.audit_manifests import AuditManifestsCommand

if __name__ == "__main__":
    raise SystemExit(AuditManifestsCommand.main())
