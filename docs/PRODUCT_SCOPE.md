# Product scope

MEPPP is a lightweight community product. Its first complete milestone is a small, coherent moderation loop rather than a wide feature matrix. The current change implements the data, security, moderation, configuration, operations, and text-first public member interface.

## Current UI milestone

- Implemented: public chronological and following feeds, text search, topic filtering, member pages, registration modes, login/logout, text publishing, flat comments, likes, follows, notifications, and bound-target reports.
- Implemented: post-moderation publishing and a real pending state for pre-moderation.
- Implemented: responsive desktop/mobile templates, keyboard focus, security headers, CSRF, same-origin redirects, form idempotency, and account/IP rate limits.
- Deliberately closed: member avatar and attachment uploads. Extension checks are not treated as a safe image pipeline.
- Still planned for the target MVP: decoded/re-encoded image uploads with dimensions, size limits, alternative text, transactional cleanup, and dedicated abuse tests.

## MVP loop

1. Visitors can browse a public chronological feed, member pages, topics, and search results.
2. Members can register, sign in, publish text, follow, like, and leave flat comments. Validated image publishing is the next milestone.
3. Members receive database-backed notifications for follows, likes, comments, moderation, and system events.
4. Members can report a member, entry, or comment.
5. Staff can triage reports, hide content, suspend accounts, and restore prior decisions.
6. Every moderation and configuration change creates an application-enforced append-only audit event.

## Core records

- User and profile
- Entry, comment, topic, and attachment
- Follow and entry like
- Notification
- Report and moderation decision
- Site configuration and configuration revision
- Audit event

## Deliberately deferred

- Private messaging, real-time chat, WebSockets, and push notifications
- Video, audio, arbitrary files, transcoding, and CDN integration
- Wallets, payments, paid attachments, and revenue sharing
- Redis, job queues, external search engines, and event buses
- Multiple databases and multiple object-storage providers
- Microservices, gRPC, desktop clients, and mobile clients
- Complex visibility modes, nested comments, trend scoring, and recommendation engines

Deferred capabilities are added only after a measured need, not as inactive infrastructure.
