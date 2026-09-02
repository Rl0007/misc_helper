import frappe
from frappe import _
from frappe.utils import now_datetime, time_diff_in_seconds

from misc_helper.clipboard.api import IMAGE_FILE_TYPES, VIDEO_FILE_TYPES, get_room
from misc_helper.clipboard.shell import add_shell_context


def get_context(context):
	room = get_room(frappe.form_dict.room_name)

	context.room = room
	context.expires_in_seconds = max(0, int(time_diff_in_seconds(room["expires_on"], now_datetime())))
	context.validity_options = get_validity_options(room["max_validity_hours"])
	context.image_file_types = sorted(IMAGE_FILE_TYPES)
	context.video_file_types = list(VIDEO_FILE_TYPES)
	# The <input accept> syntax wants dotted, lowercase extensions.
	context.accept_attribute = ",".join(f".{file_type.lower()}" for file_type in room["allowed_file_types"])
	context.labels = get_labels()
	context.title = room["display_name"] or room["room_name"]
	# templates/clipboard_shell.html renders this on <body>. Kept in the controller rather than
	# hardcoded in the shell because the two pages need different ones: this is also where the
	# reference's base-layer body colours live.
	context.body_class = (
		"cb-page flex h-screen flex-col overflow-hidden antialiased"
		" bg-surface-alt text-on-surface dark:bg-surface-dark dark:text-on-surface-dark"
	)
	context.no_cache = 1
	add_shell_context(context)


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
		"no_messages": _("no messages yet"),
		"sent_a_file": _("sent a file"),
		"rich_text_on": _("rich text on"),
		"link_url": _("Link URL"),
		"message_copied": _("Message copied"),
		"image_copied": _("Image copied"),
		"link_copied": _("Link copied — anyone with it can join"),
		"copy_blocked": _("Your browser blocked the copy. Select the text and copy it manually."),
		"room_created": _("Room created — share the link"),
		"request_failed": _("That did not go through. Please try again."),
		"file_ready": _("1 file ready to send"),
		"no_extension": _("Give the file a name with an extension, like clip.mp4."),
		"theme_light": _("Light"),
		"theme_dark": _("Dark"),
		"theme_system": _("Auto"),
		# {0}/{1} are filled in by the component; str.format is not available to it.
		"welcome": _("Welcome, {0}"),
		"files_ready": _("{0} files ready to send"),
		"message_placeholder": _("Message {0}"),
		"type_not_allowed": _("Files of type {0} are not accepted in a clipboard room."),
		"file_too_large": _("That file is {0}. The limit is {1}."),
		"theme_changed": _("Theme: {0}"),
		"theme_title": _("Theme: {0} — switch to {1}"),
	}
