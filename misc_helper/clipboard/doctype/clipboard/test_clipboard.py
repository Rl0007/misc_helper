# Copyright (c) 2026, rl0007 and Contributors
# See license.txt

import frappe

from misc_helper.clipboard.test_base import ClipboardTestBase

EXTRA_TEST_RECORD_DEPENDENCIES = []
IGNORE_TEST_RECORD_DEPENDENCIES = []


class IntegrationTestClipboard(ClipboardTestBase):
	def test_room_name_is_slugified_on_save(self):
		suffix = frappe.generate_hash(length=8)
		room = frappe.new_doc("Clipboard")
		room.room_name = f"Desk Room {suffix.upper()}"
		room.insert(ignore_permissions=True)
		# Register the name the insert actually produced, so a failing assertion below still cleans up.
		self.rooms_to_clean.append(room.name)

		self.assertEqual(room.room_name, f"desk-room-{suffix}")
		# autoname is field:room_name, so the doc name is the validated URL segment.
		self.assertEqual(room.name, f"desk-room-{suffix}")

	def test_invalid_room_name_is_rejected_at_the_doctype(self):
		room = frappe.new_doc("Clipboard")
		room.room_name = "-nope"

		self.assertRaises(frappe.ValidationError, room.insert, ignore_permissions=True)

	def test_room_name_is_unique(self):
		room_name = self.create_room()

		duplicate = frappe.new_doc("Clipboard")
		duplicate.room_name = room_name

		self.assertRaises(frappe.DuplicateEntryError, duplicate.insert, ignore_permissions=True)
