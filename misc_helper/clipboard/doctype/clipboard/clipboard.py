# Copyright (c) 2026, rl0007 and contributors
# For license information, please see license.txt

from frappe.model.document import Document

from misc_helper.clipboard.api import get_valid_room_name


class Clipboard(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		expires_on: DF.Datetime | None
		last_activity: DF.Datetime | None
		room_name: DF.Data
		validity_hours: DF.Int
	# end: auto-generated types

	_DOCTYPE_NAME = "Clipboard"

	def before_naming(self):
		# Must run before naming, not in validate: autoname is `field:room_name`, so the raw value
		# becomes the doc name and _sync_autoname_field then writes that name back over the field.
		self.room_name = get_valid_room_name(self.room_name)
