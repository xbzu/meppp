# Product scope

MEPPP is a lightweight community product. Its first complete milestone is a small, coherent moderation loop rather than a wide feature matrix. The current change implements the data, security, moderation, configuration, operations, and text-first public member interface.

## Current UI milestone

- Implemented: public chronological and following feeds, text search, topic filtering, member pages, login/logout, text publishing, flat comments, likes, follows, notifications, and bound-target reports.
- Implemented: open, closed, and single-use invitation registration; invitations can expire, be revoked, and be bound to one normalized email address.
- Implemented: a private member desk for content state, profile and password changes, and author withdrawal without destroying moderation evidence.
- Implemented: post-moderation publishing plus dedicated pending-entry and pending-comment queues for pre-moderation. Every decision requires a reason, is append-only, and notifies the author.
- Implemented: a permission-aware operations dashboard and idempotent code-defined `运营` and `审核` groups. The owner remains a Django superuser rather than a second role system.
- Implemented: responsive desktop/mobile templates, keyboard focus, security headers, CSRF, same-origin redirects, form idempotency, and account/IP rate limits.
- Deliberately closed: member avatar and attachment uploads. Extension checks are not treated as a safe image pipeline.
- Still planned for the target MVP: decoded/re-encoded image uploads with dimensions, size limits, alternative text, transactional cleanup, and dedicated abuse tests.

## MVP loop

1. Visitors can browse a public chronological feed, member pages, topics, and search results.
2. An owner can issue a one-time invitation; an invited member can register, sign in, publish text, follow, like, and leave flat comments.
3. Members receive database-backed notifications for follows, likes, comments, moderation outcomes and reasons, and system events.
4. Members can report a member, entry, or comment.
5. Staff can review pending content, triage reports, hide content, suspend accounts, and restore prior report decisions through dedicated workflows.
6. Members can inspect their own pending, public, hidden, and withdrawn records and withdraw their pending or public content.
7. Every moderation, invitation, member-security, and configuration change creates an application-enforced append-only audit event.

## Core records

- User, profile, and invitation
- Entry, comment, topic, attachment, and content review decision
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
