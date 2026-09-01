# Copyright (c) 2026, rl0007 and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document


class ClipboardSettings(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		allowed_file_types: DF.SmallText | None
		max_items_per_room: DF.Int
		max_text_size_kb: DF.Int
		max_validity_hours: DF.Int
		validity_hours: DF.Int
		writes_per_minute_per_ip: DF.Int
	# end: auto-generated types

	_DOCTYPE_NAME = "Clipboard Settings"
