# misc_helper — app notes

Personal day-to-day helper app. Modules: `Misc Helper`, `Travel` (stub), `Clipboard`.

Feature specs live in `docs/`. The clipboard feature is specified in
[`docs/clipboard-spec.md`](docs/clipboard-spec.md) — read it before changing anything under
`misc_helper/clipboard/` or `misc_helper/www/clipboard/`.

## Bench facts for this machine

**Two benches run here.** This one (`develop`) serves web on **:8010** with socketio on
**9012**; an unrelated bench occupies :8000. Testing a portal page on :8000 loads a page whose
websocket points at a socketio it does not own — realtime silently never connects and looks
like a code bug. Always use :8010.

Site: `hobby.localhost` (keep `developer_mode: 1` — doctype changes are exported to disk
through it).

---

# Framework gotchas — learned the hard way, with the why

## Hooks

**`web_include_css`, not `app_include_css`.** `app_include_css` injects into `desk.html` only;
website/portal pages read `web_include_css`. There is no `build_include` hook in Frappe.
It loads on *every* website page, so scope your selectors.

## Rate limiting

**`frappe.rate_limit` does not exist** on this version — import from `frappe.rate_limiter`.
Migrate fails on the wrong one. Pass a **callable** as `limit` (`rate_limiter.py:145` resolves
it per request) so the cap really comes from a Settings doctype instead of being frozen at
import time.

## Naming — `autoname: field:x`

**Normalise in `before_naming`, never in `validate`.** `Document.insert` runs `set_new_name()`
first, deriving `name` from the RAW field; then `_validate()` calls
`base_document.py:1323 _sync_autoname_field()`, which does `self.set(fieldname, self.name)` —
writing the raw value back over whatever `validate` just normalised. Rejection in `validate`
still works; normalisation is silently discarded.

## Long Text is bleach-sanitised behind your back

`base_document._sanitize_content` (`base_document.py:1410`) filters any Long Text field whose
value contains `<` or `>`. It entity-escapes `&` **and silently deletes `<script>` blocks
outright** — so stored user text is corrupted and pasted code loses chunks.

It short-circuits when the value has no angle bracket (`base_document.py:1399`), so `a & b`
alone is untouched. Only angle-bracket-bearing text is hit, which means *code* is the victim.

Escape hatch: `ignore_xss_filter = 1` on the field. **Consequence: storage is then raw, so
render-time escaping becomes load-bearing** — Jinja `| e` and Alpine `x-text` are the only
thing standing between a pasted payload and stored XSS. Pin the flag with a test that reads
`frappe.get_meta(dt).get_field(f).ignore_xss_filter`; a future doctype edit can silently drop it.

## Guest requests are sanitised before your function runs

`is_whitelisted` (`frappe/__init__.py:648-654`) bleach-sanitises **every string in `form_dict`**
when the caller is Guest, unless the method is marked `xss_safe`:

```python
if is_guest and method not in xss_safe_methods:
    for key, value in form_dict.items():
        if isinstance(value, str):
            form_dict[key] = sanitize_html(value)
```

It entity-escapes `&` and silently DELETES tags — so guest-submitted text arrives corrupted and
your function never sees what was sent. This is separate from, and upstream of, the
`ignore_xss_filter` field flag; fixing only the field flag does nothing for guests.

**It bites only guests**, so in-process tests and logged-in manual checks all pass while the
real users get mangled text. `<b>bold</b>` survives bleach and is a false-negative canary —
test with `<ok>` or `<script>`.

Opt out per endpoint with `@frappe.whitelist(allow_guest=True, xss_safe=True)`
(`frappe/__init__.py:581`). Do NOT blanket-apply it: set it only on endpoints that genuinely
accept free text, and leave it off where arguments are re-derived through strict validation —
that keeps a free extra layer. **`xss_safe=True` makes render-time escaping the only remaining
XSS defence for that endpoint**, so pair it with a render-side test.

Pin it with a test: an in-process test CANNOT catch a regression here, because it never reaches
`is_whitelisted`. Assert `your.module.method in frappe.xss_safe_methods`, or drive real
cookie-less HTTP.

## Realtime init ordering on portal pages

