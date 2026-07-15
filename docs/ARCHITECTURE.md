# Architecture

MEPPP is a modular Django monolith: one codebase, one application process, and SQLite by default.

## Module boundaries

| Module | Owns |
| --- | --- |
| `accounts` | Authentication identity and public profile |
| `publishing` | Entries, comments, topics, and attachments |
| `social` | One-way follows and entry likes |
| `notifications` | Recipient-owned system notifications, separate from future private messages |
| `moderation` | Reports, state transitions, and immutable decisions |
| `configuration` | Typed site settings and version history |
| `audit` | Application-enforced append-only administrative and security events |

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

SQLite uses WAL mode, foreign keys, a 20-second busy timeout, and short `IMMEDIATE` write transactions. The application stays at one process while SQLite is active.

Move to PostgreSQL before running multiple application replicas or after sustained lock contention. The ORM models, service boundaries, public identifiers, and migration history remain; the deployment configuration and load-sensitive queries change.

## Interface path

Django Admin is the first trusted-operator interface. Custom moderation and configuration workflows will use Django templates and a vendored, pinned HTMX asset. There is no separate Node build or SPA state layer.
