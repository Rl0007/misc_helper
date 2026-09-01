# Copyright (c) 2026, rl0007 and contributors
# For license information, please see license.txt

import frappe

from misc_helper.clipboard.api import DEFAULT_FILE_TYPES, DEFAULT_MAX_VALIDITY_HOURS


def execute():
	"""Fill the fields added for arbitrary file uploads and per-room expiry.

	A Single's field defaults are applied when the doc is first created, never when a field is
	added to an existing one, so a site that already had Clipboard Settings would come out of
	migrate with both of these empty.
	"""
	settings = frappe.get_doc("Clipboard Settings")
	if not settings.allowed_file_types:
		settings.allowed_file_types = "\n".join(DEFAULT_FILE_TYPES)
	if not settings.max_validity_hours:
		settings.max_validity_hours = DEFAULT_MAX_VALIDITY_HOURS

	settings.save(ignore_permissions=True)
