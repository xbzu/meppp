# Architecture

MEPPP is a modular Django monolith: one codebase, one application worker, and SQLite by default.

## Module boundaries

| Module | Owns |
| --- | --- |
| `accounts` | Authentication identity, public profile, and one-time invitations |
| `publishing` | Entries, comments, topics, safe image processing/storage, attachments, and append-only content review decisions |
| `social` | One-way follows and entry likes |
| `notifications` | Recipient-owned system notifications, separate from future private messages |
| `moderation` | Reports, state transitions, and immutable decisions |
| `configuration` | Typed site settings and version history |
| `audit` | Application-enforced append-only administrative and security events |
| `operations` | Permission manifests, role reconciliation, and the live operator dashboard |
| `web` | Public presentation, forms, request throttling, CSP, and browser-facing orchestration |

## Rules

- Views and Admin handle requests; state changes belong in explicit service functions.
- Complex reads belong in query selectors when they appear; templates do not build queries.
- Cross-module writes use public services and a single database transaction.
- Signals are not used for core business behaviour.
- Public URLs use immutable UUIDs; internal foreign keys retain compact database IDs.
- Runtime secrets remain in environment variables and never enter site configuration.
- Core relations use Django ORM features shared by SQLite and PostgreSQL.

Append-only models reject instance and QuerySet updates/deletes, and their actor links are protected. This guards application and Admin paths; it is not cryptographic immutability and does not defend against a database owner issuing raw SQL. External tamper-evident storage can be added if that threat model becomes relevant.

## Database path

SQLite uses WAL mode, foreign keys, a 20-second busy timeout, and short `IMMEDIATE` write transactions. The application stays at one worker while SQLite is active.

Move to PostgreSQL before running multiple application replicas or after sustained lock contention. The ORM models, service boundaries, public identifiers, and migration history remain; the deployment configuration and load-sensitive queries change.

## Interface path

Django Admin remains the trusted-operator interface. The public member experience uses server-rendered Django templates, ordinary links and POST forms, and a small local JavaScript file for character counters. Core flows work without JavaScript.

HTMX may later enhance likes, follows, comments, and pagination, but it is not required for correctness and is not currently shipped. If added, it will be vendored and pinned. There is no separate Node build or SPA state layer.

Public writes call explicit domain services. Registration, publishing, comments, likes, follows, withdrawals, and reports never accept actor or lifecycle fields from the browser. Registration mode is rechecked under a configuration lock in the account service; invitation claims use a digest lookup plus a conditional update inside the same transaction as account creation, so a replay cannot leave a second account behind. Entry and comment form tokens are claimed through a database uniqueness constraint in the same transaction as the write, so parallel requests cannot both succeed. Content reviews lock and conditionally transition only pending records before appending a single immutable decision.

Images are decoded with a fixed JPEG/PNG/WebP allowlist, checked against byte, edge and pixel limits, orientation-corrected, stripped of supplied metadata and re-encoded as a new single-frame WebP. Original names and bytes never enter permanent storage. Avatar input uses the same decoder, is centre-cropped to a bounded square revision, and is served only through an active-member route that rechecks its canonical path, bytes and dimensions. Replacement and removal retain old immutable revisions until the backup-safe reconciliation window. The filesystem backend writes a hidden same-directory file, flushes it, and exposes the complete inode atomically. A database failure removes every file from that submission; the default-dry-run reconciliation command handles old crash-window orphans. Media URLs resolve an attachment UUID through Django and recheck the entry/author state, so the raw media tree is never public.

Videos use a separate one-to-one asset: ffprobe accepts only the documented MP4/WebM codec pairs, FFmpeg remuxes without re-encoding while removing metadata and generates a bounded WebP poster. State-aware application routes provide same-origin poster delivery and bounded HTTP Range playback. External X/YouTube references store only canonical IDs and structured attribution obtained from fixed official oEmbed endpoints; they never share the local-media storage path.

Rate limits use the single-process Django cache with HMAC-obscured client and account keys; the short-lived, display-once recovery-code notice uses a separate in-process cache. Both match the one-process SQLite deployment boundary. Direct requests use the socket address. A configured trusted proxy must be in an explicit IP/CIDR allowlist and overwrite `X-Real-IP` with one canonical address; untrusted forwarded headers are ignored. A move to multiple processes requires shared storage for both caches or an equivalent edge limiter and secure recovery-notice handoff before scaling the application.
