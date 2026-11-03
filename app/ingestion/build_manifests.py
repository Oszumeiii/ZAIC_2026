import argparse
import json
from pathlib import Path
from typing import Sequence

from jsonschema import Draft202012Validator

from app.ingestion.business_manifest import (
    SCHEMA_FILE,
    build_business_input,
    build_evidence_bundle,
    generate_business_manifest,
)
from app.llm.gemini import create_gemini_llm

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GEMINI_REQUEST_INTERVAL_SECONDS = 4.25
DEFAULT_DATASET_DIR = PROJECT_ROOT / "dataset" / "train" / "data"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "dataset" / "train" / "manifests"
RULEBOOK_FILE = PROJECT_ROOT / "dataset" / "train" / "rulebook.md"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate one schema-validated BusinessManifest per train business."
    )
    parser.add_argument("--dataset-dir", type=Path, default=DEFAULT_DATASET_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--schema", type=Path, default=SCHEMA_FILE)
    parser.add_argument("--business-id")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)

    schema = json.loads(args.schema.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    rulebook_text = RULEBOOK_FILE.read_text(encoding="utf-8")
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
        business_input = build_business_input(business_dir, rulebook_text, schema)
        evidence_path = evidence_dir / f"{business_dir.name}.json"
        temporary_evidence_path = evidence_path.with_suffix(".json.tmp")
        temporary_evidence_path.write_text(
            json.dumps(
                build_evidence_bundle(business_input),
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary_evidence_path.replace(evidence_path)

        output_path = args.output_dir / f"{business_dir.name}.json"
        if output_path.exists() and not args.overwrite:
            print(f"Skipping existing manifest: {output_path}")
            continue
        if llm is None:
            llm = create_gemini_llm(
                min_request_interval_seconds=GEMINI_REQUEST_INTERVAL_SECONDS
            )
        manifest = generate_business_manifest(llm, business_input, schema)
        temporary_path = output_path.with_suffix(".json.tmp")
        temporary_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(output_path)
        print(f"Created {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
