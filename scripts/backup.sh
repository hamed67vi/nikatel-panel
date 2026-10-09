#!/usr/bin/env bash
# Nightly copy of the panel's data (database, auth, secret key, known hosts). Keeps 14 days.
#   0 3 * * * root bash /opt/nikatel/src/scripts/backup.sh
set -euo pipefail
DATA=/var/lib/nikatel/data
OUT=/var/lib/nikatel/backups
mkdir -p "$OUT"
STAMP=$(date +%Y%m%d-%H%M%S)
TMP=$(mktemp -d)
/opt/nikatel/venv/bin/python - "$DATA/nikatel.db" "$TMP/nikatel.db" <<'PY'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1]); dst = sqlite3.connect(sys.argv[2]); src.backup(dst); dst.close(); src.close()
PY
cp -p "$DATA"/auth.json "$DATA"/secret.key "$DATA"/known_hosts "$TMP"/ 2>/dev/null || true
tar -czf "$OUT/nikatel-$STAMP.tar.gz" -C "$TMP" .
chmod 600 "$OUT/nikatel-$STAMP.tar.gz"
rm -rf "$TMP"
find "$OUT" -name 'nikatel-*.tar.gz' -mtime +14 -delete
echo "backup: $OUT/nikatel-$STAMP.tar.gz"
