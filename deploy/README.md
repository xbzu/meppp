# MEPPP production deployment packet

This packet targets one MEPPP container behind the existing aaPanel Nginx on a shared host. It does not modify DNS, Cloudflare, aaPanel, or the server by itself.

## Required gates

1. Take and verify a backup of every existing site configuration that may be touched.
2. Confirm `18080/tcp` is unused and the Docker subnet does not overlap any existing Docker, VPN, or host route.
3. Install a valid origin certificate for `meppp.com` and `www.meppp.com`.
4. Test the origin by explicit host mapping before changing DNS.
5. Keep registration closed for the first production smoke test.
6. Change Cloudflare DNS only after origin TLS, application health, backup, and rollback checks pass.

Container startup automatically applies migrations, reconciles the code-defined `运营` and `审核` permission groups, and collects static files. It does not create an owner, assign staff, issue invitations, or change registration mode.

## aaPanel layout

Recommended host paths:

```text
/opt/meppp/                        repository checkout and Compose project
/srv/meppp/data/                   SQLite, private media, generated static files, backup manifests
/www/backup/meppp.com/             independently mounted backup copy
/www/wwwroot/meppp.com/            ACME challenge/aaPanel placeholder only
```

The data directory must be local storage and writable only by container UID/GID `10001:10001`:

```bash
install -d -o 10001 -g 10001 -m 700 /srv/meppp/data
install -d -o root -g root -m 700 /www/backup/meppp.com
```

Copy `.env.example` to `.env`, replace the secret, and use the bind mount:

```dotenv
MEPPP_DATA_MOUNT=/srv/meppp/data
MEPPP_TMPFS_SIZE=192m
```

Do not lower the temporary filesystem below 192 MiB while video uploads are
enabled; the two Gunicorn threads may each hold a bounded upload, safe remux,
and generated poster at the same time.

Before the first start, validate the fixed subnet (`172.30.89.0/28`) against every existing Docker network and host/VPN route. If it collides, change `MEPPP_NETWORK_SUBNET`, `MEPPP_NETWORK_GATEWAY`, `MEPPP_CONTAINER_IP`, and `MEPPP_TRUSTED_PROXY_IPS` together.

## Docker Compose compatibility gate

The target currently has Docker Engine but no Compose/buildx CLI plugins. Before deployment, install the pinned Compose `v2.40.3` plugin with its official release checksum. The default system-wide plugin path remains visible to hardened systemd jobs that intentionally hide `/root`:

```bash
cd /opt/meppp && sh ./deploy/install_compose_plugin.sh
docker compose version
docker compose config --quiet
```

The installer supports the target `x86_64` plus `aarch64`, refuses to overwrite an existing plugin, and installs only after SHA-256 verification. Checksums come from Docker Compose's official `v2.40.3` GitHub release. If any version, checksum, or Compose config check fails, stop before building.

Build without assuming a buildx plugin, then prove the immutable image exists:

```bash
cd /opt/meppp
RELEASE_TAG=v0.1.0-rc.14
test -z "$(docker image ls --quiet meppp:${RELEASE_TAG})"
DOCKER_BUILDKIT=1 docker build --pull --tag "meppp:${RELEASE_TAG}" .
docker image inspect "meppp:${RELEASE_TAG}" >/dev/null
docker compose up --detach --no-build --wait app
```

If the Docker Engine cannot complete the BuildKit build, stop at the build gate; do not fall back by changing the Dockerfile or starting an unverified image on the shared host.

## Nginx and Cloudflare source IPs

The target aaPanel host already defines Cloudflare `set_real_ip_from` networks and `real_ip_header CF-Connecting-IP` globally, and those networks were checked against Cloudflare's official IPv4/IPv6 lists. Do not add a second include to the MEPPP vhost. Reconfirm the global state before installation:

```bash
nginx -T 2>&1 | grep -E 'set_real_ip_from|real_ip_header|real_ip_recursive'
```

`deploy/update_cloudflare_real_ip.py` and the example include are for a different host where no global real-IP configuration exists. Generate and review that include only after proving the global configuration is absent; never overwrite a shared global Nginx file without its own backup and validation gate.

Create a second credential boundary in front of Django admin. Do not reuse the Django password:

```bash
install -d -o root -g www -m 750 /www/server/nginx/conf/meppp-auth
admin_hash=$(openssl passwd -6)
printf 'meppp-operator:%s\n' "$admin_hash" \
  > /www/server/nginx/conf/meppp-auth/.htpasswd
unset admin_hash
chown root:www /www/server/nginx/conf/meppp-auth/.htpasswd
chmod 640 /www/server/nginx/conf/meppp-auth/.htpasswd
```

`openssl passwd -6` prompts without placing the password in shell history and uses the target's verified SHA-512 `crypt` support. The target host does not have `htpasswd`, so the deployment does not require adding a system package. Do not place this file under `/www/server/panel/vhost/nginx`: that parent is intentionally non-traversable by the Nginx worker, and its permissions must not be loosened.

