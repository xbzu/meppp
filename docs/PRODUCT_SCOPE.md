# Product scope

MEPPP is a lightweight community product. Its first complete milestone is a small, coherent moderation loop rather than a wide feature matrix. This document defines the target MVP; the foundation change implements its data, security, moderation, configuration, and operations boundaries, not the public feed and member interface yet.

## MVP loop

1. Visitors can browse a public chronological feed, member pages, topics, and search results.
2. Members can register, sign in, publish text with up to four images, follow, like, and leave flat comments.
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
