# X / YouTube attributed source sharing

MEPPP implements external sharing as an attributed reference card: a member pastes a public X Post or YouTube video URL, optionally adds original context, reviews the normal publishing form, and confirms publication. It is deliberately not a third-party media downloader or automatic cross-platform repost bot.

## Implemented contract

- One optional `ExternalReference` is bound one-to-one to an entry and follows the entry's moderation and withdrawal lifecycle.
- Only exact X and YouTube host/path shapes are accepted. MEPPP extracts the public ID locally and rebuilds a canonical HTTPS URL; userinfo, non-443 ports, fragments, malformed IDs and every other host are rejected.
- The server requests metadata only from fixed `publish.x.com` and `www.youtube.com` HTTPS endpoints with a four-second timeout, a 128 KiB response cap and no redirects. A user-supplied host is never requested.
- X oEmbed HTML is parsed into bounded plain text; raw provider HTML and scripts are not stored or rendered.
- YouTube playback uses `youtube-nocookie.com` only after official metadata verification. X cards remain first-party HTML with an attributed source link.
- Every outbound source link uses `rel="ugc nofollow noreferrer noopener"`; attribution and the canonical source URL stay visible.
- Due metadata is refreshed in bounded batches by `refresh_external_references` and the optional fifteen-minute systemd timer. Unavailable content stops showing its preview.
- `/write/?url=<supported-url>` pre-fills a normal draft. Authentication and the final publish button remain mandatory.

## Product boundaries

- No automatic download, storage or re-upload of X/YouTube media.
- No background publishing without the member's confirmation.
- No generic server-side URL fetcher, arbitrary redirect following or remote thumbnail hotlinking.
- No X account cookie, Susan's private login, or OAuth token is required.
- Self-uploaded local media is a separate path and the member is told that upload means they own or are authorised to use it.

## Later, only if justified

A browser share extension or PWA Share Target may open the existing prefilled draft route. OAuth connectors are considered only after the provider's then-current official API supports a user-selected collection and the implementation has PKCE/state, minimal read-only scope, encrypted token storage, revocation, audit and key-rotation boundaries.

Status: the source-reference contract was implemented in `v0.1.0-rc.6`; `v0.1.0-rc.8` adds distinct front-end entry points, provider recognition, and owner-controlled X/YouTube publishing switches. Full third-party media copying remains deliberately out of scope.
