# Production operations

MEPPP runs as one non-root application container behind an existing host Nginx. The production shape intentionally stays small: one Gunicorn worker with two threads, one local `/data` mount, and no Redis, queue, search, or database service. `deploy/README.md` contains the aaPanel/Nginx/Cloudflare packet.

This repository does not change a server, DNS record, Cloudflare zone, or aaPanel configuration by itself.

## Repository release gate

The release policy requires `main` changes to arrive through pull requests. GitHub branch protection must require
the `quality`, `browser`, and `container` checks against the latest `main`, including for
administrators. Create annotated release tags only from the protected `main` commit after
all three checks pass; never tag an unreviewed branch or a locally patched server tree.

## Runtime safety boundary

- The container uses fixed UID/GID `10001:10001`, drops every Linux capability, enables `no-new-privileges`, and has a read-only root filesystem.
- Only `/data` and a 192 MiB `noexec` temporary filesystem are writable. Keep
  `MEPPP_TMPFS_SIZE` at or above 192 MiB so two bounded video-processing requests
  cannot exhaust the temporary upload, remux, and poster workspace.
- The service is published only on host loopback, default `127.0.0.1:18080`.
- CPU, memory, PID, and JSON log rotation limits protect the shared host.
- A fixed private bridge makes the immediate proxy address deterministic. If its subnet collides, all network values and `MEPPP_TRUSTED_PROXY_IPS` must change together.
- Only one application container may write SQLite. Never scale this Compose service above one replica.
- Never run `docker compose down -v`, a global Docker prune, or a global firewall change on the shared host.

## First-start configuration

Copy `.env.example` to `.env`; never commit the real file. Generate the application secret independently:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

The placeholder is rejected in production. Keep `.env` mode `600`. For aaPanel, prefer the explicit host bind mount documented in `deploy/README.md`, owned by `10001:10001`, so backups are visible to the host operator.

Before first start:

1. Confirm `18080/tcp` is unused.
2. Confirm `172.30.89.0/28` does not overlap another Docker network, host route, or VPN.
3. If the target still lacks Compose, run the checksum-pinned `deploy/install_compose_plugin.sh`; never pipe a network download into a shell.
4. Run `docker compose version` and `docker compose config --quiet`.
5. Build an immutable release tag with `DOCKER_BUILDKIT=1 docker build`; stop if the engine cannot build without the absent buildx plugin.
6. Keep registration closed and do not change public DNS yet.

Start and inspect:

```bash
RELEASE_TAG=v0.1.0-rc.13
test -z "$(docker image ls --quiet meppp:${RELEASE_TAG})"
DOCKER_BUILDKIT=1 docker build --pull --tag "meppp:${RELEASE_TAG}" .
docker image inspect "meppp:${RELEASE_TAG}" >/dev/null
docker compose up --detach --no-build --wait app
docker compose ps
docker compose logs --tail=100 app
curl --fail --header 'Host: meppp.com' http://127.0.0.1:18080/health/ready
```

The container health probe sends `MEPPP_HEALTHCHECK_HOST` explicitly. That value must appear in `MEPPP_ALLOWED_HOSTS`; a successful database connection alone is not considered an HTTP-ready service.

Create the first owner interactively only after the private origin canary passes:

```bash
docker compose exec app python manage.py createsuperuser
```

There is no default administrator credential.

The container reconciles the code-defined `运营` and `审核` groups after every migration and before serving traffic. It never creates an owner or assigns people to a group. The owner can assign active staff through Django Admin; only the owner can manage member accounts and registration invitations in the first operational milestone. Reconciliation is deliberately corrective: permissions added to these two managed groups outside the code manifest are removed at the next start, while group membership is preserved.

For an invitation-only opening, keep registration closed during the private smoke test. Then use **运营总览 → 注册邀请** to issue a time-limited token, copy the plaintext from that one response, and deliver it through a separate private channel. The database and audit log retain only a digest and short hint. Change registration mode to **仅限邀请** only after the join, pending-content, review, notification, and withdrawal smoke path passes.

For a small public opening, complete the same private smoke test, switch moderation to **发布前审核**, and only then switch registration to **开放注册**. New registrations require an email address and receive a display-once recovery code; the plaintext credential is never written to the database. Keep the registration, login and recovery rate limits in place.

The public-opening defaults allow at most five pending entries per member, 100 MiB of
member media per day, five video-processing attempts per hour, and 20 GiB of total
referenced site media while preserving at least 256 MiB of free storage. The application
checks capacity before decoding video or storing an avatar and repeats the authoritative
check inside the write transaction. Avatar processing is limited to five attempts per hour
per member/IP, and the `avatar_uploads_enabled` admin switch blocks new uploads while
leaving removal available. Tune only the positive `MEPPP_MEMBER_PENDING_ENTRY_LIMIT`,
`MEPPP_MEMBER_DAILY_MEDIA_BYTES`, `MEPPP_MEDIA_MAX_TOTAL_BYTES`, and
`MEPPP_MEDIA_MIN_FREE_BYTES` values; keep a documented storage and backup budget.

