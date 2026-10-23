#!/usr/bin/env bash
# Hourly from cron. Mails once per day while / stays above THRESHOLD, through the
# brevo-relay container so the relay token never leaves its environment.
set -euo pipefail

THRESHOLD="${THRESHOLD:-85}"
TO="${ALERT_TO:-vincenzopetronebiz@gmail.com}"
STAMP=/root/bizstudio-recorder/.disk-alert-$(date +%F)

used=$(df --output=pcent / | tail -1 | tr -dc '0-9')
[ "$used" -lt "$THRESHOLD" ] && exit 0
[ -e "$STAMP" ] && exit 0

summary=$(df -h / | tail -1; echo; docker system df 2>/dev/null || true)
docker exec -i -e TO="$TO" -e USED="$used" bizrec-brevo-relay-1 node -e '
let body = "";
process.stdin.on("data", (c) => (body += c)).on("end", async () => {
	const res = await fetch("http://127.0.0.1:8025/emails", {
		method: "POST",
		headers: { authorization: `Bearer ${process.env.RELAY_TOKEN}`, "content-type": "application/json" },
		body: JSON.stringify({
			to: process.env.TO,
			subject: `vincenzoserver: disco al ${process.env.USED}%`,
			text: body,
		}),
	});
	if (!res.ok) process.exit(1);
});' <<<"$summary"

touch "$STAMP"
find /root/bizstudio-recorder -maxdepth 1 -name '.disk-alert-*' -mtime +7 -delete
