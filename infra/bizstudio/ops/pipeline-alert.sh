#!/usr/bin/env bash
# Every 10 minutes from cron. Mails through the brevo-relay container when the worker stops
# beating or a video stays untranscribed; each issue is mailed at most once per day.
# NO_AUDIO and SKIPPED are final states the worker sets on purpose, so they are not issues.
set -euo pipefail

TO="${ALERT_TO:-vincenzopetronebiz@gmail.com}"
DATA=/root/bizstudio-recorder
STAMP_DIR="${STAMP_DIR:-$DATA}"
HEARTBEAT="$DATA/worker-state/heartbeat"
HEARTBEAT_MAX_MINUTES="${HEARTBEAT_MAX_MINUTES:-10}"
VIDEO_MIN_AGE_MINUTES="${VIDEO_MIN_AGE_MINUTES:-30}"
TODAY=$(date +%F)

issues=()
report=""

if [ ! -e "$HEARTBEAT" ]; then
	issues+=(heartbeat)
	report+="Worker heartbeat mancante ($HEARTBEAT)."$'\n'
else
	age=$(( ($(date +%s) - $(stat -c %Y "$HEARTBEAT")) / 60 ))
	if [ "$age" -ge "$HEARTBEAT_MAX_MINUTES" ]; then
		issues+=(heartbeat)
		report+="Worker heartbeat fermo da $age minuti ($(date -r "$HEARTBEAT" '+%F %T'))."$'\n'
	fi
fi

stuck=$(docker exec bizrec-mysql-1 sh -c 'MYSQL_PWD="$MYSQL_PASSWORD" mysql -ucap cap -N -B -e "
	SELECT id, COALESCE(transcriptionStatus, \"NULL\"), createdAt, REPLACE(name, \"\t\", \" \") FROM videos
	WHERE isScreenshot = 0
	  AND createdAt < UTC_TIMESTAMP() - INTERVAL '"$VIDEO_MIN_AGE_MINUTES"' MINUTE
	  AND (transcriptionStatus IS NULL OR transcriptionStatus NOT IN (\"COMPLETE\", \"NO_AUDIO\", \"SKIPPED\"))
	ORDER BY createdAt"') || {
	issues+=(mysql)
	report+="Query MySQL fallita: impossibile controllare le trascrizioni."$'\n'
	stuck=""
}

while IFS=$'\t' read -r id status created name; do
	[ -n "$id" ] || continue
	issues+=("video-$id")
	report+="Video $id ($name), creato $created UTC: transcriptionStatus $status."$'\n'
done <<<"$stuck"

[ "${#issues[@]}" -eq 0 ] && exit 0

fresh=()
for issue in "${issues[@]}"; do
	[ -e "$STAMP_DIR/.pipeline-alert-$issue-$TODAY" ] || fresh+=("$issue")
done
[ "${#fresh[@]}" -eq 0 ] && exit 0

body="$report"$'\n'"Log worker: docker logs --tail 200 bizrec-worker-1"$'\n'$'\n'"$(docker logs --tail 30 bizrec-worker-1 2>&1 || true)"
docker exec -i -e TO="$TO" -e COUNT="${#issues[@]}" bizrec-brevo-relay-1 node -e '
let body = "";
process.stdin.on("data", (c) => (body += c)).on("end", async () => {
	const res = await fetch("http://127.0.0.1:8025/emails", {
		method: "POST",
		headers: { authorization: `Bearer ${process.env.RELAY_TOKEN}`, "content-type": "application/json" },
		body: JSON.stringify({
			to: process.env.TO,
			subject: `Bizstudio Recorder: pipeline video, ${process.env.COUNT} problemi`,
			text: body,
		}),
	});
	if (!res.ok) process.exit(1);
});' <<<"$body"

for issue in "${fresh[@]}"; do
	touch "$STAMP_DIR/.pipeline-alert-$issue-$TODAY"
done
echo "$(date '+%F %T') alerted: ${fresh[*]}"
find "$STAMP_DIR" -maxdepth 1 -name '.pipeline-alert-*' -mtime +7 -delete
