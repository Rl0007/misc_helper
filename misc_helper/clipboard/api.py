# Copyright (c) 2026, rl0007 and contributors
# For license information, please see license.txt

import base64
import binascii
import re
from datetime import datetime
from typing import TYPE_CHECKING

import frappe
from frappe import _
from frappe.core.api.file import get_max_file_size
from frappe.rate_limiter import rate_limit
from frappe.utils.data import add_to_date, cint, cstr, get_datetime, now_datetime

from misc_helper.clipboard.cleanup import delete_room

if TYPE_CHECKING:
	from misc_helper.clipboard.doctype.clipboard_item.clipboard_item import ClipboardItem
	from misc_helper.clipboard.doctype.clipboard_settings.clipboard_settings import ClipboardSettings

ROOM_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{2,40}$")

# Names a stranger is likely to type independently, so two unrelated people land in one room.
# Warned about in the UI, never rejected -- the product is "say the room name out loud".
COMMON_ROOM_NAMES = frozenset(
	{"test", "notes", "temp", "demo", "abc", "clip", "paste", "home", "admin", "data"}
)

# Used when Clipboard Settings.allowed_file_types is blank. Blank falls back here rather than
# meaning "allow everything" (Frappe's own convention for System Settings.allowed_file_extensions):
# these files are served public from this site's own origin, so an accidentally emptied field would
# silently start accepting .html and .svg, which is stored XSS against every later visitor.
DEFAULT_FILE_TYPES = (
	"PNG",
	"JPG",
	"JPEG",
	"GIF",
	"WEBP",
	"BMP",
	"AVIF",
	"MP4",
	"WEBM",
	"MOV",
	"MKV",
	"AVI",
	"M4V",
	"OGV",
)

# Rendered as a thumbnail and copyable as a real image; everything else gets a download card.
IMAGE_FILE_TYPES = frozenset({"PNG", "JPG", "JPEG", "GIF", "WEBP", "BMP", "AVIF"})

# Rendered in a <video> player rather than as a download card. Read by the room page too.
VIDEO_FILE_TYPES = ("MP4", "WEBM", "MOV", "MKV", "AVI", "M4V", "OGV")

# Extension -> the container family its bytes must actually be. Several extensions share a family
# (mp4/mov/m4v/avif are all ISO base media; mkv/webm are both Matroska) and sniffing cannot tell
# those apart without parsing the box tree, which buys nothing: every member of a family is either
# allowed or not, together.
FILE_TYPE_FAMILIES = {
	"PNG": "PNG",
	"JPG": "JPEG",
	"JPEG": "JPEG",
	"GIF": "GIF",
	"WEBP": "WEBP",
	"BMP": "BMP",
	"AVIF": "ISOBMFF",
	"MP4": "ISOBMFF",
	"M4V": "ISOBMFF",
	"MOV": "ISOBMFF",
	"MKV": "MATROSKA",
	"WEBM": "MATROSKA",
	"AVI": "AVI",
	"OGV": "OGG",
}

FILE_TYPE_EXTENSION_PATTERN = re.compile(r"^[A-Z0-9]{1,10}$")

# An opaque id the browser generates for itself and stores locally. It is only ever compared
# against itself to decide which side of the chat an item sits on -- never rendered, never treated
# as proof of anything. Anyone can send any value; that is fine, because it grants nothing that
# knowing the room name did not already grant (spec §5).
SENDER_PATTERN = re.compile(r"^[a-f0-9]{16,64}$")

# Fallbacks for when the matching Clipboard Settings field is blank or zero.
DEFAULT_VALIDITY_HOURS = 24
DEFAULT_MAX_VALIDITY_HOURS = 168

# Werkzeug rejects an over-long body before our function is ever entered, so the transport cap is
# the real ceiling -- see get_max_file_bytes.
DEFAULT_TRANSPORT_BYTES = 25 * 1024 * 1024

# Headroom inside the transport cap for the JSON keys, the room name and the file name.
JSON_ENVELOPE_BYTES = 4096

ITEM_FIELDS = (
	"name",
	"item_type",
	"sender",
	"is_pinned",
	"content",
	"file_type",
	"file_url",
	"file_name",
	"file_size",
	"creation",
)


def get_settings() -> "ClipboardSettings":
	return frappe.get_cached_doc("Clipboard Settings")


def get_writes_per_minute() -> int:
	return cint(get_settings().writes_per_minute_per_ip)