The image includes FFmpeg and ffprobe for bounded self-uploaded video validation and metadata-stripping remux. Install and manually run `meppp-external-refresh.service`, then enable `meppp-external-refresh.timer`; it refreshes only due X/YouTube cards in small batches through fixed official endpoints. A failed source refresh must not block ordinary text or local-media publishing.

## Reverse proxy trust

Set `MEPPP_TRUST_PROXY=1` only for the documented Nginx path. The Nginx template overwrites both `X-Forwarded-Proto` and `X-Real-IP`. Django accepts them only when the direct peer is the exact fixed Docker gateway in `MEPPP_TRUSTED_PROXY_IPS`; client-supplied forwarded headers are discarded.

The target host already trusts Cloudflare's `CF-Connecting-IP` through a global Nginx real-IP configuration whose IPv4/IPv6 networks were checked against Cloudflare's official current lists. The MEPPP vhost must not include a duplicate. `deploy/update_cloudflare_real_ip.py` is only a template for another host proven to have no global definition. Always review `nginx -T` and run `nginx -t` before reload.

Start HSTS at 3600 seconds. Do not enable `includeSubDomains` or preload until every current and planned subdomain is proven HTTPS-only and the rollback window has passed.

## SQLite backup policy

The application command uses Python/SQLite's online backup API, runs `PRAGMA integrity_check`, writes the database with mode `600`, and creates a standard SHA-256 sidecar manifest. Retention keeps the newest artifact in at least seven UTC calendar-day buckets plus four ISO week buckets. Values below `7` daily or `4` weekly are rejected.

Create a backup while the service is running:

```bash
docker compose exec -T app python manage.py backup_sqlite
```

The entrypoint runs the same verified backup automatically before migrations whenever an existing database is present. That protects upgrades, but it does not replace the scheduled daily job.

Configure an aaPanel daily task using `deploy/cron/meppp-backup.sh`. It creates the online backup, runs a non-destructive restore drill, verifies every image, video and poster against that database snapshot, refuses a destination on the same filesystem, incrementally copies immutable WebP/MP4/WebM media plus the database to an already-mounted independent disk, and verifies database and media checksums. Example aaPanel shell task:

```bash
cd /opt/meppp && \
MEPPP_APP_DIR=/opt/meppp \
MEPPP_HOST_DATA_DIR=/srv/meppp/data \
MEPPP_OFFSITE_DIR=/www/backup/meppp.com \
sh ./deploy/cron/meppp-backup.sh
```

Schedule it once daily after the independent disk is mounted and monitored. The template intentionally fails instead of silently copying to the source filesystem. A backup inside the same `/data` filesystem is not disaster recovery. For a remote object store or backup host, keep the local verified-copy gate and add the provider's separately authenticated transfer after it; do not put those credentials in `.env` or this repository.

### Pull backup to an operator Mac

When the server has no independently mounted backup device, use the split preparation/pull path:

1. Install the matching service and timer from `deploy/systemd/`. The service runs the release-matched `deploy/cron/meppp-prepare-backup.sh` from `/opt/meppp`. Enable the timer only after a manual service run succeeds. It creates an online database backup, performs a restore drill, verifies snapshot media, and writes a media manifest before the pull window.
2. Generate a dedicated SSH key on the Mac. Add only its public key to the server with an `authorized_keys` restriction equivalent to `command="/usr/bin/rrsync -ro /srv/meppp/data",restrict`. Back up `authorized_keys` first and confirm the installed `rrsync` path. This key must not receive an unrestricted shell.
3. Create the destination directory on the verified external volume. Copy `deploy/macos/com.meppp.offsite-backup.plist.example` to the user's LaunchAgents directory and replace every placeholder, including the expected external-volume UUID and a private lock-file path under the user's local cache directory. The lock is coordination metadata only; databases and media still go exclusively to the verified external volume. Do not load a template containing placeholders, and do not use a symlink as the destination or lock path.
4. Run `deploy/macos/meppp-offsite-pull.sh` manually once. Confirm that its `LAST_SUCCESS` marker is written on the external disk, the selected database returns `integrity_check=ok`, the copied database and media manifests verify, and the reported backup age is below 26 hours.
5. Load the LaunchAgent and run one attended test. Monitor the success marker; a loaded job alone is not backup proof.

The pull script fails closed when the external disk is absent, has the wrong UUID, is the root filesystem, lacks the configured free-space floor, or receives an old/mismatched snapshot. It uses additive copies only and never falls back to the Mac system disk. At every release and at least monthly, copy a selected external database artifact back to an isolated server staging directory and run `restore_sqlite` plus `verify_media` there. Do not overwrite the live database during this proof.