`frappe.realtime.on()` is guarded by `if (this.socket)` (`socketio_client.js:12`) and **silently
does nothing** when the socket does not exist yet — no listener, no connection, no error. The
website bundle builds the socket from its own `frappe.ready` handler
(`website/js/website.js:684`), which runs AFTER `alpine:init`. So an Alpine component that
subscribes in `init()` registers nothing and the feature quietly falls back to whatever polling
you wrote, which masks the bug.

Fix: call `frappe.realtime.init(window.socketio_port, true)` yourself before `on()`.
`RealTimeClient.init()` short-circuits on `if (this.socket) return`, so it is order-independent
and exactly one socket is built either way. `window.socketio_port` is set in `base.html:53`.

Verify with `frappe.realtime.socket.connected` and
`frappe.realtime.socket.listeners('<event>').length` — not by watching the UI update, which
polling will do for you and hide the fault.

## Page background in both themes

Set `context.body_class` from `get_context` (`base.html:57` renders it) rather than styling
`body` in your app CSS — a `web_include_css` file loads on EVERY portal page, so a `body` rule
there repaints the whole site. Without this the page shows a white band below the app wrapper
in dark mode.

## Realtime for guests

Guests **can** receive realtime on portal pages: every socket including Guest auto-joins the
`website` room (`realtime/handlers.js:8`), and `socketio_client.js` ships in
`frappe-web.bundle.js`. Use `frappe.realtime.publish_to_website(event, msg)`.

`doc_subscribe` / `doctype_subscribe` will NOT work for guests — they call `has_permission`.

**The `website` room broadcasts to every connected socket on the site**, so never put content
in the payload. Publish a content-free "something changed" ping and have the client re-fetch
through an endpoint that owns authorization. That stays correct when access control is added
later.

## Files

- **Do not enable `allow_guests_to_upload_files`** to let guests upload — it is site-wide and
  opens uploads for every doctype (`handler.py:135`). Write your own `allow_guest` endpoint
  and create the `File` doc directly, owning your own size/type limits.
- **`only_allow_system_managers_to_upload_public_files`** breaks guest public uploads if turned
  on: `File.enforce_public_file_restrictions` calls `frappe.only_for("System Manager")`, and
  `ignore_permissions=True` does NOT bypass it.
- **`File.file_name` is not the on-disk filename once bytes are deduped.** `save_file` dedupes
  by `content_hash`, so the second doc keeps its own generated hash in `file_name` while
  `file_url` points at the first-uploaded blob. Only `file_url` resolves to a real path.
- Identical bytes share one blob; `_delete_file_on_disk` refcounts by `content_hash` and only
  unlinks when the last referencing File doc goes. Separate File docs per attachment are safe.
- `delete_doc` already cascades attached Files (`model/delete_doc.py:189`) — an `on_trash` that
  deletes them is redundant.

## Request body size is NOT `System Settings.max_file_size`

`app.py:206` gives **every path except `/api/method/upload_file`** a
`request.max_content_length` of `conf.max_file_size` (site_config, in **bytes**) or 25 MB. So a
custom whitelisted upload endpoint is capped there and Werkzeug 413s the body **before your
function is entered** — raising `System Settings.max_file_size` (which is in **MB**, and only feeds
`get_max_file_size()` for `upload_file` and `File.check_max_file_size`) changes nothing for you.

Two units, two places, and the one the UI exposes is the one that does not apply. On top of that a
base64 body inflates the bytes by 4/3, so a 25 MB transport cap carries ~18.7 MB of actual file.

Consequences:
- Compute the real cap as `min(get_max_file_size(), (transport - envelope) * 3 // 4)` and
  **pre-check it in the browser** — otherwise the user gets a bare 413 with no message, since a
  413 never reaches your `frappe.throw`.
- Verify by asserting the computed number, not by uploading: a passing small file proves nothing
  about where the ceiling is.

## Public files are same-origin, so the extension allowlist IS the XSS control

Anything stored via a public `File` is served from the site's own origin. `File.validate_file_extension`
checks `System Settings.allowed_file_extensions` and **returns early when that is unset** (blank
means allow-all), and it only runs `if frappe.request`. If your endpoint accepts arbitrary types,
own an allowlist of your own and make blank mean *your defaults*, never *everything* — `.svg` and
`.html` carrying a `<script>` are stored XSS against every later visitor.

