# Copyright (c) 2026, rl0007 and Contributors
# See license.txt

import frappe

from misc_helper.clipboard.test_base import ClipboardTestBase

EXTRA_TEST_RECORD_DEPENDENCIES = []
IGNORE_TEST_RECORD_DEPENDENCIES = []


class IntegrationTestClipboardItem(ClipboardTestBase):
	def test_content_field_keeps_the_xss_filter_disabled(self):
		"""Pins the doctype JSON flag itself, not just today's behaviour: a future edit in the
		DocType form would drop it silently and pasted code would start getting bleached again."""
		content_field = frappe.get_meta("Clipboard Item").get_field("content")

		self.assertTrue(
			content_field.ignore_xss_filter,
			"Clipboard Item.content must set ignore_xss_filter or _sanitize_content mangles pasted text",
		)

	def test_clipboard_link_is_mandatory(self):
		item = frappe.new_doc("Clipboard Item")
		item.item_type = "Text"
		item.content = "orphan"

		self.assertRaises(frappe.MandatoryError, item.insert, ignore_permissions=True)
