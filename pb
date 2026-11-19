#!/bin/sh
# Runs pb with the newest Python 3.11+ on this machine (tomllib is stdlib from 3.11).
for py in python3.14 python3.13 python3.12 python3.11 python3; do
  if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    cd "$(dirname "$0")" && exec "$py" -m providerbench "$@"
  fi
done
echo "pb needs Python 3.11 or newer (for tomllib). Install one, e.g. 'brew install python' or 'uv python install 3.12'." >&2
exit 1
