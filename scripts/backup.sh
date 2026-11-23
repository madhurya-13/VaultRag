#!/usr/bin/env bash
# Bash equivalent of scripts/backup.ps1 (Linux/macOS with Docker).
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_DIR="${1:-backups}"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
TARGET="$PROJECT_ROOT/$OUTPUT_DIR"
mkdir -p "$TARGET"

DUMP="$TARGET/vaultrag-$TIMESTAMP.dump"
echo "Writing PostgreSQL custom-format dump to $DUMP"
docker exec vaultrag-postgres pg_dump -U vaultrag -d vaultrag -Fc -f /tmp/vaultrag.dump
docker cp vaultrag-postgres:/tmp/vaultrag.dump "$DUMP"
docker exec vaultrag-postgres rm -f /tmp/vaultrag.dump

echo "Verifying the dump can be listed by pg_restore"
docker cp "$DUMP" vaultrag-postgres:/tmp/verify.dump
docker exec vaultrag-postgres pg_restore --list /tmp/verify.dump > "$TARGET/vaultrag-$TIMESTAMP.toc.txt"
head -5 "$TARGET/vaultrag-$TIMESTAMP.toc.txt"
docker exec vaultrag-postgres rm -f /tmp/verify.dump

echo "Archiving the private document volume"
docker run --rm -v vaultrag_documents:/data:ro -v "$TARGET:/backup" alpine \
  tar czf "/backup/documents-$TIMESTAMP.tar.gz" -C /data .

echo "Backup complete:"
ls -lh "$DUMP" "$TARGET/documents-$TIMESTAMP.tar.gz"
echo
echo "Restore (destructive; stop the API first):"
echo "  docker exec vaultrag-postgres pg_restore -U vaultrag -d vaultrag --clean --if-exists <dump>"
echo "  docker run --rm -v vaultrag_documents:/data -v \"$TARGET:/backup\" alpine tar xzf /backup/documents-$TIMESTAMP.tar.gz -C /data"
