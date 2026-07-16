# Public UI specification

## Direction: PaoPao-style blue stream

MEPPP's public interface is an independent Django adaptation of the interaction hierarchy reviewed in PaoPao CE commit `2f55b4b5d7f204d1b939b2ba41ccfde4b8e071cd`. PaoPao CE is MIT-licensed and Copyright 2022 ROC. The implementation keeps the useful community structure while replacing its brand, content, assets, copy, routes, data model, components, and implementation.

go-postery commit `1bcebf66c70bec6d12f0c2be812984cd69b5de57` (MIT, Copyright 2025 Zhi Lei Yang) is used only as a secondary blue-colour and contrast check. Its source components, layout implementation, brand, logo, icons, screenshots, and demonstration content are not copied.

- A quiet white canvas, dark text, restrained grey borders, and sky blue `#0284c7` for primary actions and active states.
- Local Chinese sans-serif stacks only; no network font or frontend framework dependency.
- A PaoPao-like three-column desktop hierarchy: compact navigation, one readable stream, and a small discovery rail.
- The stream begins with a compact title and inline composer or a clear sign-in/register prompt, then uses border-separated entries instead of floating marketing cards.
- Search and topics stay compact in the right rail and do not compete with the stream.
- Registration remains directly discoverable for signed-out visitors; the register page explains whether registration is open, invite-only, or temporarily closed.
- Mobile keeps the stream title at the top and opens the same navigation in a left drawer; there is no invented bottom navigation.
- `MEPPP` is a configurable working name, not a permanently embedded product brand.

## Reference-to-MEPPP mapping

| Reviewed public pattern | Independent MEPPP implementation |
| --- | --- |
| Compact left navigation | `paopao-shell` navigation landmark using MEPPP routes, labels, and inline icons |
| Narrow central timeline | `stream-panel` rendered by Django from MEPPP entries and moderation states |
| Inline publishing prompt | MEPPP composer or login/register call to action governed by registration policy |
| Compact topic/search rail | MEPPP topic counts and search form using MEPPP query parameters |
| Responsive rail collapse | Native CSS breakpoints with MEPPP's mobile left drawer and safe-area contract |

## Pages

| Page | Public behaviour |
| --- | --- |
| Home | Latest/following stream, text search, topics, registration or writing prompt |
| Entry | Full text, topics, like state, flat comments, bound report links |
| Member | Public name, bio, counts, public entries, follow state |
| Login/register | Public member authentication, visible registration entry, fail-closed registration modes |
| My community | Recipient-only content states, profile/password settings, and author withdrawal |
| Composer | Text, up to four images with optional alternative text, and up to three existing topics; pending state in pre-moderation mode |
| Notifications | Recipient-only follow, like, comment, moderation outcome/reason, and system notices |
| Report | Private reason/details form bound to a visible user, entry, or comment |
| Admin | Branded operations dashboard, one-time invitations, dedicated content queues, configuration, and report workflows |

## Responsive contract

- Wide desktop: 200px compact navigation, 620px stream, 240px discovery rail, and small inter-column gaps.
- `822–1100px`: the left navigation collapses to icons while the 620px stream and 240px discovery rail remain visible.
- `<= 821px`: both rails are hidden; the stream fills the available width and the title-bar menu opens a 312px left drawer.
- Minimum interactive height is 44px where layout permits.
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

The reference review establishes information hierarchy and interaction expectations, not a source-code import. MEPPP's templates and native CSS are written for its existing Django product stories and security contracts. It does not import the references' DOM, CSS, JavaScript/TypeScript/Vue components, text, icons, assets, routes, database structures, or demonstration data. Full attribution and scope are in [`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md).