@frappe.whitelist(allow_guest=True)
def get_room(room_name: str) -> dict:
	"""Return the room and its items, newest first. Creates the room if it is absent or expired."""
	room_name = get_valid_room_name(room_name)
	room = get_active_room(room_name)

	settings = get_settings()

	return {
		"room_name": room_name,
		"expires_on": cstr(room["expires_on"]),
		"validity_hours": get_room_validity_hours(room_name),
		"max_validity_hours": get_max_validity_hours(settings),
		"is_weak_name": is_weak_name(room_name),
		"allowed_file_types": get_allowed_file_types(settings),
		"max_file_bytes": get_max_file_bytes(),
		"items": get_items(room_name),
	}


# xss_safe is set ONLY here, and only because this endpoint stores text verbatim. For a Guest
# caller, is_whitelisted (frappe/__init__.py:649) bleach-sanitises every string in form_dict before
# the function body runs -- it entity-escapes `&` and silently DELETES tags -- which corrupts the
# clipboard content the user pasted. The flag lifts that for this endpoint's arguments, so
# escaping at render time is now the ONLY XSS defence for guest-submitted text, which is this
# feature's entire user base. Never interpolate `content` unescaped into a template.
@frappe.whitelist(allow_guest=True, xss_safe=True, methods=["POST"])
@rate_limit(key="room_name", limit=get_writes_per_minute, seconds=60)
def add_text(room_name: str, content: str, sender: str | None = None) -> dict:
	room_name = get_valid_room_name(room_name)
	settings = get_settings()

	content = cstr(content).strip()
	if not content:
		frappe.throw(_("Cannot add empty text."))

	max_text_size_kb = cint(settings.max_text_size_kb)
	if len(content.encode()) > max_text_size_kb * 1024:
		frappe.throw(_("Text is larger than the {0} KB limit.").format(max_text_size_kb))

	get_active_room(room_name)
	validate_room_capacity(room_name, settings)

	item = frappe.new_doc("Clipboard Item")
	item.clipboard = room_name
	item.item_type = "Text"
	item.content = content
	item.sender = get_valid_sender(sender)
	# The room name is the only credential this product has; §5 of the spec makes that the trust
	# model, so the guest API authorizes and the doctype stays closed to the desk.
	item.insert(ignore_permissions=True)

	save_write(room_name)
	return get_item_data(item)


