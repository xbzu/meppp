# External share roadmap

MEPPP will support sharing public X and YouTube links as attributed community cards. The product goal is “bring a link into a draft, add your own context, then confirm publication” rather than automatic copying or cross-platform reposting.

## Phase 1 — safe link cards

- Add one optional `EntryLink` to an entry with `provider`, `canonical_url`, `external_id`, and metadata status.
- Accept only recognised X status and YouTube watch/shorts/youtu.be path shapes, extract the public content ID, and rebuild the canonical HTTPS URL locally.
- Reject userinfo, non-443 ports, fragments, overlong URLs, malformed IDs, and every host or path outside the explicit allowlist.
- Do not fetch a remote page during publication; the first card shows provider, canonical URL, and external ID.
- Keep the existing entry moderation, nonce, and withdrawal boundaries around the card; add a non-sensitive audit event only if the link feature defines and tests one explicitly.
- Render external links with `rel="ugc nofollow noreferrer noopener"`.

Acceptance: a member can paste a supported URL, add original commentary, preview the attributed card, confirm publication, and later withdraw the entry. Unsupported URLs fail with a clear form error.

## Phase 2 — official metadata

- Resolve titles, creator names, thumbnails, and publish times only through official APIs or oEmbed endpoints.
- Process metadata outside the publication request with strict host allowlists, timeouts, redirect limits, and response-size limits. Revalidate the host and resolved IP after every redirect, and reject private, loopback, link-local, reserved, and otherwise non-public address ranges.
- Store structured fields only; do not retain raw HTML, comments, full post text, or video files.
- Use a MEPPP-owned card rather than a third-party iframe unless the CSP and privacy contract are reviewed separately. Do not hotlink or cache remote thumbnails until the relevant platform terms, rights, and privacy handling have passed review.

## Phase 3 — one-click entry points

- A bookmark action or browser extension first recognises a supported platform, extracts its public ID, removes fragments and tracking parameters, and then opens `/write/?url=<canonical-public-url>`.
- A later PWA Share Target provides the same prefilled draft flow on mobile.
- The member must still be logged in, review the source card, add context, and press the normal publish button.

## Phase 4 — optional account connectors

OAuth is only considered after the provider's current official API is verified to expose a user-selected collection. The flow requires `state`, PKCE where supported, exact callback allowlists, the smallest read-only scope, encrypted token storage, log redaction, key rotation, revocation, and a separate configuration/audit boundary. Connector failure must never block ordinary community publishing.

## Product boundaries

- No automatic download or re-upload of X/YouTube media.
- No background publishing to MEPPP without member confirmation.
- No generic server-side URL fetcher or arbitrary redirect following.
- Attribution and the canonical source link stay visible on every external card.

Status: planned. This UI release exposes only a labelled roadmap note; it does not present a non-functional import button as a completed feature.