Magic-byte sniffing is still worth it for formats you can recognise, but it can only ever *confirm*
an extension, never derive one for a format you do not know. Map extension → container family
(mp4/mov/m4v/avif are all ISO-BMFF `ftyp`; mkv/webm are both EBML) and reject a mismatch.

## Adding a field to an existing Single does not apply its default

A Single's field defaults are applied when the doc is first created. Add a field to a doctype whose
Single row already exists and `migrate` leaves it **NULL** — the JSON says `"default": "168"` and
the site reads `None`. Seed it with a patch, and have the code fall back to a module constant so a
blank never silently means zero.

## The clipboard pages do not load Frappe's website bundle at all

**Read this before debugging any CSS problem on `/clipboard`.** Those pages render inside
`templates/clipboard_shell.html`, a standalone `<html>` document that does NOT extend
`templates/base.html`. They ship one stylesheet (`clipboard.css`, Tailwind **with preflight**) and
four scripts, and `window.frappe` is `undefined` on them.

Why: `website.bundle.css` is 692 KB of Bootstrap, and everything it was providing here was three
values — a CSRF token, the socketio port, and a socket.io client. All three now come from
`misc_helper/clipboard/shell.py` (`window.clipboard_boot`) and a vendored socket.io, with realtime
hand-wired in `clipboard_realtime.js`. This is the same shape frappe-ui apps (CRM, Helpdesk,
Gameplan, Drive) use.

**Every collision documented in the next four sections therefore does NOT apply to those pages** —
no `!` modifiers, no `bg-transparent` on buttons, no `m-0`/`list-none`/`text-inherit` on bare
elements, no `.text-sm` weight leak. Write plain utilities. Those sections still govern every
OTHER portal page in this app, which does extend `base.html`.

If a colour or a margin ever mysteriously stops applying on a clipboard page again, the first
thing to check is whether something put that bundle back on the page.

Two things this cost, both worth knowing:
- `body_class` still comes from each page's `get_context`, because the two pages need different
  ones (the room is a fixed-height `h-screen overflow-hidden` flex shell; the landing page is an
  ordinary scrolling document) and because the reference's base-layer body colours live there.
  Without them the document behind the app is the browser's white in both themes.
- Assets are versioned from one constant, `shell.ASSET_VERSION`. **Bump it on any change** to
  `clipboard.css`, `clipboard_room.js` or `clipboard_realtime.js`.

## Bootstrap owns `.bg-primary` and friends, with `!important`

Frappe's `website.bundle.css` ships Bootstrap's contextual utilities — `.bg-primary`,
`.text-primary`, `.border-primary` and the same for secondary/success/danger/warning/info — as
**hardcoded colours with `!important`** (`.bg-primary { background-color: #171717 !important }`).
Any design system whose semantic tokens use those bare names (Penguin UI does) silently loses
every one of them on a portal page, and the failure is invisible in the class list.

Only the BARE names collide: `bg-danger/40` generates a different class and is safe, as is
`text-on-primary`. `web_include_css` is injected after the bundle, so Tailwind's important
modifier (`bg-primary!`) wins on source order. Grep the built bundle before assuming a name is
free:
`python3 -c "import re;print(re.search(r'\.bg-primary\s*\{[^}]*\}', open(PATH).read()))"`

## `.hidden` is `!important` in Frappe's bundle, so `hidden md:flex` never turns on

`website.bundle.css` ships `.d-none, .hide-control, .hide, .hidden { display: none !important }`.
Tailwind emits its own `.hidden{display:none}` with no `!important`, so the two look
interchangeable — but the Bootstrap one wins, and `!important` beats a media-query utility
**regardless of specificity or source order**. Every `hidden md:flex` / `hidden sm:inline` on a
portal page is therefore permanently invisible at every width.

This is the inverse of the `.bg-primary` collision above and hides in exactly the same way: the
class list reads correctly, no error is raised, and the element is simply never there. It cost a
sidebar and the room header's "Delete after" label before it was found.

Fix with Tailwind's important modifier on the SHOWING half — `hidden md:flex!`, `hidden sm:inline!`
— never by dropping `hidden` (the element would then show at every width). `md:hidden` is
unaffected, because it wants `display:none` anyway, which is why half the responsive classes on a
page keep working and mask the other half.

