import io

import frappe
from frappe import _
from frappe.utils import get_url, now_datetime, time_diff_in_seconds

from misc_helper.clipboard.api import VIDEO_FILE_TYPES, get_room


def get_context(context):
	room = get_room(frappe.form_dict.room_name)
	room_url = get_url(f"/clipboard/{room['room_name']}")

	context.room = room
	context.expires_in_seconds = max(0, int(time_diff_in_seconds(room["expires_on"], now_datetime())))
	context.room_url = room_url
	context.qr_svg = get_qr_svg(room_url)
	context.validity_options = get_validity_options(room["max_validity_hours"])
	context.video_file_types = list(VIDEO_FILE_TYPES)
	context.allowed_file_types = room["allowed_file_types"]
	# The <input accept> syntax wants dotted, lowercase extensions.
	context.accept_attribute = ",".join(f".{file_type.lower()}" for file_type in room["allowed_file_types"])
	context.labels = get_labels()
	context.title = room["room_name"]
	context.body_class = "bg-surface"
	context.no_cache = 1


def get_qr_svg(url):
	"""QR modules as an inline SVG that inherits the surrounding text color, so the code
	stays legible in both the light and dark espresso themes."""
	import qrcode
	import qrcode.image.svg

	image = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
	stream = io.BytesIO()
	image.save(stream)

	svg = stream.getvalue().decode().split("?>", 1)[-1].strip()
	return svg.replace("<svg ", '<svg class="w-full h-auto" fill="currentColor" ', 1)


def get_validity_options(max_validity_hours: int) -> list[dict]:
	"""Choices for the delete-after control, translated here rather than assembled in JS.

	A duration label does not survive being built from a number at runtime -- plural rules differ
	per language -- so each one is a whole translatable string.
	"""
	options = (
		(1, _("1 hour")),
		(6, _("6 hours")),
		(12, _("12 hours")),
		(24, _("1 day")),
		(72, _("3 days")),
		(168, _("7 days")),
	)

	return [{"hours": hours, "label": label} for hours, label in options if hours <= max_validity_hours]


def get_labels() -> dict:
	"""Strings the Alpine component builds at runtime, translated here because the component
	lives in a static .js file that Jinja never sees."""
	return {
		"expired": _("expired"),
		"in_prefix": _("in"),
		"text": _("Text"),
		"request_failed": _("That did not go through. Please try again."),
		"copy_blocked": _("Your browser blocked the copy. Select the text and copy it manually."),
		# {0}/{1} are filled in by the component; str.format is not available to it.
		"type_not_allowed": _("Files of type {0} are not accepted in a clipboard room."),
		"file_too_large": _("That file is {0}. The limit is {1}."),
		"no_extension": _("Give the file a name with an extension, like clip.mp4."),
		"draft_placeholder": _("Type, paste, or drop a file"),
		"caption_placeholder": _("Add a caption, or just send"),
	}
