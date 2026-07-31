#!/bin/bash
set -Eeuo pipefail

readonly WRITABLE_DIRS=(
    /app/data
    /app/logs
    /app/backups
    /app/static/uploads/images
)

printf 'Starting xianyu-auto-reply system as uid=%s gid=%s...\n' "$(id -u)" "$(id -g)"

for directory in "${WRITABLE_DIRS[@]}"; do
    mkdir -p "${directory}"
    test -w "${directory}" || {
        printf 'ERROR: required directory is not writable: %s\n' "${directory}" >&2
        exit 1
    }
done

find /app/logs -type f -name "*.log" -mtime +7 -delete 2>/dev/null || true

exec python Start.py
