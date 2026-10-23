#!/usr/bin/env bash
# One-time, idempotent setup for the meet bot. Run on vincenzoserver from this dir:
#   ./bootstrap.sh secrets   -> generate missing secrets, MinIO user/bucket, Cap API key
#   ./bootstrap.sh attendee  -> (after `up -d attendee-app`) Attendee account + API key
set -euo pipefail
DATA=${BIZREC_DATA:-/root/bizstudio-recorder}
SECRETS=$DATA/secrets.env
CAP_USER_ID=${CAP_USER_ID:-cng79m7693kkajy}
OWNER_EMAIL=${OWNER_EMAIL:-vincenzopetronebiz@gmail.com}

get() { grep -E "^$1=" "$SECRETS" | tail -1 | cut -d= -f2-; }
ensure() {
  if [ -z "$(get "$1")" ]; then
    printf '%s=%s\n' "$1" "$2" >> "$SECRETS"
    echo "added $1"
  fi
}
rand() { openssl rand -hex "$1"; }

secrets() {
  chmod 600 "$SECRETS"
  ensure ATTENDEE_DJANGO_SECRET_KEY "$(rand 32)"
  ensure ATTENDEE_CREDENTIALS_ENCRYPTION_KEY "$(openssl rand 32 | base64 | tr '+/' '-_')"
  ensure ATTENDEE_POSTGRES_PASSWORD "$(rand 24)"
  ensure ATTENDEE_S3_KEY "attendee"
  ensure ATTENDEE_S3_SECRET "$(rand 24)"
  ensure ATTENDEE_UI_PASSWORD "$(rand 12)"
  ensure MEETBOT_PASSWORD "$(rand 12)"
  ensure MEETBOT_CAP_API_KEY "$(cat /proc/sys/kernel/random/uuid)"
  mkdir -p "$DATA/meet/postgres" "$DATA/meet/redis" "$DATA/meet/bridge"

  docker exec -i -e MC_KEY="$(get ATTENDEE_S3_KEY)" -e MC_SECRET="$(get ATTENDEE_S3_SECRET)" bizrec-minio-1 sh -s <<'EOF'
set -e
mc alias set boot http://127.0.0.1:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
mc mb --ignore-existing boot/attendee
cat > /tmp/attendee-policy.json <<'JSON'
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["s3:*"],"Resource":["arn:aws:s3:::attendee","arn:aws:s3:::attendee/*"]}]}
JSON
mc admin policy create boot attendee-rw /tmp/attendee-policy.json >/dev/null
mc admin user add boot "$MC_KEY" "$MC_SECRET" >/dev/null
mc admin policy attach boot attendee-rw --user "$MC_KEY" >/dev/null 2>&1 || true
# Safety net: the bridge deletes each recording after importing it into Cap.
case "$(mc ilm rule ls boot/attendee 2>/dev/null)" in
  *[Ee]xpir*) ;;
  *) mc ilm rule add --expire-days 30 boot/attendee >/dev/null ;;
esac
rm -f /tmp/attendee-policy.json
echo "minio: bucket attendee + user ready"
EOF

  docker exec -i -e K="$(get MEETBOT_CAP_API_KEY)" -e U="$CAP_USER_ID" bizrec-mysql-1 sh -c \
    'mysql -ucap -p"$MYSQL_PASSWORD" cap -e "INSERT IGNORE INTO auth_api_keys (id, userId, source) VALUES (\"$K\", \"$U\", \"unknown\");"' 2>/dev/null
  echo "cap: api key for $CAP_USER_ID ready"
}

attendee() {
  local out
  out=$(docker compose --env-file "$SECRETS" exec -T -e OWNER_EMAIL="$OWNER_EMAIL" -e UI_PASSWORD="$(get ATTENDEE_UI_PASSWORD)" \
    -e HAS_KEY="$(get ATTENDEE_API_KEY)" attendee-app python manage.py shell -c '
import os
from allauth.account.models import EmailAddress
from accounts.models import User
from bots.models import ApiKey, Project
email = os.environ["OWNER_EMAIL"]
user = User.objects.filter(email=email).first()
if not user:
    user = User.objects.create_user(username=email, email=email, password=os.environ["UI_PASSWORD"])
EmailAddress.objects.update_or_create(user=user, email=email, defaults={"verified": True, "primary": True})
project = Project.objects.filter(organization=user.organization).order_by("id").first()
if not os.environ.get("HAS_KEY"):
    _, key = ApiKey.create(project=project, name="meet-bridge")
    print("KEY=" + key)
print("project=" + project.object_id)
')
  echo "$out" | grep -v '^KEY='
  local key
  key=$(echo "$out" | sed -n 's/^KEY=//p')
  if [ -n "$key" ]; then ensure ATTENDEE_API_KEY "$key"; fi
}

"${1:?usage: bootstrap.sh secrets|attendee}"
