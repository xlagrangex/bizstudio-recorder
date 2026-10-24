# Runbook Bizstudio Recorder

Server: `ssh vincenzoserver`. Segreti in `/root/bizstudio-recorder/secrets.env` (mai stamparli, mai committarli). Dati in `/root/bizstudio-recorder/` (mysql-data, minio-data, worker-state, rclone-cache).

## Compose

Stack principale `bizrec` (rclone, minio, mysql, brevo-relay, media-server, worker, cap-web):

```sh
cd /root/bizstudio-recorder/src/infra/bizstudio
alias bc='env -u CAP_ALLOWED_SIGNUP_DOMAINS docker compose --env-file /root/bizstudio-recorder/secrets.env'
bc ps
bc up -d                         # applica le modifiche al compose
bc up -d --no-deps --no-build cap-web   # ricrea un solo servizio
bc restart worker
```

Meet bot `bizrec-meet`: stessi comandi da `/root/bizstudio-recorder/src/infra/bizstudio/meet-bot`.

`env -u CAP_ALLOWED_SIGNUP_DOMAINS` serve perché una variabile esportata nella shell vincerebbe su `secrets.env`.

Il sorgente è `infra/bizstudio/` del repo (branch `bizstudio/rebrand`). Per aggiornare il server dal Mac:

```sh
rsync -a infra/bizstudio/<dir>/ vincenzoserver:/root/bizstudio-recorder/src/infra/bizstudio/<dir>/
```

Limiti: media-server, cap-web e worker hanno `mem_limit: 1g`. Se un container viene ucciso per OOM, `docker inspect <container> --format '{{.State.OOMKilled}}'` lo conferma.

## Log

```sh
docker logs --tail 200 -f bizrec-cap-web-1
docker logs --tail 200 -f bizrec-worker-1        # pipeline trascrizione/titoli/note
docker logs --tail 200 bizrec-media-server-1
docker logs --tail 200 bizrec-minio-1
tail -50 /root/bizstudio-recorder/backup.log
tail -50 /root/bizstudio-recorder/disk-alert.log
```

I log Docker di rclone, media-server e cap-web ruotano a 3 x 10 MB.

## Rielaborare un video