Install `deploy/nginx/meppp.com.conf.example` through aaPanel's site configuration editor. Back up the prior vhost file first. Run `nginx -t` before any reload. The template rate-limits public traffic, applies tighter login/registration/admin limits, and requires Basic Auth for `/admin`.

The Nginx template converts Cloudflare's authenticated client address into `X-Real-IP`. Django trusts only the fixed Docker bridge gateway, never arbitrary public `X-Real-IP` or `X-Forwarded-Proto` headers.

## Full (strict) sequence

Cloudflare Full (strict) requires the origin on port 443 to present an unexpired certificate from a public CA or Cloudflare Origin CA whose hostname matches the request. Complete the origin certificate and local Nginx test first; changing the Cloudflare mode early can produce a 526 response.

1. Install the matching origin certificate in aaPanel.
2. Test Nginx configuration and reload only after it passes.
3. Test the origin with an explicit host mapping before changing public DNS.
4. In Cloudflare, set SSL/TLS encryption to **Full (strict)**.
5. Set proxied DNS records only after the application canary passes: apex `A -> 38.22.89.60`; `www` may be a proxied CNAME to the apex.
6. Confirm the public certificate, redirect, login, admin Basic Auth, and source IP logging.

Official references:

- <https://developers.cloudflare.com/ssl/origin-configuration/ssl-modes/full-strict/>
- <https://developers.cloudflare.com/ssl/origin-configuration/origin-ca/>
- <https://www.cloudflare.com/ips/>

No DNS or Cloudflare setting is automated by this repository.

## Daily backup task

Mount an independent backup disk, then create an aaPanel daily shell task from `deploy/cron/meppp-backup.sh`. The task fails if source and destination have the same filesystem device or media contains a symbolic link, performs an online SQLite backup, runs a fresh-path restore drill, verifies all attachment files against the snapshot, incrementally copies immutable media plus the database, and verifies both SHA-256 manifests. Example:

```bash
cd /opt/meppp && \
MEPPP_APP_DIR=/opt/meppp \
MEPPP_HOST_DATA_DIR=/srv/meppp/data \
MEPPP_OFFSITE_DIR=/www/backup/meppp.com \
sh ./deploy/cron/meppp-backup.sh
```

For the initial `meppp.com` deployment, the independent copy is pulled to the operator Mac instead of relying on `/www/backup` on the same server disk. Install the source-side preparation timer and run it once before enabling the Mac job:

```bash
install -m 0644 deploy/systemd/meppp-backup-prepare.service /etc/systemd/system/
install -m 0644 deploy/systemd/meppp-backup-prepare.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now meppp-backup-prepare.timer
systemctl start meppp-backup-prepare.service
```

Give the Mac a dedicated read-only SSH key whose server-side `authorized_keys` entry is restricted to `/srv/meppp/data` with `rrsync`, then install the machine-specific copy of `deploy/macos/com.meppp.offsite-backup.plist.example`. Create the destination directory on the external volume first, and fill in the external volume UUID and paths before loading it. `deploy/macos/meppp-offsite-pull.sh` refuses a missing directory, the wrong or missing volume, or a destination outside that volume; it never uses `--delete`, verifies the copied database and media manifests, and marks success only while the newest database is under 26 hours old.

macOS may deny an unattended LaunchAgent access to a removable volume even when the same script succeeds interactively. Treat `Operation not permitted` as a failed automation gate; do not claim the LaunchAgent is working from the manual run. Without changing macOS privacy settings, install `deploy/remote/meppp-remote-pull.sh` on an independently hosted Linux server instead. Its systemd service passes the source key through `LoadCredential`, pulls through the same source-side read-only `rrsync` restriction, verifies database/media checksums and freshness, never deletes old copies, and writes `LAST_SUCCESS` only after every check passes. Enable the matching `meppp-remote-monitor.timer` only after the first pull succeeds; it checks freshness and manifests hourly and leaves a visible failure marker for the control plane.

Debian 11's older `rrsync` does not recognize the harmless `--dirs` spelling emitted by current macOS rsync. Install `deploy/rrsync/meppp-rrsync-compat.sh` as `/usr/local/sbin/meppp-rrsync` and use that exact path in the forced command when this compatibility error is proven. The wrapper maps only `--dirs` to the equivalent `-d`; the distribution `rrsync` still enforces every read-only, path, and option check.

The Nginx template intentionally has no public `/media/` alias. Images, videos and video posters pass through state-aware application routes so pending, hidden, withdrawn, or inactive-author media cannot be fetched directly. Do not schedule the backup task until the destination mount and its monitoring are proven. See `docs/OPERATIONS.md` for retention, media reconciliation, restore drills, and attended recovery.

Install the due-source refresh job after the release smoke test:

```bash
install -m 0644 deploy/systemd/meppp-external-refresh.service /etc/systemd/system/
install -m 0644 deploy/systemd/meppp-external-refresh.timer /etc/systemd/system/
systemctl daemon-reload
systemctl start meppp-external-refresh.service
systemctl enable --now meppp-external-refresh.timer
```

The job handles only X/YouTube records already accepted by the application and calls fixed official oEmbed endpoints. It is not a generic URL fetcher and does not download third-party media.
