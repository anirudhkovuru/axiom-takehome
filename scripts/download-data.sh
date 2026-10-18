#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
data_dir="$repo_root/data"
database_path="$data_dir/data.duckdb"
readme_path="$data_dir/README.source.md"
database_id="11LBqqg2xBB4QNrrp6MCLCiKlnzEvFx-Z"
readme_id="14Q5SIgWHCwbGcLVcxL4AyH0jXHLJwXuq"

mkdir -p "$data_dir"

if [[ -f "$database_path" ]] && [[ $(stat -f%z "$database_path" 2>/dev/null || stat -c%s "$database_path") -gt 400000000 ]]; then
  echo "Dataset already present at $database_path"
else
  temp_database="$(mktemp "$data_dir/data.duckdb.XXXXXX")"
  trap 'rm -f "$temp_database"' EXIT
  curl -L --fail --progress-bar \
    "https://drive.usercontent.google.com/download?id=${database_id}&export=download&confirm=t" \
    -o "$temp_database"
  database_size=$(stat -f%z "$temp_database" 2>/dev/null || stat -c%s "$temp_database")
  if [[ "$database_size" -lt 400000000 ]]; then
    echo "Downloaded file is unexpectedly small: ${database_size} bytes" >&2
    exit 1
  fi
  mv "$temp_database" "$database_path"
  trap - EXIT
fi

curl -L --fail --silent --show-error \
  "https://drive.usercontent.google.com/download?id=${readme_id}&export=download&confirm=t" \
  -o "$readme_path"

echo "Scientific dataset is ready at $database_path"