Verify in the browser, not in the class list:
`getComputedStyle(el).display` at a wide viewport. And when walking the CSSOM to find the winning
rule, do NOT skip nodes that have a `cssRules` property — Chrome now gives every `CSSStyleRule` an
empty one, so `if (r.cssRules) recurse; else check` silently skips every style rule and reports
that the colliding rule does not exist.

## The Tailwind-built stylesheet has no cache-busting

`bundled_asset()` only content-hashes real `*.bundle.css` files. A stylesheet built by the
Tailwind CLI and referenced from `web_include_css` ships with none, so browsers serve a stale copy
across deploys — and a stale stylesheet is not cosmetic here, since the cascade fixes below live
in it. Put a `?v=N` on the `web_include_css` path and bump it on every CSS change. **While
developing, a changed class that "does nothing" is almost always this, not your markup.**

## Component CSS must be emitted BEFORE Tailwind's utilities

Hand-written component classes have to stay unlayered to beat Frappe's own unlayered bundle — put
them in `@layer components` and Bootstrap's button rules flatten them. But unlayered also means
source order decides against Tailwind's utilities, so emitting them *after* the utilities lets
`.my-button { display: inline-flex }` silently defeat `md:hidden`, and responsive show/hide stops
working with no error. Import them before `tailwindcss/utilities.css`; it is the only position
where both hold.

## No preflight means Bootstrap still styles bare elements

