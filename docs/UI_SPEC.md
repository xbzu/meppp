# Public UI specification

## Direction: Quiet Ledger

MEPPP uses an independent “quiet community ledger” direction. It is built for reading, deliberate writing, and transparent chronological conversation rather than attention-maximising engagement.

- Warm paper background, dark ink, forest green, and a restrained ember accent.
- Editorial display typography paired with a local Chinese sans-serif stack; no network fonts.
- Horizontal masthead and a two-column desktop reading layout.
- Entries are separated by rules and whitespace rather than floating social-media cards.
- No gradients, glass effects, mascot, chat bubbles, infinite scroll, or copied interface assets.
- `MEPPP` is a configurable working name, not a permanently embedded product brand.

## Pages

| Page | Public behaviour |
| --- | --- |
| Home | Latest/following feed, text search, topics, registration or writing prompt |
| Entry | Full text, topics, like state, flat comments, bound report links |
| Member | Public name, bio, counts, public entries, follow state |
| Login/register | Public member authentication, fail-closed registration modes |
| Composer | Text and up to three existing topics; pending state in pre-moderation mode |
| Notifications | Recipient-only follow, like, comment, moderation, and system notices |
| Report | Private reason/details form bound to a visible user, entry, or comment |
| Admin | Branded Django Admin for trusted configuration and moderation work |

## Responsive contract

- `>= 960px`: reading column plus a 270px context rail.
- `641–959px`: centred reading column; side sections become a second row.
- `<= 640px`: one column, 16px page edges, compact horizontal navigation.
- Minimum interactive height is 44px where layout permits.
- No fixed bottom navigation and no content-obscuring overlays.
- Media layout is one-up, two-up, three with a spanning final image, or a two-by-two grid.

## Accessibility and safety

- `lang="zh-Hans"`, semantic landmarks, a keyboard skip link, visible labels, and strong focus.
- User text remains auto-escaped; entry, comment, and bio fields are never rendered with `safe`.
- Public URLs use UUIDs and inactive or non-public targets resolve as not found.
- Public pages ship a same-origin CSP, restrictive permissions policy, and referrer policy.
- Authenticated and authentication responses are private/no-store and vary on cookies.
- Member uploads remain closed until image decoding, re-encoding, size/pixel limits, EXIF handling, and transactional cleanup are complete.

## Clean-room boundary

The UI is derived only from MEPPP’s own product stories and this specification. It does not use the reference project’s screenshots, DOM, CSS, copy, icons, assets, route layout, or design tokens.