If an unattended LaunchAgent receives `Operation not permitted` while writing the removable volume, its manual success is still useful recovery evidence but is not proof of scheduled backup. Do not redirect it to the Mac system disk. Use a separately hosted Linux backup target instead: install `deploy/remote/meppp-remote-pull.sh`, `deploy/remote/meppp-remote-monitor.sh`, their environment example, and the matching `deploy/systemd/meppp-remote-*` units. Keep the source key root-only under `/etc/meppp`; systemd passes it to the non-login service account with `LoadCredential`. Run the pull manually, complete an isolated restore drill, and only then enable both the daily pull and hourly health-monitor timers. A pull failure leaves `LAST_FAILURE`; stale or damaged backup evidence leaves `MONITOR_FAILURE`; the next fully verified pull clears both. The source-side key remains forced through read-only `rrsync`, so the backup target can read the prepared database/media tree but cannot obtain a shell or write to production.

The backup target requires a non-login account plus a traversable, non-listable parent directory. Keep credentials owned by root and the backup tree owned only by the service account:

```bash
useradd --system --user-group --home-dir /nonexistent --no-create-home \
  --shell /usr/sbin/nologin meppp-backup
install -d -o root -g root -m 700 /etc/meppp
install -d -o root -g meppp-backup -m 710 /srv/backups
install -d -o meppp-backup -g meppp-backup -m 700 /srv/backups/meppp.com
install -o root -g root -m 600 SOURCE_READ_ONLY_KEY /etc/meppp/source-readonly-ed25519
install -o root -g root -m 600 PINNED_KNOWN_HOSTS /etc/meppp/source-known_hosts
```

Back up the source account's `authorized_keys` before adding the dedicated public key. Restrict it to the backup target's fixed address, the distribution `rrsync`, read-only mode, and the MEPPP data root:

```text
from="BACKUP_TARGET_IP",restrict,command="/usr/bin/rrsync -ro /srv/meppp/data" ssh-ed25519 DEDICATED_PUBLIC_KEY
```

Install the scripts, reviewed environment file, services, and timers. Run both services manually and inspect `LAST_SUCCESS` before enabling either timer; never enable from unit-file validation alone.
The packaged units allow writes only to `/srv/backups/meppp.com` and `/run/meppp-remote-pull`; changing either environment value requires a matching review and update of every related unit.

Current macOS rsync may send `--dirs`, which Debian 11's older `/usr/bin/rrsync` allow-list does not recognize. After proving that exact error, install `deploy/rrsync/meppp-rrsync-compat.sh` as `/usr/local/sbin/meppp-rrsync` and reference it in the key's forced command. The wrapper converts only that token to the equivalent `-d` before invoking the distribution checker; it does not weaken the read-only or restricted-directory boundary.

Database backup alone is incomplete once an entry has images. Submitted media is immutable even after moderation or author withdrawal, so the scheduled job keeps a non-destructive media superset and a snapshot-specific SHA-256 manifest. It never uses `--delete`. Before any disaster recovery on a fresh host, restore the verified media mirror into `/data/media`, then restore the selected database and run:

```bash
docker compose exec -T app python manage.py verify_media \
  --database /data/restore-staging/REPLACE/restored.sqlite3 \
  --media-root /data/media
```

Old unreferenced crash-window files are reported, not deleted, by default. Review the output before the explicit cleanup form:

```bash
docker compose exec -T app python manage.py reconcile_media
docker compose exec -T app python manage.py reconcile_media --delete --minimum-age-hours 48
```

SQLite must stay on a local filesystem, never NFS, a synchronized drive, or an object-store mount. Multiple replicas, sustained write contention, or queue-heavy workloads trigger a planned PostgreSQL migration.

## Non-destructive restore drill

Every release and every monthly operations check must restore the newest backup into a newly created temporary directory and confirm `PRAGMA integrity_check=ok`:

```bash
docker compose exec -T app sh -c \
  'latest=$(find /data/backups/sqlite -maxdepth 1 -name "meppp-*.sqlite3" -type f | sort | tail -1); test -n "$latest"; python manage.py restore_sqlite "$latest"'
```

The default `restore_sqlite` path never overwrites the live database. It verifies the manifest and source database, restores through SQLite's backup API to a fresh directory under `/data/restore-drills` (not the size-limited `/tmp`), verifies the restored database, reports `live_database_untouched=yes`, and removes the drill directory. Add `--keep-drill` only when an operator needs to inspect the temporary artifact.

## Staged recovery and manual offline cutover

There is deliberately no application command that overwrites the live database. First restore the chosen artifact into a new path on the same `/data` filesystem and keep it for inspection:

```bash
docker compose exec -T app python manage.py restore_sqlite \
  /data/backups/sqlite/CHOSEN_BACKUP.sqlite3 \
  --drill-root /data/restore-staging --keep-drill
```

Record the reported `temporary_path` as `STAGED_DB`. Before stopping service, run one more online backup, drill that new backup, copy it and its manifest to independent storage, and record the current `MEPPP_IMAGE`.

The actual cutover is an attended maintenance procedure, not an automated command:

1. Run `docker compose stop app`, then prove `docker compose ps` shows no running MEPPP application writer.
2. Recheck `STAGED_DB` with a one-off container: `docker compose run --rm --no-deps --entrypoint python app -c 'import sqlite3; path="/data/restore-staging/REPLACE/restored.sqlite3"; rows=sqlite3.connect(path).execute("PRAGMA integrity_check").fetchall(); assert rows == [("ok",)], rows'`.
3. On the host, compare `stat -c '%d' /srv/meppp/data/meppp.sqlite3 STAGED_HOST_PATH`; both device numbers must match so the final rename is atomic.
4. Create a new timestamped quarantine directory under `/srv/meppp/data/backups/sqlite/pre-restore-files/`. It must be empty.
5. Move the live `meppp.sqlite3`, and any matching `meppp.sqlite3-wal` and `meppp.sqlite3-shm`, into that quarantine directory one path at a time. After every move, verify the source disappeared and destination exists. If any move fails, stop and move already-moved files back; never unlink either copy.
6. Prove `/srv/meppp/data/meppp.sqlite3` does not exist, then use host `mv STAGED_HOST_PATH /srv/meppp/data/meppp.sqlite3`. Because both paths were proven on the same filesystem, this final rename is atomic.
7. Re-run `PRAGMA integrity_check` against the installed path with the one-off container. Start the application only after it returns exactly `ok`.
8. Run health, login, homepage, admin Basic Auth, and staff smoke checks. Keep the quarantined files and old image until the rollback window closes.

If any post-cutover check fails, stop the app, move the failed restored database into a separate evidence path, move every quarantined original file back to its exact original name, restore the previous immutable image tag, validate integrity, and only then start. No recovery step uses `rm` on a database, WAL, or SHM file.

## Upgrade and rollback

For every upgrade:

1. Run the online backup command and a restore drill; retain their output.
2. Before upgrading from a release older than secure-media migration `0005`, run `docker compose exec -T app python manage.py shell -c 'from meppp.publishing.models import Attachment; print(Attachment.objects.count())'`. The result must be exactly `0`. Stop if it is not: old attachments were not guaranteed to be safely re-encoded, so migration deliberately preserves and blocks them instead of silently trusting or deleting them.
3. Record `docker compose images` and the current `MEPPP_IMAGE` value.
4. Build the new source as a new immutable tag and update `MEPPP_IMAGE` in `.env`.
5. Run `docker compose config --quiet`.
6. Start only the application service and wait for healthy status. Startup migrates the database, clamps any historical image configuration above the 4-image/5-MiB hard caps with a configuration revision, reconciles managed role permissions, and collects static assets before Gunicorn starts.
7. Test health, homepage, login, admin Basic Auth, and a staff moderation path before opening traffic.

Do not overwrite or delete the prior image during the release window. If code fails before a migration changes data, restore the prior `MEPPP_IMAGE` value and recreate the service. If a migration or application write changed data incompatibly, use the attended staged-recovery procedure above, restore the prior image tag, and then start. Never attempt schema rollback by copying a live SQLite file.

## Cloudflare and DNS cutover

The origin must already serve HTTPS with an unexpired hostname-matching public certificate or Cloudflare Origin CA certificate before enabling Full (strict). Otherwise Cloudflare can return 526. Test the origin through an explicit local host mapping, back up the old DNS values, and keep their rollback TTL/target recorded.

Only after origin TLS, container health, Nginx configuration, admin protection, backup, restore drill, and closed-registration smoke tests pass should the proxied apex and `www` records be changed. Verify the public path independently, then retain the former origin as a rollback target during the observation window.

## Observability and incident minimum

- Application logs go to standard output and are rotated by Docker (`10m` × `5` by default).
- Nginx keeps separate access and warning-level error logs for `meppp.com`.
- `/health/live` proves the process responds; `/health/ready` also proves a database query, a readable/writable media directory, and the configured free-space floor (256 MiB by default). The Nginx template restricts both to origin-local requests.
- A healthy probe is not proof that login, posting, moderation, backup, or restore works. Keep those checks in the release checklist.
- On failure, preserve the image tag, `.env` backup, Nginx backup, selected database manifest, command output, and the shortest rollback action. Do not repeatedly restart or resubmit changes without identifying the failed gate.
