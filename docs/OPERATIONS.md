# Operations boundary

This repository currently validates source, migrations, tests, production settings, and a disposable container in GitHub Actions. It does not deploy to a local machine or server.

## Minimal future runtime

- One container
- One Gunicorn worker with four threads
- One `/data` persistent volume for SQLite, static output, and future media
- No Redis, queue worker, external search service, or database service

The image runs migrations and collects static assets before starting. It refuses to start in production without a secret key and allowed hosts. No default administrator password exists; an owner is created interactively with Django's `createsuperuser` command.

Generate a secret before any future runtime setup:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

The placeholder in `.env.example` is intentionally rejected at startup.

Production defaults require HTTPS. The Compose port binds to `127.0.0.1` so a trusted local reverse proxy can terminate TLS. Set `MEPPP_TRUST_PROXY=1` only when that proxy overwrites both `X-Forwarded-Proto` and `X-Real-IP`, then list the proxy's directly observed IP or CIDR in `MEPPP_TRUSTED_PROXY_IPS`. The application accepts a single canonical address in `X-Real-IP` only from those trusted networks; forwarded headers from public clients are ignored. Plain HTTP is reserved for disposable tests with an explicit `MEPPP_SECURE=0` override.

## SQLite limits

- The database file must stay on a local persistent filesystem, not NFS or a synchronized drive.
- Only one application container may write to it.
- Backups must use SQLite's online backup mechanism and include uploaded media.
- Multiple replicas, sustained write contention, or queue-heavy workloads trigger migration to PostgreSQL.

## Media boundary

The data model reserves validated image attachments. Public media delivery is intentionally not enabled in this foundation change; it will be added with the upload workflow so access control, MIME validation, and response headers are tested together.
