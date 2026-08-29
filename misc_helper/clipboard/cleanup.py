# Copyright (c) 2026, rl0007 and contributors
# For license information, please see license.txt

import frappe
from frappe.utils.data import now_datetime


def delete_expired_rooms() -> None:
	"""Daily scheduled job. An expired name is free for reuse the moment its room is gone."""
	expired_room_names = frappe.get_all(
		"Clipboard",
		filters={"expires_on": ("<", now_datetime())},
		pluck="name",
		limit_page_length=0,
	)
	for room_name in expired_room_names:
		delete_room(room_name)
		# A worker's work is invisible until it commits, and one bad room must not
		# roll back every room cleaned before it.
		frappe.db.commit()


def delete_room(room_name: str) -> None:
	"""Delete a room, its items, and the File attached to each image item."""
	item_names = frappe.get_all(
		"Clipboard Item",
		filters={"clipboard": room_name},
		pluck="name",
		limit_page_length=0,
	)
	# ponytail: per-item delete_doc so Frappe's attachment cascade removes each image File,
	# capped by max_items_per_room; revisit if that cap is ever raised past a few thousand.
	for item_name in item_names:
		frappe.delete_doc("Clipboard Item", item_name, ignore_permissions=True, delete_permanently=True)

	frappe.delete_doc("Clipboard", room_name, ignore_permissions=True, delete_permanently=True)
