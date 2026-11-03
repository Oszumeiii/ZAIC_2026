"""Audit saved manifests against current files without an LLM or ingestion."""

from pathlib import Path
from typing import Sequence
import argparse
import json

from jsonschema import Draft202012Validator

from app.llm.gemini import GeminiLLM
from app.manifests.service import BusinessManifestService, SCHEMA_FILE
from app.manifests.validation import ManifestValidator
from app.storage.json_store import JsonStore

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GEMINI_REQUEST_INTERVAL_SECONDS = 4.25
DEFAULT_DATASET_DIR = PROJECT_ROOT / "dataset" / "train" / "data"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "dataset" / "train" / "manifests"


class BuildManifestsCommand:
    """Generate and persist manifests, evidence bundles and reports."""

    @staticmethod
    def main(argv: Sequence[str] | None = None) -> int:
        parser = argparse.ArgumentParser(
            description="Generate one schema-validated BusinessManifest per train business."
        )
        parser.add_argument("--dataset-dir", type=Path, default=DEFAULT_DATASET_DIR)
        parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
        parser.add_argument("--schema", type=Path, default=SCHEMA_FILE.with_name("business_manifest.v2.schema.json"))
        parser.add_argument("--business-id")
        parser.add_argument("--overwrite", action="store_true")
        args = parser.parse_args(argv)

        schema = json.loads(args.schema.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        rulebook_text = (args.dataset_dir.parent / "rulebook.md").read_text(encoding="utf-8")
        business_dirs = sorted(path for path in args.dataset_dir.iterdir() if path.is_dir())
        if args.business_id:
            business_dirs = [path for path in business_dirs if path.name == args.business_id]
            if not business_dirs:
                raise ValueError(f"Business not found: {args.business_id}")

        args.output_dir.mkdir(parents=True, exist_ok=True)
        evidence_dir = args.output_dir / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        llm = None
        for business_dir in business_dirs:
            output_path = args.output_dir / f"{business_dir.name}.json"
            if output_path.exists() and not args.overwrite:
                print(f"Skipping existing manifest: {output_path}")
                continue
            business_input = BusinessManifestService.build_input(business_dir, rulebook_text, schema)
            if llm is None:
                llm = GeminiLLM.from_env(
                    min_request_interval_seconds=GEMINI_REQUEST_INTERVAL_SECONDS
                )
            manifest = BusinessManifestService.generate(llm, business_input, schema)
            JsonStore.write(BusinessManifestService.build_evidence(business_input), evidence_dir / f"{business_dir.name}.json")
            report = ManifestValidator.validate(manifest, business_input["source_inventory"], business_input["_evidence_ids"])
            JsonStore.write(report, args.output_dir / "reports" / f"{business_dir.name}.json")
            JsonStore.write(manifest, output_path)
            print(f"Created {output_path}")
        return 0


class AuditManifestsCommand:
    """Audit manifests against current source files without calling an LLM."""

    @staticmethod
    def main():
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument('--dataset-dir', type=Path, default=Path('dataset/train/data'))
        parser.add_argument('--manifest-dir', type=Path, default=Path('dataset/train/manifests'))
        parser.add_argument('--output-dir', type=Path, required=True)
        parser.add_argument('--business-id')
        args = parser.parse_args()
        schema = json.loads(SCHEMA_FILE.read_text())
        rulebook = (args.dataset_dir.parent / 'rulebook.md').read_text()
        failed = False
        for root in sorted(args.dataset_dir.iterdir()):
            if not root.is_dir() or (args.business_id and root.name != args.business_id):
                continue
            manifest = json.loads((args.manifest_dir / f'{root.name}.json').read_text())
            Draft202012Validator(schema).validate(manifest)
            data = BusinessManifestService.build_input(root, rulebook, schema)
            report = ManifestValidator.validate(manifest, data['source_inventory'], data['_evidence_ids'])
            JsonStore.write(report, args.output_dir / f'{root.name}.json')
            failed |= bool(report['errors'])
            print(root.name, report['coverage'], report['errors'], flush=True)
        return int(failed)
