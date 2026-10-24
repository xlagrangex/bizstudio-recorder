#!/bin/bash
# Nightly: DB dump + videos still on local disk (older ones already live on the Storage Share tier).
set -euo pipefail
cd /root/bizstudio-recorder
set -a; . ./secrets.env; set +a
day=$(date +%F)
dest=hetzner:BizStudio/Recorder/backups
dump=$(mktemp /tmp/bizrec-db.XXXXXX.sql.gz)
trap 'rm -f "$dump"' EXIT
# Secrets go through the environment, not argv, so they stay out of ps.
# --no-tablespaces: the cap user lacks the PROCESS privilege mysqldump 8 wants for them.
MYSQL_PWD="$MYSQL_PASSWORD" docker exec -e MYSQL_PWD bizrec-mysql-1 \
  mysqldump --single-transaction --routines --no-tablespaces -ucap cap | gzip > "$dump"
[ "$(stat -c %s "$dump")" -gt 1000 ] || { echo "dump too small"; exit 1; }
rclone copyto "$dump" "$dest/db/$day.sql.gz"
RCLONE_CONFIG_SRC_ACCESS_KEY_ID="$MINIO_ROOT_USER" RCLONE_CONFIG_SRC_SECRET_ACCESS_KEY="$MINIO_ROOT_PASSWORD" \
  docker run --rm --network bizrec_default -v /root/.config/rclone:/config/rclone:ro \
  -e RCLONE_CONFIG_SRC_TYPE=s3 -e RCLONE_CONFIG_SRC_PROVIDER=Minio -e RCLONE_CONFIG_SRC_ENDPOINT=http://minio:9000 \
  -e RCLONE_CONFIG_SRC_ACCESS_KEY_ID -e RCLONE_CONFIG_SRC_SECRET_ACCESS_KEY \
  rclone/rclone:1.74 copy src:cap "$dest/hot" --max-age 48h --transfers 2
rclone delete "$dest/db" --min-age 30d
echo "ok $day $(date +%T)"
