# Copyright (c) 2026, rl0007 and contributors
# For license information, please see license.txt

import base64
import binascii
import re
from datetime import datetime
from typing import TYPE_CHECKING

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils.data import add_to_date, cint, cstr, flt, get_datetime, now_datetime

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

IMAGE_MAGIC_BYTES = (
	(b"\x89PNG\r\n\x1a\n", "png"),
	(b"\xff\xd8\xff", "jpg"),
	(b"GIF87a", "gif"),
	(b"GIF89a", "gif"),
)

ITEM_FIELDS = ("name", "item_type", "content", "file_url", "file_name", "file_size", "creation")


def get_settings() -> "ClipboardSettings":
	return frappe.get_cached_doc("Clipboard Settings")


def get_writes_per_minute() -> int:
	return cint(get_settings().writes_per_minute_per_ip)


@frappe.whitelist(allow_guest=True)
def get_room(room_name: str) -> dict:
	"""Return the room and its items, newest first. Creates the room if it is absent or expired."""
	room_name = get_valid_room_name(room_name)
	room = get_active_room(room_name)

	return {
		"room_name": room_name,
		"expires_on": cstr(room["expires_on"]),
		"is_weak_name": is_weak_name(room_name),
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
def add_text(room_name: str, content: str) -> dict:
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
	# The room name is the only credential this product has; §5 of the spec makes that the trust
	# model, so the guest API authorizes and the doctype stays closed to the desk.
	item.insert(ignore_permissions=True)

	save_write(room_name)
	return get_item_data(item)


# No xss_safe here, deliberately: this endpoint takes no free text. room_name and file_name are
# re-derived through strict patterns below, and base64 has no character sanitize_html would touch,
# so the guest form_dict sanitiser is a free extra layer. Same reasoning for get_room/delete_item.
@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(key="room_name", limit=get_writes_per_minute, seconds=60)
def add_image(room_name: str, file_name: str, data_base64: str) -> dict:
	room_name = get_valid_room_name(room_name)
	settings = get_settings()

	image_bytes = get_image_bytes(data_base64, flt(settings.max_image_size_mb))
	extension = get_image_extension(image_bytes)

	get_active_room(room_name)
	validate_room_capacity(room_name, settings)

	# The item is inserted first only so the File can carry its attachment from the start; the
	# ordering has no effect on how Frappe stores the bytes.
	item = frappe.new_doc("Clipboard Item")
	item.clipboard = room_name
	item.item_type = "Image"
	item.file_name = get_display_file_name(file_name, extension)
	item.file_size = len(image_bytes)
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
			"file_name": f"{frappe.generate_hash(length=32)}.{extension}",
			"is_private": 0,
			"content": image_bytes,
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

	# Never trust the item name alone: a guest holding one room's name must not reach another's.
	owning_room = frappe.db.get_value("Clipboard Item", cstr(item_name), "clipboard")
	if owning_room != room_name:
		frappe.throw(_("This item does not belong to room {0}.").format(room_name), frappe.PermissionError)

	frappe.delete_doc("Clipboard Item", item_name, ignore_permissions=True, delete_permanently=True)
	save_write(room_name)


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


def get_expiry() -> datetime:
	return add_to_date(now_datetime(), hours=cint(get_settings().validity_hours))


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
	room.expires_on = get_expiry()
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
		{"expires_on": get_expiry(), "last_activity": now_datetime()},
		update_modified=False,
	)
	# The payload stays empty on purpose: the website room broadcasts to every socket on the
	# site, so it may never carry content. Clients re-fetch through get_room, which authorizes.
	#
	# publish_to_website() is a v17-only convenience wrapper (frappe/realtime/__init__.py) that
	# does not exist on v16, which this app targets -- AttributeError in production. Call the
	# underlying publish_realtime() with the same room directly; both exist on every version.
	frappe.publish_realtime(
		f"clipboard_update:{room_name}", {}, room=frappe.realtime.get_website_room(), after_commit=True
	)


def get_image_bytes(data_base64: str, max_image_size_mb: float) -> bytes:
	data_base64 = "".join(cstr(data_base64).split())
	# Browsers hand back a data URI when reading a pasted image; keep only the payload.
	if "," in data_base64[:64]:
		data_base64 = data_base64.split(",", 1)[1]

	try:
		image_bytes = base64.b64decode(data_base64, validate=True)
	except (binascii.Error, ValueError):
		frappe.throw(_("Image data is not valid base64."))

	if not image_bytes:
		frappe.throw(_("Cannot add an empty image."))

	# The cap belongs on the decoded bytes; base64 inflates by a third and would let a
	# larger image through.
	if len(image_bytes) > int(max_image_size_mb * 1024 * 1024):
		frappe.throw(_("Image is larger than the {0} MB limit.").format(max_image_size_mb))

	return image_bytes


def get_image_extension(image_bytes: bytes) -> str:
	"""Sniff the real format. A supplied name or extension proves nothing about the bytes."""
	for magic_bytes, extension in IMAGE_MAGIC_BYTES:
		if image_bytes.startswith(magic_bytes):
			return extension

	if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
		return "webp"

	frappe.throw(_("Only PNG, JPEG, GIF and WebP images are accepted."))


def get_display_file_name(file_name: str, extension: str) -> str:
	"""A label for the UI only -- the stored file is named from a hash, never from this."""
	file_name = cstr(file_name).strip().rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
	file_name = re.sub(r"[^A-Za-z0-9._-]", "-", file_name)[:100].strip("-.")

	return file_name or f"pasted-image.{extension}"
