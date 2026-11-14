#!/usr/bin/env bash
# Export one frozen image, enforce BTC's uncompressed tar cap, then create MD5.
set -euo pipefail
team="${1:?Usage: bash docker/export.sh lowercase_team [output_directory]}"
[[ "$team" =~ ^[a-z0-9_]+$ ]] || { echo 'Team must contain lowercase letters, digits, underscores' >&2; exit 2; }
image="zaic2026_datamate_${team}:final"
output="${2:-.}"
mkdir -p "$output"
archive="$output/zaic2026_datamate_${team}.tar.gz"
[[ ! -e "$archive" ]] || { echo "Already exists: $archive" >&2; exit 2; }
platform=$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$image")
[[ "$platform" == 'linux/amd64' ]] || { echo "Wrong platform: $platform" >&2; exit 2; }
partial=$(mktemp "$output/.export.XXXXXX")
trap 'rm -f "$partial"' EXIT
# pipefail propagates a failed docker save instead of publishing a tiny corrupt gzip.
docker save "$image" | gzip -n > "$partial"
python3 - "$partial" "$archive" <<'PY'
import gzip, hashlib, json, os, sys
from pathlib import Path
partial, archive = map(Path, sys.argv[1:])
size = 0
with gzip.open(partial, 'rb') as stream:
    while block := stream.read(8 * 1024 * 1024):
        size += len(block)
        if size > 100_000_000_000:
            raise SystemExit(f'Uncompressed tar exceeds 100 GB: {size}')
with partial.open('rb') as stream:
    md5 = hashlib.file_digest(stream, 'md5').hexdigest()
os.replace(partial, archive)
Path(str(archive)+'.md5').write_text(f'{md5}  {archive.name}\n')
Path(str(archive)+'.size.txt').write_text(str(size)+'\n')
print(json.dumps({'archive':str(archive), 'uncompressed_tar_bytes':size, 'md5':md5}, indent=2))
PY
docker image inspect "$image" > "${archive}.image.json"