# xss_safe, for the same reason add_text is: `content` is the caption, and for a Guest caller
# is_whitelisted (frappe/__init__.py:649) bleach-sanitises every string in form_dict before this
# body runs, which would corrupt it. Escaping at render time is therefore the ONLY XSS defence for
# this endpoint's arguments -- never interpolate `content` or `file_name` unescaped into a
# template. The flag costs nothing for the other arguments: room_name and file_name are re-derived
# through strict patterns below, sender through SENDER_PATTERN, and base64 has no character
# sanitize_html would touch.
@frappe.whitelist(allow_guest=True, xss_safe=True, methods=["POST"])
@rate_limit(key="room_name", limit=get_writes_per_minute, seconds=60)
def add_file(
	room_name: str,
	file_name: str,
	data_base64: str,
	content: str | None = None,
	sender: str | None = None,
) -> dict:
	room_name = get_valid_room_name(room_name)
	settings = get_settings()

	file_bytes = get_file_bytes(data_base64)
	file_type = get_file_type(file_name, settings)
	validate_file_bytes(file_bytes, file_type)

	get_active_room(room_name)
	validate_room_capacity(room_name, settings)

	# The item is inserted first only so the File can carry its attachment from the start; the
	# ordering has no effect on how Frappe stores the bytes.
	item = frappe.new_doc("Clipboard Item")
	item.clipboard = room_name
	item.item_type = "Image" if file_type in IMAGE_FILE_TYPES else "File"
	item.content = get_valid_caption(content, settings)
	item.sender = get_valid_sender(sender)
	item.file_type = file_type
	item.file_name = get_display_file_name(file_name, file_type)
	item.file_size = len(file_bytes)
	item.insert(ignore_permissions=True)

	# Public file with an unguessable name: Frappe permission-checks private files against their
	# attached doc, so a guest could never load one back. Never trust the supplied file name.
	#
	# Every item gets its own File doc, but two rooms holding identical bytes SHARE one blob on
	# disk: File.save_file dedupes by content_hash and points the second doc at the first doc's
	# file_url. That is safe -- File._delete_file_on_disk refcounts by content_hash and only
	# unlinks once the last File doc referencing the blob is deleted.
	#
	# ponytail: because of that dedupe, File.file_name is NOT the on-disk filename -- the doc keeps
	# its own generated hash while file_url points at the first-uploaded blob. Only file_url
	# resolves to a real path. A cleanup script, migration, or the gated streaming endpoint of
	# spec §7.4 must derive paths from file_url, never from file_name.
	#
	# ponytail: depends on the System Setting only_allow_system_managers_to_upload_public_files
	# staying off -- File.enforce_public_file_restrictions calls frappe.only_for("System Manager"),
	# which ignore_permissions does NOT bypass. Turn that setting on and this must move to private
	# files behind a gated streaming endpoint (spec §7.4).
	stored_file = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": f"{frappe.generate_hash(length=32)}.{file_type.lower()}",
			"is_private": 0,
			"content": file_bytes,
			"attached_to_doctype": item.doctype,
			"attached_to_name": item.name,
		}
	).insert(ignore_permissions=True)

	item.db_set("file_url", stored_file.file_url, update_modified=False)

	save_write(room_name)
	return get_item_data(item)


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(key="room_name", limit=get_writes_per_minute, seconds=60)
def delete_item(room_name: str, item_name: str) -> None:
	room_name = get_valid_room_name(room_name)
	validate_item_belongs_to_room(room_name, item_name)

	frappe.delete_doc("Clipboard Item", item_name, ignore_permissions=True, delete_permanently=True)
	save_write(room_name)


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(key="room_name", limit=get_writes_per_minute, seconds=60)
def set_pinned(room_name: str, item_name: str, is_pinned: bool) -> dict:
	"""Pin or unpin one item. The pin is shared -- everyone in the room sees it."""
	room_name = get_valid_room_name(room_name)
	validate_item_belongs_to_room(room_name, item_name)

	is_pinned = cint(is_pinned)
	frappe.db.set_value("Clipboard Item", item_name, "is_pinned", is_pinned, update_modified=False)
	# Deliberately NOT save_write: pinning is not new content, so it must not slide the room's
	# expiry. It still pings viewers so the pin appears everywhere at once.
	publish_room_change(room_name)

	return {"name": item_name, "is_pinned": is_pinned}


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(key="room_name", limit=get_writes_per_minute, seconds=60)
def set_validity(room_name: str, validity_hours: int) -> dict:
	"""Retune how long THIS room survives after its last write.

	Deliberately per-room rather than writing Clipboard Settings.validity_hours: rooms are public
	and guessable by design (spec §5), so a site-wide setter would let anyone holding any room name
	change the expiry of every other room on the site.
	"""
	room_name = get_valid_room_name(room_name)
	settings = get_settings()

	validity_hours = cint(validity_hours)
	max_validity_hours = get_max_validity_hours(settings)
	if validity_hours < 1 or validity_hours > max_validity_hours:
		frappe.throw(
			_("Delete-after must be between 1 and {0} hours.").format(max_validity_hours),
		)

	get_active_room(room_name)
	frappe.db.set_value("Clipboard", room_name, "validity_hours", validity_hours, update_modified=False)
	# Applies the new window immediately rather than only on the next paste, and pings viewers.
	save_write(room_name)

	return {
		"validity_hours": validity_hours,
		"expires_on": cstr(frappe.db.get_value("Clipboard", room_name, "expires_on")),
	}


def validate_item_belongs_to_room(room_name: str, item_name: str) -> None:
	"""Never trust the item name alone: a guest holding one room's name must not reach another's."""
	owning_room = frappe.db.get_value("Clipboard Item", cstr(item_name), "clipboard")
	if owning_room != room_name:
		frappe.throw(_("This item does not belong to room {0}.").format(room_name), frappe.PermissionError)


def get_valid_sender(sender: str | None) -> str:
	"""Keep a well-formed browser id, drop anything else rather than throwing.

	Dropping beats rejecting: the id decides only which side of the chat a bubble sits on, so a
	client that sends nothing (or something odd) should still be able to post -- it just does not
	get to claim a side.
	"""
	sender = cstr(sender).strip().lower()

	return sender if SENDER_PATTERN.match(sender) else ""


def get_valid_caption(content: str | None, settings: "ClipboardSettings") -> str:
	content = cstr(content).strip()
	if not content:
		return ""

	max_text_size_kb = cint(settings.max_text_size_kb)
	if len(content.encode()) > max_text_size_kb * 1024:
		frappe.throw(_("Caption is larger than the {0} KB limit.").format(max_text_size_kb))

	return content


