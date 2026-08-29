# Clipboard — Named Shared Clipboard Rooms

**App:** `misc_helper` · **Module:** `Clipboard` · **Status:** approved — build in progress
**Author:** Rahul Agrawal · **Date:** 2026-08-29

---

## 1. Problem

Moving text and images between my own devices — and to a friend — is painful.
Third-party clipboard sites are untrusted, and sharing a link every time is cumbersome.

## 2. Solution in one line

Memorable, named, public clipboard rooms at `/clipboard/<room-name>` that anyone can
open in a browser with nothing installed, paste text or images into, copy back out,
see update live, and that delete themselves after a configurable validity window.

**The meeting-room model.** There is no "create" step and no share link to send.
You say *"go to /clipboard/standup"* out loud, the other person types it, and you are
both in the same room. The room is created lazily on first visit.

## 3. Prior art — what we borrow and what we reject

| Tool | Model | Why not / what we take |
|---|---|---|
| [CloudPaste](https://cloudpaste.io/), [ClipShare](https://clipshare.cc/en), [OnlinClipboard](https://onlinclipboard.com/) | Hosted, random 6-digit code | Untrusted third party; a code cannot be said out loud and remembered. **Take:** QR-to-open-on-phone, expiry as a first-class idea. |
| [Ghostboard](https://alternativeto.net/software/ghostboard-text-sharing) | Self-hosted, WebSocket, single text buffer | Closest architecture. **Take:** WebSocket push over polling. **Reject:** text-only, single overwritten buffer. |
| [ClipCascade](https://www.xda-developers.com/self-hosted-clipboard-manager-syncs-across-devices-without-cloud/), [UniClipboard](https://github.com/uniclipboard/uniclipboard) | OS clipboard sync via installed desktop agents | Different product. Needs software on every device, gives no URL to hand a friend. **Reject wholesale.** |
| [Lynavo Paste](https://dev.to/lynavo/lynavo-paste-open-source-self-hosted-cross-device-clipboard-sync-tool-2n8) | Browser-based, self-hostable | Nearest overall. Architecture undocumented publicly. |

**The gap we fill:** memorable named rooms + browser-only + images + auto-expiry + own server.
No existing tool combines all five.

## 4. Scope

**In (v1):** named public rooms · text items · image items · live sync · copy to clipboard ·
delete item · auto-expiry · QR code · settings singleton.

**Out (v1), deliberately:** passcodes, private rooms, arbitrary file types, edit-in-place,
markdown rendering, per-item expiry, room listing/discovery.

Passcode and private rooms are the intended v2. The v1 doctype carries **no** `passcode_hash`
column — dead columns are worse than a later migration. Frappe's own login is the likely v2
gate, since the page already runs inside a Frappe session.

## 5. Security model — read this before approving

Rooms are **public and guessable by design**. Knowing the name grants full read and write.
That is the feature; it is what makes "go to /clipboard/standup" work.

Consequences, accepted:
- A generic name (`test`, `notes`, `temp`) **will** collide with a stranger eventually.
  Mitigation is convention, not code: use `rahul-standup`. The UI shows a one-line warning
  on rooms whose name is under 6 characters or on a small built-in common-word list.
- **Never paste credentials or anything sensitive into a short generic room.** Stated in the UI.
- Image files are stored **public with a random-hashed filename** (see §7.4). An image URL
  leaked on its own remains reachable until the item or the room is deleted.

**Abuse containment:** guest writes are rate-limited, item counts and byte sizes are capped,
and everything expires. The site-wide `allow_guests_to_upload_files` System Setting stays
**OFF** — we never route guest uploads through Frappe's generic `upload_file` handler.

## 6. Data model

### 6.1 DocType `Clipboard`
Naming: `autoname: field:room_name` — the doc name **is** the URL segment.

| Field | Type | Notes |
|---|---|---|
| `room_name` | Data, unique, reqd | Slugified. Validated `^[a-z0-9][a-z0-9-]{2,40}$` |
| `expires_on` | Datetime, read-only | Set on create; slides on every write |
| `last_activity` | Datetime, read-only | |

Permissions: no role gets desk write. System Manager read/delete for cleanup only.
All real access is through the guest API in §7, which owns its own authorization.

### 6.2 DocType `Clipboard Item`

| Field | Type | Notes |
|---|---|---|
| `clipboard` | Link → Clipboard, reqd | Indexed |
| `item_type` | Select: `Text` / `Image`, reqd | |
| `content` | Long Text | Text items only |
| `file_url` | Data | Image items only |
| `file_name` | Data | Image items only |
| `file_size` | Int | Bytes, image items only |

`on_trash` deletes the attached File doc so no orphans survive.

### 6.3 DocType `Clipboard Settings` (Single)

| Field | Type | Default |
|---|---|---|
| `validity_hours` | Int | 24 |
| `max_items_per_room` | Int | 200 |
| `max_image_size_mb` | Float | 5 |
| `max_text_size_kb` | Int | 100 |
| `writes_per_minute_per_ip` | Int | 30 |

## 7. Backend — `misc_helper/clipboard/api.py`

Every endpoint is `@frappe.whitelist(allow_guest=True)`. Writes carry `@frappe.rate_limit`.
Limits are read from `Clipboard Settings` via `frappe.get_cached_doc`, never hardcoded.

### 7.1 `get_room(room_name)`
Slugify and validate the name. Look up the `Clipboard`; if absent **or expired**, create it
fresh with `expires_on = now + validity_hours`. Return room meta plus items, newest first,
in **one** `frappe.get_all` — no per-item `get_doc` (N+1).

Returns: `{room_name, expires_on, items: [...]}`

### 7.2 `add_text(room_name, content)`
Reject empty content and content over `max_text_size_kb`. Reject if the room is at
`max_items_per_room`. Insert the item, slide `expires_on` and `last_activity`, publish (§7.5).

### 7.3 `delete_item(room_name, item_name)`
Verify the item actually belongs to that room before deleting — never trust the item name alone.

### 7.4 `add_image(room_name, file_name, data_base64)`
**Our own endpoint, not Frappe's `upload_file`** — Frappe's requires the site-wide
`allow_guests_to_upload_files` setting, which would open guest uploads for every doctype on
the site (`frappe/handler.py:135`). We keep that off and own the limits here instead.

Steps: decode base64 → enforce `max_image_size_mb` on the **decoded** bytes → validate the
magic bytes are a real image (png/jpeg/gif/webp), not just a trusted extension → create a
`File` with `is_private = 0` and a `frappe.generate_hash()` filename → create the
`Clipboard Item` → slide expiry → publish.

> **Why public files:** Frappe permission-checks private files against their attached doc, so a
> guest could never load one. A public file with an unguessable hashed name matches the trust
> model of the room URL itself. Alternative if you want it tighter: stream images through a
> gated endpoint (~40 extra lines). **Decision needed — see §12.**

### 7.5 Realtime
On every write:

```python
frappe.realtime.publish_to_website(f"clipboard_update:{room_name}", {}, after_commit=True)
```

Verified working for guests: every socket — Guest included — auto-joins the `website` room
(`frappe/realtime/handlers.js:8`), and `socketio_client.js` ships in `frappe-web.bundle.js`,
so `frappe.realtime.on()` works on a portal page with no login and no permission handshake
(unlike `doc_subscribe`, which calls `has_permission`).

**The payload is deliberately empty.** The `website` room broadcasts to every connected
socket on the site, so it must never carry content. It is a bare "something changed" ping;
the client re-fetches through `get_room`, which is the single place authorization lives.
This keeps us correct today and still correct after v2 adds passcodes.

### 7.6 Scheduled cleanup — `misc_helper/clipboard/cleanup.py`
Daily via `scheduler_events`. Delete every `Clipboard` past `expires_on` and cascade to its
items and their Files. An expired name is immediately free for reuse.

## 8. Routing

```python
website_route_rules = [{"from_route": "/clipboard/<room_name>", "to_route": "clipboard/room"}]
```

- `www/clipboard/index.html` — landing: one input, "enter a room name", plus the naming advice.
- `www/clipboard/room.html` + `room.py` — the room itself. `get_context` reads
  `frappe.form_dict.room_name`, calls `get_room`, and server-renders the first paint so the
  page is useful before JS boots. Also renders the QR code (`qrcode`, already in the bench env)
  as an inline data-URI SVG.

## 9. Frontend — Jinja + Tailwind v4 (on frappe-ui tokens) + Alpine

Confirmed stack. No SPA framework, no jQuery. Mobile-first. All JS `async/await` — no
callbacks, no nested promises.

### 9.1 Design tokens — use frappe-ui/espresso, define no colors

Frappe's website bundle **already loads the frappe-ui (espresso) token layer** on every portal
page: `website.bundle.scss` imports `espresso/_colors.scss`, which pulls in
`frappe/public/css/espresso/colors.css` — 1125 CSS custom properties, with a
`[data-theme="dark"]` block already wired to Frappe's theme switching.

So the page inherits light/dark for free and **must not declare a single hardcoded color**.

Semantic tokens to build on:

| Purpose | Token |
|---|---|
| Page background | `--surface-base` |
| Card / raised surface | `--surface-gray-1` … `--surface-gray-10` |
| Elevated panel | `--surface-elevation-1` / `-2` / `-3` |
| Primary text | `--ink-gray-8`, `--ink-gray-9` |
| Muted / secondary text | `--ink-gray-5`, `--ink-gray-6` |
| Link | `--ink-blue-link` |
| Borders | `--outline-gray-1` … `--outline-gray-9`, `--outline-base` |
| Destructive (delete) | `--surface-red-*`, `--ink-red-*` |
| Success (copied) | `--surface-green-*`, `--ink-green-*` |

### 9.2 Why we still add Tailwind

The bundle also ships `scss/common/utilities.scss`, but it is a curated ~55-class subset —
flex, a few rounded/shadow/border helpers — with **no spacing, gap, padding, or text-size
scale**. Not enough to lay out a page.

So the app gets a **Tailwind v4 CLI build**, with its theme mapped onto the espresso tokens
via `@theme` so Tailwind utilities emit the frappe-ui variables rather than their own palette:

```css
@import "tailwindcss";
@theme {
  --color-surface-base: var(--surface-base);
  --color-surface-card: var(--surface-gray-1);
  --color-ink-base: var(--ink-gray-8);
  --color-ink-muted: var(--ink-gray-5);
  --color-outline-base: var(--outline-gray-2);
  /* … */
}
```

`bg-surface-card` / `text-ink-muted` then resolve to frappe-ui tokens, and dark mode follows
Frappe's `[data-theme]` with no extra work. Static class names only — no computed strings, or
the Tailwind scanner drops them.

### 9.3 Layout & interactions

**Layout:** room name as heading · expiry countdown · QR code (collapsed on mobile) ·
large paste box · item list, newest first.

- A document-level `paste` handler, so <kbd>Ctrl+V</kbd> works anywhere on the page without
  focusing anything. Text → `add_text`. Image blob → base64 → `add_image`.
- Typed text plus a Send button, for the phone case where there is nothing to paste.
- Per item: **Copy** (`navigator.clipboard.writeText`, or `ClipboardItem` for images) with a
  brief "Copied" state, and **Delete**.
- Images render as thumbnails; click to open full size.
- `frappe.realtime.on('clipboard_update:<room>')` → refetch. A 10s poll runs as a backstop
  only if the socket never connects.
- Empty state explains the room is public and names the expiry.

## 10. Build order

1. `Clipboard`, `Clipboard Item`, `Clipboard Settings` doctypes — created via `bench console`
   with `frappe.new_doc` so validations actually run, then exported to disk.
2. `api.py` — `get_room`, `add_text`, `delete_item`. Verify in `bench console`.
3. Routing + `room.html` server-rendered, text only. First end-to-end paint.
4. Alpine layer: paste handler, copy, delete.
5. Realtime publish + subscribe. Verify with two browsers.
6. `add_image` + thumbnails + copy-image.
7. QR code.
8. Cleanup job + `scheduler_events`. Verify by hand-setting `expires_on` into the past.
9. Ruff + Biome. Tests (§11). Screenshots on desktop and mobile widths.

## 11. Testing

Backend (`FrappeTestCase`, real DB, rolled back): lazy creation on first visit · expired room
is recreated empty rather than resurrected · name validation accepts and rejects the right
shapes · text over cap rejected · item cap enforced · `delete_item` refuses an item from
another room · oversized image rejected · non-image bytes with a `.png` name rejected ·
cleanup deletes expired rooms, their items, and their Files · `get_room` issues no N+1.

Manual, evidence captured: two browsers on one room, paste in A appears in B without reload ·
phone via QR · copy-paste round trip for text and image on desktop and mobile.

## 12. Decisions — settled 2026-08-29

All three confirmed by Rahul; the build proceeds on these.

1. **Image storage** — **public files with `frappe.generate_hash()` filenames.** Accepted
   consequence: an image URL leaked on its own stays reachable until the item or room is
   deleted. Matches the room URL's own trust model. The gated streaming endpoint stays
   available as a later tightening if the threat model changes.
2. **Expiry** — **slides on activity.** Every successful write resets
   `expires_on = now + validity_hours`, so a room in active use survives and an abandoned one
   dies on schedule.
3. **Room name collisions** — **warn, never reject.** `get_room` returns `is_weak_name` and the
   UI shows a one-line caution. Rejecting names would break the "say it out loud" property that
   is the whole point of the feature.

**Status:** spec approved, build in progress.
