"""Build-time only: fetch a frozen public model revision and inventory all weights."""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def main():
    from huggingface_hub import HfApi, snapshot_download
    from docker.verify_model import inventory
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True, help='Full 40-character public commit SHA before cutoff')
    parser.add_argument('--output', default='models/Qwen3.5-9B')
    args = parser.parse_args()
    import re
    if not re.fullmatch('[0-9a-f]{40}', args.revision):
        raise ValueError('Provide an immutable commit SHA')
    repo = 'Qwen/Qwen3.5-9B'
    info = HfApi().model_info(repo, revision=args.revision)
    history = HfApi().list_repo_commits(repo, revision=args.revision)
    commit = history[0]
    if commit.created_at >= datetime(2026, 10, 26, tzinfo=timezone.utc):
        raise ValueError('Revision is not before the competition cutoff')
    if info.card_data.license != 'apache-2.0':
        raise ValueError('Review the actual model licence before packaging')
    first_weights = next(c for c in reversed(history) if any(
        name.endswith('.safetensors') for name in HfApi().list_repo_files(repo, revision=c.commit_id)))
    snapshot_download(repo, revision=args.revision, local_dir=args.output,
                      allow_patterns=['*.json', '*.safetensors', '*.jinja', '*.txt', '*.model', 'LICENSE*', 'README.md'])
    root = Path(args.output)
    report = inventory(root)
    report.update(repo=repo, revision=args.revision, revision_date=commit.created_at.isoformat(),
                  licence='apache-2.0', fine_tuned=False,
                  original_release_date=first_weights.created_at.isoformat(),
                  release_date_evidence=f'https://huggingface.co/{repo}/tree/{first_weights.commit_id}')
    (root / 'MODEL_MANIFEST.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