def get_valid_room_name(room_name: str) -> str:
	room_name = cstr(room_name).strip().lower().replace(" ", "-").replace("_", "-")
	if not ROOM_NAME_PATTERN.match(room_name):
		frappe.throw(
			_(
				"Room name must be 3 to 41 characters of lowercase letters, numbers and hyphens,"
				" and start with a letter or number."
			)
		)

	return room_name


def is_weak_name(room_name: str) -> bool:
	"""A name a stranger could land on by accident. The UI warns, it never blocks."""
	return len(room_name) < 6 or room_name in COMMON_ROOM_NAMES


def get_max_validity_hours(settings: "ClipboardSettings") -> int:
	return cint(settings.max_validity_hours) or DEFAULT_MAX_VALIDITY_HOURS


def get_room_validity_hours(room_name: str) -> int:
	"""The room's own window, falling back to the site default, clamped to the current cap.

	Clamping on read rather than on write matters: lowering max_validity_hours must shorten rooms
	that were already set beyond it, not leave them grandfathered past the new limit.
	"""
	settings = get_settings()
	validity_hours = cint(frappe.db.get_value("Clipboard", room_name, "validity_hours"))
	validity_hours = validity_hours or cint(settings.validity_hours) or DEFAULT_VALIDITY_HOURS

	return min(validity_hours, get_max_validity_hours(settings))


def get_expiry(room_name: str) -> datetime:
	return add_to_date(now_datetime(), hours=get_room_validity_hours(room_name))


def get_active_room(room_name: str) -> dict:
	"""Return the live room, creating it fresh if it is absent or already past its expiry."""
	expires_on = frappe.db.get_value("Clipboard", room_name, "expires_on")
	if expires_on:
		if get_datetime(expires_on) > now_datetime():
			return {"room_name": room_name, "expires_on": expires_on}

		# Expired names are reusable, and the new occupant must never see the old occupant's items.
		delete_room(room_name)

	room = frappe.new_doc("Clipboard")
	room.room_name = room_name
	room.validity_hours = cint(get_settings().validity_hours) or DEFAULT_VALIDITY_HOURS
	room.expires_on = add_to_date(now_datetime(), hours=room.validity_hours)
	room.last_activity = now_datetime()
	room.insert(ignore_permissions=True)
	# get_room is reached over GET, which Frappe does not commit for us. Flag the request instead
	# of committing here -- a bare commit would also commit whatever the caller had in flight.
	frappe.flags.commit = True

	return {"room_name": room_name, "expires_on": room.expires_on}


def get_items(room_name: str) -> list[dict]:
	return frappe.get_all(
		"Clipboard Item",
		filters={"clipboard": room_name},
		fields=list(ITEM_FIELDS),
		order_by="creation desc",
		limit_page_length=0,
	)


def get_item_data(item: "ClipboardItem") -> dict:
	return {field: item.get(field) for field in ITEM_FIELDS}


def validate_room_capacity(room_name: str, settings: "ClipboardSettings") -> None:
	max_items_per_room = cint(settings.max_items_per_room)
	if frappe.db.count("Clipboard Item", {"clipboard": room_name}) >= max_items_per_room:
		frappe.throw(
			_("This room already holds its maximum of {0} items. Delete one first.").format(
				max_items_per_room
			)
		)


def save_write(room_name: str) -> None:
	"""Slide the expiry -- an actively used room survives -- and ping every viewer."""
	frappe.db.set_value(
		"Clipboard",
		room_name,
		{"expires_on": get_expiry(room_name), "last_activity": now_datetime()},
		update_modified=False,
	)
	publish_room_change(room_name)


def publish_room_change(room_name: str) -> None:
	# The payload stays empty on purpose: the website room broadcasts to every socket on the
	# site, so it may never carry content. Clients re-fetch through get_room, which authorizes.
	#
	# publish_to_website() is a v17-only convenience wrapper (frappe/realtime/__init__.py) that
	# does not exist on v16, which this app targets -- AttributeError in production. Call the
	# underlying publish_realtime() with the same room directly; both exist on every version.
	frappe.publish_realtime(
		f"clipboard_update:{room_name}", {}, room=frappe.realtime.get_website_room(), after_commit=True
	)


