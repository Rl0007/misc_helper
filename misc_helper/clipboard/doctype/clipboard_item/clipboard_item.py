# Copyright (c) 2026, rl0007 and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document


class ClipboardItem(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		clipboard: DF.Link
		content: DF.LongText | None
		file_name: DF.Data | None
		file_size: DF.Int
		file_type: DF.Data | None
		file_url: DF.Data | None
		is_pinned: DF.Check
		item_type: DF.Literal["Text", "Image", "File"]
		sender: DF.Data | None
	# end: auto-generated types

	_DOCTYPE_NAME = "Clipboard Item"
