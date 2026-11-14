"""Count stored, unquantized safetensors parameters and verify the frozen package offline."""
import hashlib
import json
import math
import struct
from datetime import datetime, timezone
from pathlib import Path


def inventory(root):
    root = Path(root)
    if not (root / 'config.json').is_file() or not (root / 'tokenizer.json').is_file():
        raise ValueError('Missing model config/tokenizer')
    if not list(root.glob('LICENSE*')):
        raise ValueError('Missing model licence')
    shards = sorted(root.glob('*.safetensors'))
    if not shards:
        raise ValueError('No bundled weights; inference may not download them')
    parameters, names = 0, set()
    for shard in shards:
        with shard.open('rb') as stream:
            size = struct.unpack('<Q', stream.read(8))[0]
            if size > 100_000_000:
                raise ValueError('Invalid safetensors header')
            tensors = json.loads(stream.read(size))
        for name, tensor in tensors.items():
            if name == '__metadata__':
                continue
            if name in names or tensor['dtype'] not in ('BF16', 'F16', 'F32', 'F64'):
                raise ValueError('Duplicate tensor or unsupported packed weights; count original parameters explicitly')
            count = math.prod(tensor['shape'])
            start, end = tensor['data_offsets']
            width = {'BF16':2, 'F16':2, 'F32':4, 'F64':8}[tensor['dtype']]
            if start < 0 or end - start != count * width:
                raise ValueError('Tensor byte count does not match shape')
            names.add(name)
            parameters += count
            if 8 + size + tensor['data_offsets'][1] > shard.stat().st_size:
                raise ValueError('Truncated weight shard')
    index = root / 'model.safetensors.index.json'
    if index.exists():
        mapping = json.loads(index.read_text())['weight_map']
        if set(mapping) != names or set(mapping.values()) != {s.name for s in shards}:
            raise ValueError('Weight index does not match packaged shards')
    if not 0 < parameters <= 15_000_000_000:
        raise ValueError(f'Parameter budget exceeded: {parameters}')
    files = {}
    for path in sorted(root.iterdir()):
        if path.is_file() and path.name != 'MODEL_MANIFEST.json':
            with path.open('rb') as stream:
                files[path.name] = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'parameters': parameters, 'files': files}


def verify(root):
    root = Path(root)
    declared = json.loads((root / 'MODEL_MANIFEST.json').read_text())
    if datetime.fromisoformat(declared['revision_date']) >= datetime(2026, 10, 26, tzinfo=timezone.utc):
        raise ValueError('Model revision violates cutoff')
    actual = inventory(root)
    if any(declared[k] != actual[k] for k in ('parameters', 'files')):
        raise ValueError('Frozen model package has changed')
    return declared


if __name__ == '__main__':
    import sys
    print(json.dumps(verify(sys.argv[1]), indent=2))