def get_max_file_bytes() -> int:
	"""The largest file this endpoint can actually accept.

	NOT simply the System Settings figure. app.py:206 gives every path except
	/api/method/upload_file a max_content_length of conf.max_file_size or 25 MB, so Werkzeug 413s
	an over-long body before this module is entered -- raising only the System Setting would change
	nothing. On top of that the body is base64, which inflates the bytes by 4/3, plus a small JSON
	envelope. The browser pre-checks against this same number so the user gets a real message
	instead of a bare 413.

	ponytail: single-shot base64 in one request body, so the whole file is buffered in the worker's
	memory. Move to a chunked or multipart endpoint before raising conf.max_file_size far past
	~200 MB.
	"""
	transport_bytes = cint(frappe.conf.get("max_file_size")) or DEFAULT_TRANSPORT_BYTES

	return min(get_max_file_size(), (transport_bytes - JSON_ENVELOPE_BYTES) * 3 // 4)


def get_file_bytes(data_base64: str) -> bytes:
	data_base64 = "".join(cstr(data_base64).split())
	# Browsers hand back a data URI when reading a pasted file; keep only the payload.
	if "," in data_base64[:64]:
		data_base64 = data_base64.split(",", 1)[1]

	try:
		file_bytes = base64.b64decode(data_base64, validate=True)
	except (binascii.Error, ValueError):
		frappe.throw(_("File data is not valid base64."))

	if not file_bytes:
		frappe.throw(_("Cannot add an empty file."))

	# The cap belongs on the DECODED bytes; base64 inflates by a third, so checking the encoded
	# string would let a third more through than intended.
	max_file_bytes = get_max_file_bytes()
	if len(file_bytes) > max_file_bytes:
		frappe.throw(
			_("File is larger than the {0} MB limit.").format(round(max_file_bytes / 1024 / 1024, 1))
		)

	return file_bytes


def get_allowed_file_types(settings: "ClipboardSettings") -> list[str]:
	allowed_file_types = [
		file_type.strip().upper().lstrip(".") for file_type in cstr(settings.allowed_file_types).splitlines()
	]

	return [file_type for file_type in allowed_file_types if file_type] or list(DEFAULT_FILE_TYPES)


def get_file_type(file_name: str, settings: "ClipboardSettings") -> str:
	"""The allowlisted extension for these bytes, taken from the supplied name.

	The extension is the control for formats we cannot sniff, which is why it is checked against
	an allowlist rather than a denylist -- a file this app stores is served public from this site's
	own origin, so one wrong type is stored XSS against every later visitor.
	"""
	extension = cstr(file_name).rsplit(".", 1)[-1].strip().upper() if "." in cstr(file_name) else ""
	if not FILE_TYPE_EXTENSION_PATTERN.match(extension):
		frappe.throw(_("Give the file a name with an extension, like clip.mp4."))

	if extension not in get_allowed_file_types(settings):
		frappe.throw(_("Files of type {0} are not accepted in a clipboard room.").format(extension))

	return extension


def get_file_family(file_bytes: bytes) -> str | None:
	"""The container these bytes really are, or None for a format we cannot recognise."""
	if file_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
		return "PNG"
	if file_bytes.startswith(b"\xff\xd8\xff"):
		return "JPEG"
	if file_bytes.startswith((b"GIF87a", b"GIF89a")):
		return "GIF"
	if file_bytes.startswith(b"BM"):
		return "BMP"
	if file_bytes.startswith(b"\x1aE\xdf\xa3"):
		return "MATROSKA"
	if file_bytes.startswith(b"OggS"):
		return "OGG"
	if file_bytes[:4] == b"RIFF":
		# RIFF is a wrapper; the real format is the four bytes after the length field.
		return {b"WEBP": "WEBP", b"AVI ": "AVI"}.get(file_bytes[8:12])
	# ISO base media (mp4/mov/m4v/avif): a size-prefixed `ftyp` box, always the first box.
	if file_bytes[4:8] == b"ftyp":
		return "ISOBMFF"

	return None


def validate_file_bytes(file_bytes: bytes, file_type: str) -> None:
	"""Confirm the bytes are the format the extension claims. A supplied name proves nothing.

	Only enforced for the families in FILE_TYPE_FAMILIES. An extension someone adds to Clipboard
	Settings later gets no byte check -- there is no honest one to make -- so that addition is the
	moment to think about what the site will then serve.
	"""
	expected_family = FILE_TYPE_FAMILIES.get(file_type)
	if not expected_family:
		return

	if get_file_family(file_bytes) != expected_family:
		frappe.throw(_("This file's contents are not really a {0} file.").format(file_type))


def get_display_file_name(file_name: str, file_type: str) -> str:
	"""A label for the UI only -- the stored file is named from a hash, never from this."""
	file_name = cstr(file_name).strip().rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
	file_name = re.sub(r"[^A-Za-z0-9._-]", "-", file_name)[:100].strip("-.")

	return file_name or f"pasted-file.{file_type.lower()}"