Preflight is not imported (it strips Frappe's navbar/footer), so Bootstrap's own `pre`, `a`, `h1`
and list rules survive. A class beats them on specificity, but only if you write one: `pre` inside
a coloured bubble keeps Bootstrap's near-black and vanishes unless you add `text-inherit`, and an
`h1` keeps its margins and drops off its row unless you add `m-0`. Every macro carries its own
resets, and this looks fine in isolation — it breaks only on a page that also loads the bundle,
which is every page in production.

The same omission also leaves `img` at the UA's `display: inline`. Preflight would make it `block`,
so **`mx-auto` on an image centres nothing** (an inline box ignores auto side margins) and every
image sits on the text baseline with a descender gap beneath it. Port a scoped
`:where(.cb-page) :is(img, svg, video, canvas) { display: block }` rather than hunting the symptom —
it cost a left-aligned avatar in the join modal that read as "the padding is broken".

## Frappe ships its OWN `.text-xs` / `.text-sm` / `.text-base` / `.text-lg`, and they are not Tailwind's

This is the worst collision found so far, because it is a **partial** override:

```
frappe   .text-sm { font-size; line-height: 1.15; font-weight: 420; letter-spacing: 0.02em }
tailwind .text-sm { font-size; line-height }
```

Same name, same `(0,1,0)` specificity, both unlayered — so your build wins on source order for the
two properties it declares, and **`font-weight` and `letter-spacing` leak through from Frappe on
every element carrying one of those four classes**. Size and leading come out right, which is
exactly why nobody looks further: only the weight (420, not 400) and the tracking (0.02em, not
`normal`) are quietly wrong.

An *arbitrary* value like `text-[10px]` is unaffected, since Frappe defines no such class. So one
page renders some text at 400 and some at 420 with nothing in the markup to explain it.

Neutralise it before the utilities are emitted, so it still loses to `font-bold` / `tracking-*`:
`:where(.cb-page) :is(.text-xs, .text-sm, .text-base, .text-lg) { font-weight: inherit; letter-spacing: inherit }`

Related, same bundle, same class of bug: `body { font-size: var(--text-lg); font-weight: 420;
letter-spacing: 0.02em }` and `p { line-height: 1.7 }` set the page's INHERITED typography at element
specificity. Re-declare the baseline on your page wrapper class (which beats bare `body`), and reset
`p`/`h1`–`h6` with `font: inherit` — an inherited value always loses to a direct rule, so the wrapper
alone cannot reach them.

**Verify typography by measuring, never by looking.** Render the reference in the same browser and
diff `getComputedStyle` for font-size/weight/line-height/letter-spacing on matching elements; a 400
vs 420 weight and 0.02em of tracking are invisible side by side but change every wrap point.

## `space-y-*` silently does nothing when the children are form controls

Tailwind v4 emits space utilities wrapped in `:where(...)`, i.e. specificity `(0,0,0)`. Frappe's
bundle ships Bootstrap's reboot, which includes:

```css
input, button, select, optgroup, textarea { margin: 0; }
```

That is `(0,0,1)` and beats `(0,0,0)` **regardless of source order**, so `space-y-3` on a form whose
children are an `<input>` and a `<button>` produces exactly 0px and the controls touch. Nothing in
the class list hints at it.

Use `flex flex-col gap-3` instead — `gap` is not a margin, so the reboot cannot reach it. The same
applies to `space-x-*` on a row of buttons.

## Committing from a read path

Never call `frappe.db.commit()` in a GET handler — it commits the caller's entire ambient
transaction. Set `frappe.flags.commit = True` instead; the framework commits once at end of
request (`app.py:433`).

## Tailwind v4 on a Frappe portal page

- Frappe **already ships the frappe-ui/espresso token layer** on every website page
  (`frappe/public/css/espresso/colors.css` via `website.bundle.scss`) — ~1125 CSS variables
  with a `[data-theme="dark"]` block. Mapping onto these gets dark mode for free.
- The bundled `scss/common/utilities.scss` is only a ~55-class subset (no spacing or text
  sizes), so a real Tailwind build is still needed.
- **Use `@theme inline`.** Plain `@theme` freezes the token's value at `:root`, breaking the
  dark-mode swap. `inline` substitutes the `var()` into the utility itself.
- **Import utilities UNLAYERED.** Frappe's bundle is unlayered, and unlayered rules beat any
  `@layer` regardless of source order — put utilities in `@layer utilities` and Frappe's
  `.inline-flex` silently overrides your `md:hidden`.
- **Do not import preflight** — it strips Frappe's navbar and footer. Without it, Bootstrap's
  list padding survives, so add `list-none pl-0` on `<ul>`s.

## The linter: Biome owns JS, and semgrep needs a reason not a silence

The Frappe app boilerplate ships **prettier + eslint** pre-commit hooks, but this app formats JS
with **Biome** (`biome.json`, `yarn lint`). Three formatters over the same files can never all be
satisfied — prettier and Biome disagree on tabs and quote style, so `clipboard_room.js` churned
~370 lines on every run — and neither boilerplate hook excluded the vendored `alpine.min.js` /
`socket.io.min.js`, so each run rewrote 6000 lines of minified library and eslint then reported
`no-func-assign` against them. Both hooks are gone; the `biomejs/pre-commit` hook is the only JS
tool, pinned to the same version as `package.json`. `.eslintrc` was deleted with them — it was desk
boilerplate (globals for `cur_frm`, jQuery, Cypress) that this Tailwind/Alpine app never used.

**Keep the pre-commit rev, `package.json`'s `@biomejs/biome`, and `biome.json`'s `$schema` on one
version.** The hook installs its own copy, so a drifting pin means CI and your machine format
differently and the diff flip-flops per committer.

The linter job also runs **semgrep**, which is not optional-advisory in CI — any finding exits
non-zero. Two rules fire here by design and both are silenced per-line with a stated reason, which
is what the rules themselves ask for:

- `guest-whitelisted-method` on all 8 `allow_guest=True` endpoints — a clipboard reached by saying
  a room name out loud is the product; there is no user to check permissions against. The rationale
  lives once in the GUEST ACCESS block at the top of `api.py` and each decorator points back to it.
- `frappe-manual-commit` — the scheduled cleanup job and the test base's settings/teardown, all of
  which must outlive the per-class rollback.

**Put `# nosemgrep` on the line ABOVE the decorator, never trailing it.** Trailing it pushes the
line past ruff's 110 columns, ruff-format then splits the decorator across lines, and the comment
lands on the closing `)` — no longer the line the finding is on, so the silence silently stops
working and the linter goes red again with the comment still sitting there.

## Testing

- **`IntegrationTestCase.change_settings` has no `try/finally`** — an assertion failing inside
  its block leaks the changed setting and commits it to the site. Write a context manager that
  always restores.
- A suite written *after* the code proves nothing by being green. Mutate the implementation one
  line at a time and confirm each mutation turns a specific test red; a mutation that stays
  green means the test is blind, or the "bug" it guards was never real.
