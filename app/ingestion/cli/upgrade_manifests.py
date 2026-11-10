"""CLI entry point; implementation lives in app.manifests.migration."""

from app.manifests.migration import ManifestMigrator

if __name__ == "__main__":
    raise SystemExit(ManifestMigrator.main())
