"""Build the required local artifact manifest and explicit no-training declaration."""
import hashlib
import json
from pathlib import Path


def build(root):
    root = Path(root)
    training = root / 'training_code'
    data = training / 'data/train'
    questions = [json.loads(line) for line in (data/'questions.jsonl').read_text().splitlines() if line.strip()]
    config = {'checkpoint_training': 'none', 'synthetic_data_generation': 'none',
              'training_samples_used': 0, 'validation_samples_used_to_fit_policy': 0,
              'verification_questions': len(questions), 'verification_businesses': len({q['business_id'] for q in questions}),
              'seed':2026, 'temperature':0.7, 'n_samples':3, 'agree_min':2, 'max_retry':5,
              'max_model_len':16384, 'sql_tokens':2048, 'manifest_tokens':6144,
              'engine':'vllm', 'tensor_parallel':2, 'dtype':'bfloat16', 'thinking_enabled':False}
    (training/'configs').mkdir(parents=True, exist_ok=True)
    (training/'configs/frozen.json').write_text(json.dumps(config, indent=2))
    files = []
    for directory in ('training_code', 'app', 'training', 'evaluation', 'schemas', 'docker'):
        for path in sorted((root/directory).rglob('*')):
            if not path.is_file() or '__pycache__' in path.parts or path.name == 'data_manifest.json':
                continue
            with path.open('rb') as stream:
                checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
            files.append({'path': str(path), 'bytes': path.stat().st_size, 'sha256': checksum})
    model_dir = root / 'models/Qwen3.5-9B'
    model = json.loads((model_dir/'MODEL_MANIFEST.json').read_text())
    for name, checksum in model['files'].items():
        path = model_dir/name
        files.append({'path':str(path), 'bytes':path.stat().st_size, 'sha256':checksum})
    report = {'source':'Zalo AI Challenge 2026 DataMate provided training release',
              'source_version':'exact bundled bytes identified by SHA-256',
              'splits':config, 'files':files, 'model':model}
    readme = root/'README.md'
    with readme.open('a') as stream:
        stream.write('\n## Exact bundled model and data declaration\n\n')
        stream.write(f"Model: {model['repo']}\n\nRevision: {model['revision']}\n\n")
        stream.write(f"Original release date: {model['original_release_date']}\n\n")
        stream.write(f"Revision date: {model['revision_date']}\n\nLicence: {model['licence']}\n\n")
        stream.write(f"Stored parameters (all tensors): {model['parameters']}\n\n")
        stream.write(f"Provided train verification data: {len(questions)} questions; "
                     f"{config['verification_businesses']} businesses. No team training/generation.\n")
    for path in (readme, root/'predict.sh', root/'requirements.txt', model_dir/'MODEL_MANIFEST.json'):
        with path.open('rb') as stream:
            checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
        files.append({'path':str(path), 'bytes':path.stat().st_size, 'sha256':checksum})
    (training/'data_manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == '__main__':
    build('/code')