Il worker salta i video che ha già chiuso (stato `done`, `failed`, `no_audio`, `skipped` nel suo `worker.db`) e quelli con `transcriptionStatus` finale in MySQL. Per rifarne uno, con `<ID>` = id del video (quello nell'URL `/s/<ID>`):

```sh
docker exec bizrec-worker-1 python -c "import sqlite3; c=sqlite3.connect('/state/worker.db'); c.execute('DELETE FROM jobs WHERE video_id=?', ('<ID>',)); c.commit()"
docker exec bizrec-mysql-1 sh -c 'MYSQL_PWD="$MYSQL_PASSWORD" mysql -ucap cap -e "UPDATE videos SET transcriptionStatus=NULL WHERE id=\"<ID>\""'
docker logs -f bizrec-worker-1 | grep <ID>
```

Il worker lo riprende al giro successivo (ogni 20 s, dopo 60 s dall'ultima modifica del video). Stato dei job: `docker exec bizrec-worker-1 python -c "import sqlite3; [print(tuple(r)) for r in sqlite3.connect('/state/worker.db').execute('SELECT video_id, stage, attempts, last_error FROM jobs')]"`.

## Backup e restore

Cron `/etc/cron.d/bizrec-backup`, ogni notte alle 04:15: `infra/bizstudio/ops/backup.sh` (copia sul server in `src/infra/bizstudio/ops/`). Fa:

- dump MySQL gzip in `hetzner:BizStudio/Recorder/backups/db/AAAA-MM-GG.sql.gz` (tenuti 30 giorni);
- copia dei video caricati nelle ultime 48 h da MinIO a `hetzner:BizStudio/Recorder/backups/hot`. I video più vecchi passano già sul tier freddo della Storage Share (regola ILM di MinIO, tier `HETZNER` servito dal container rclone).

Controllo:

```sh
tail -5 /root/bizstudio-recorder/backup.log      # deve finire con "ok <data>"
rclone ls hetzner:BizStudio/Recorder/backups/db
```

Lancio manuale: `/root/bizstudio-recorder/src/infra/bizstudio/ops/backup.sh >> /root/bizstudio-recorder/backup.log 2>&1`.

Prova di restore su un MySQL usa e getta (non tocca il DB vero):

```sh
rclone copyto hetzner:BizStudio/Recorder/backups/db/<data>.sql.gz /tmp/drill.sql.gz
docker run -d --rm --name bizrec-restore-drill -e MYSQL_ROOT_PASSWORD=drill -e MYSQL_DATABASE=cap mysql:8.0
# attendere che risponda: docker exec bizrec-restore-drill mysqladmin -uroot -pdrill ping
gunzip -c /tmp/drill.sql.gz | docker exec -i bizrec-restore-drill mysql -uroot -pdrill cap
docker exec bizrec-restore-drill mysql -uroot -pdrill -e "SELECT COUNT(*) FROM videos; SELECT COUNT(*) FROM users" cap
docker stop bizrec-restore-drill && rm /tmp/drill.sql.gz   # --rm elimina anche il volume anonimo
```

Restore vero (distruttivo, solo dopo aver fermato chi scrive):

```sh
bc stop cap-web worker
gunzip -c /tmp/<data>.sql.gz | docker exec -i bizrec-mysql-1 sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot cap'
bc start cap-web worker
```

## Alert

Entrambi mandano una mail a vincenzopetronebiz@gmail.com tramite il container brevo-relay, al massimo una volta al giorno per problema.

- **Disco** (`ops/disk-alert.sh`, cron `/etc/cron.d/bizrec-disk-alert`, ogni ora): `/` sopra l'85%. Cosa guardare: `df -h /`, `docker system df`, `du -sh /root/bizstudio-recorder/*`. Si libera spazio con `docker image prune` / `docker builder prune` e svuotando `rclone-cache`; i video vecchi stanno già sulla Storage Share. Non cancellare `minio-data` né `mysql-data`.
- **Pipeline** (`ops/pipeline-alert.sh`, ogni 10 minuti): heartbeat del worker fermo da più di 10 minuti, oppure un video più vecchio di 30 minuti senza `transcriptionStatus` finale. Cosa fare: `docker logs --tail 200 bizrec-worker-1`; se il worker è bloccato `bc restart worker`; se il video è in `failed`, leggere `last_error` e rielaborarlo (sezione sopra).

## Deploy dell'immagine web

```sh
gh workflow run docker-build-web.yml -R xlagrangex/bizstudio-recorder --ref bizstudio/rebrand -f tag=latest
gh run watch -R xlagrangex/bizstudio-recorder $(gh run list -R xlagrangex/bizstudio-recorder -w docker-build-web.yml -L 1 --json databaseId -q '.[0].databaseId')
```

Poi sul server:

```sh
cd /root/bizstudio-recorder/src/infra/bizstudio
docker pull ghcr.io/xlagrangex/cap-web:latest
bc up -d --no-deps cap-web
curl -s -o /dev/null -w '%{http_code}\n' https://rec.bizstudio.it/login   # 200
```

Il container torna `healthy` in circa 10-60 s; durante il riavvio il sito è giù per qualche secondo.

## Registrazioni locali sul Mac

`~/Library/Application Support/it.bizstudio.recorder/recordings/`, una cartella `<nome> <data>.cap` per registrazione:

- `content/` registrazione grezza (segmenti schermo, camera, audio, cursori);
- `output/result.mp4` export finale, se è stato fatto;
- `recording-meta.json` metadati, compreso lo stato dell'upload.

Restano lì anche dopo l'upload finché non vengono cancellate dall'app.

## Upload fallito

1. Non cancellare la cartella `.cap`: finché c'è, il video è recuperabile.
2. Dall'app: Impostazioni → Registrazioni → **Reupload** sulla registrazione (oppure dall'editor). Ricarica sullo stesso link.
3. Se il pulsante non c'è o fallisce di nuovo: aprire la registrazione nell'editor, esportare in MP4 (o usare `output/result.mp4` se esiste) e caricarlo dalla dashboard web di rec.bizstudio.it (importa video).
4. Lato server, se l'upload risulta bloccato: `docker logs --tail 200 bizrec-cap-web-1 | grep -i upload` e lo stato in MySQL:

```sh
docker exec bizrec-mysql-1 sh -c 'MYSQL_PWD="$MYSQL_PASSWORD" mysql -ucap cap -e "SELECT video_id, phase, uploaded, total, processing_error, updated_at FROM video_uploads ORDER BY updated_at DESC LIMIT 10"'
```

Un video con `phase` diversa da `complete` non viene trascritto dal worker finché l'upload non si chiude.
