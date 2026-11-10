"""Compatibility entry point; prefer app.ingestion.cli.upgrade_manifests."""

from app.ingestion.cli.upgrade_manifests import ManifestMigrator

if __name__ == "__main__":
    raise SystemExit(ManifestMigrator.main())
