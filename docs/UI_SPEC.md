# Public UI specification

## Direction: Blue Commons

MEPPP uses an independent blue social-community interface built for quick discovery, lightweight publishing, and clear conversation. Its information architecture is informed by established three-column community patterns, including a high-level review of PaoPao CE, while every template, style, icon, word, and asset is independently implemented for MEPPP.

- Cobalt blue, airy blue-grey, white surfaces, dark navy text, and restrained violet/red state accents.
- Local Chinese sans-serif stacks only; no network fonts or frontend framework dependency.
- A compact global header plus a three-column desktop community layout: navigation, feed, and discovery.
- The feed prioritises a visible composer, chronological entries, media, topics, and clear interaction actions.
- Registration remains discoverable in every mode; the join page explains whether registration is open, invite-only, or temporarily closed.
- Mobile uses a compact brand header and non-obscuring bottom navigation with safe-area spacing.
- No copied logo, screenshots, source components, design tokens, sample content, or distinctive interface copy.
- `MEPPP` is a configurable working name, not a permanently embedded product brand.

## Pages

| Page | Public behaviour |
| --- | --- |
| Home | Latest/following feed, text search, topics, registration or writing prompt |
| Entry | Full text, topics, like state, flat comments, bound report links |
| Member | Public name, bio, counts, public entries, follow state |
| Login/register | Public member authentication, fail-closed registration modes |
| My community | Recipient-only content states, profile/password settings, and author withdrawal |
| Composer | Text, up to four images with optional alternative text, and up to three existing topics; pending state in pre-moderation mode |
| Notifications | Recipient-only follow, like, comment, moderation outcome/reason, and system notices |
| Report | Private reason/details form bound to a visible user, entry, or comment |
| Admin | Branded operations dashboard, one-time invitations, dedicated content queues, configuration, and report workflows |

## Responsive contract

- `>= 1121px`: 220px navigation, up to 680px feed, and 280px discovery rail.
- `761–1120px`: centred feed; both contextual rails are hidden.
- `<= 760px`: one column, 12px page edges, compact top actions, and safe-area bottom navigation.
- Minimum interactive height is 44px where layout permits.
- Fixed mobile navigation reserves body space and never covers form actions or feed content.
- Media layout is one-up, two-up, three with a spanning final image, or a two-by-two grid.

## Accessibility and safety

- `lang="zh-Hans"`, semantic landmarks, a keyboard skip link, visible labels, and strong focus.
- User text remains auto-escaped; entry, comment, and bio fields are never rendered with `safe`.
- Public URLs use UUIDs and inactive or non-public targets resolve as not found.
- Public pages ship a same-origin CSP, restrictive permissions policy, and referrer policy.
- Authenticated and authentication responses are private/no-store and vary on cookies.
- Entry images accept JPG, PNG and WebP input, provide accessible ordered previews, and are served only after server decoding and re-encoding. Empty alternative text is preserved for decorative images rather than replaced with noisy fallback text.
- Avatar upload remains closed until replacement, retention and moderation behaviour are separately defined.

## Independent implementation boundary

Reference products may be reviewed at a high level to identify general information architecture and usability patterns. MEPPP does not import their DOM, CSS, source components, text, icons, assets, design tokens, route identifiers, or data structures. The current Blue Commons implementation was written directly in Django templates and native CSS against MEPPP's existing product stories and security contracts.
