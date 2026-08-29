import io

import frappe
from frappe import _
from frappe.utils import get_url, now_datetime, time_diff_in_seconds

from misc_helper.clipboard.api import get_room


def get_context(context):
	room = get_room(frappe.form_dict.room_name)
	room_url = get_url(f"/clipboard/{room['room_name']}")

	context.room = room
	context.expires_in_seconds = max(0, int(time_diff_in_seconds(room["expires_on"], now_datetime())))
	context.room_url = room_url
	context.qr_svg = get_qr_svg(room_url)
	context.labels = get_labels()
	context.title = room["room_name"]
	context.body_class = "bg-surface-base"
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


def get_labels() -> dict:
	"""Strings the Alpine component builds at runtime, translated here because the component
	lives in a static .js file that Jinja never sees."""
	return {
		"expired": _("expired"),
		"in_prefix": _("in"),
		"text": _("Text"),
		"request_failed": _("That did not go through. Please try again."),
		"copy_blocked": _("Your browser blocked the copy. Select the text and copy it manually."),
	}
