# Copyright (c) 2026, rl0007 and Contributors
# See license.txt

import os

import frappe
from frappe.utils.data import add_to_date, now_datetime

from misc_helper.clipboard.api import add_image, add_text
from misc_helper.clipboard.cleanup import delete_expired_rooms
from misc_helper.clipboard.test_base import ClipboardTestBase


class TestDeleteExpiredRooms(ClipboardTestBase):
	def expire(self, room_name: str) -> None:
		frappe.db.set_value(
			"Clipboard",
			room_name,
			"expires_on",
			add_to_date(now_datetime(), hours=-1),
			update_modified=False,
		)

	def test_expired_room_its_items_and_its_files_are_deleted(self):
		expired_room = self.create_room(prefix="expired")
		text_item = add_text(expired_room, "goes away")["name"]
		image = add_image(expired_room, "shot.png", self.encode(self.make_image_bytes()))
		image_item = image["name"]
		stored_file = frappe.db.get_value(
			"File", {"attached_to_doctype": "Clipboard Item", "attached_to_name": image_item}, "name"
		)
		on_disk_path = self.get_on_disk_path(image["file_url"])
		self.assertTrue(os.path.exists(on_disk_path))

		self.expire(expired_room)
		delete_expired_rooms()

		self.assertFalse(frappe.db.exists("Clipboard", expired_room))
		self.assertFalse(frappe.db.exists("Clipboard Item", text_item))
		self.assertFalse(frappe.db.exists("Clipboard Item", image_item))
		self.assertFalse(frappe.db.exists("File", stored_file))
		self.assertFalse(os.path.exists(on_disk_path))

	def test_unexpired_room_is_left_alone(self):
		live_room = self.create_room(prefix="live")
		live_item = add_text(live_room, "still needed")["name"]
		expired_room = self.create_room(prefix="expired")
		add_text(expired_room, "stale")
		self.expire(expired_room)

		delete_expired_rooms()

		self.assertFalse(frappe.db.exists("Clipboard", expired_room))
		self.assertTrue(frappe.db.exists("Clipboard", live_room))
		self.assertTrue(frappe.db.exists("Clipboard Item", live_item))
		self.assertEqual(frappe.db.count("Clipboard Item", {"clipboard": live_room}), 1)
