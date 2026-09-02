# Copyright (c) 2026, rl0007 and Contributors
# See license.txt

import base64
from contextlib import contextmanager

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import get_files_path
from frappe.utils.data import add_to_date, now_datetime

from misc_helper.clipboard.api import get_settings
from misc_helper.clipboard.cleanup import delete_room

# A real 1x1 transparent PNG. Anything shorter is not a PNG a browser would ever hand us.
TINY_PNG_BYTES = base64.b64decode(
	"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class ClipboardTestBase(IntegrationTestCase):
	"""Shared fixtures for the Clipboard suites.

	`api.get_active_room` commits, so the per-class rollback cannot undo the rooms these tests
	create. Every room is therefore tracked and deleted explicitly in tearDown.
	"""

	def setUp(self):
		super().setUp()
		self.rooms_to_clean = []

	def tearDown(self):
		# get_active_room sets this to make the framework commit a room created on a GET; left
		# set it would leak into whatever test runs next.
		frappe.flags.commit = False
		for room_name in self.rooms_to_clean:
			if frappe.db.exists("Clipboard", room_name):
				delete_room(room_name)
		frappe.db.commit()  # nosemgrep: frappe-manual-commit -- cleanup must outlive the rollback
		super().tearDown()

	def make_room_name(self, prefix: str = "room") -> str:
		"""Unique per call: rollback is per class, so two tests would otherwise share a room."""
		room_name = f"{prefix}-{frappe.generate_hash(length=8)}"
		self.rooms_to_clean.append(room_name)
		return room_name

	def use_room_name(self, room_name: str) -> str:
		"""Adopt a fixed name, but only clean up a room this test actually created -- these
		names are short and generic, and a real room of that name may already exist."""
		if not frappe.db.exists("Clipboard", room_name):
			self.rooms_to_clean.append(room_name)

		return room_name

	def create_room(self, expires_on=None, prefix: str = "room") -> str:
		room_name = self.make_room_name(prefix)
		room = frappe.new_doc("Clipboard")
		room.room_name = room_name
		room.expires_on = expires_on or add_to_date(now_datetime(), hours=get_settings().validity_hours)
		room.last_activity = now_datetime()
		room.insert(ignore_permissions=True)

		return room_name

	def create_text_item(self, room_name: str, content: str = "hello") -> str:
		item = frappe.new_doc("Clipboard Item")
		item.clipboard = room_name
		item.item_type = "Text"
		item.content = content
		item.insert(ignore_permissions=True)

		return item.name

	def make_image_bytes(self, size: int | None = None) -> bytes:
		"""A PNG of an exact byte length, unique per call.

		Unique bytes matter: File.validate_duplicate_entry reuses an existing File when the
		content hash matches, which would make two items share one blob.
		"""
		image_bytes = TINY_PNG_BYTES + frappe.generate_hash(length=16).encode()
		if size is None:
			return image_bytes

		if size < len(image_bytes):
			raise ValueError(f"cannot build a PNG smaller than {len(image_bytes)} bytes")

		return image_bytes + b"\x00" * (size - len(image_bytes))

	def encode(self, image_bytes: bytes) -> str:
		return base64.b64encode(image_bytes).decode()

	def get_on_disk_path(self, file_url: str) -> str:
		"""Resolve what is actually served. File.file_name is NOT the on-disk name once
		File.save_file dedupes identical bytes onto an existing blob and rewrites file_url.
		"""
		return get_files_path(file_url.rsplit("/", 1)[-1], is_private=0)

	@contextmanager
	def temporary_settings(self, **values):
		"""Change Clipboard Settings for the block and always put them back.

		IntegrationTestCase.change_settings has no try/finally, so a failing assertion inside
		its block leaks the changed setting into the site -- and api.get_active_room commits,
		so rollback would not undo it either.
		"""
		settings = frappe.get_doc("Clipboard Settings")
		previous_values = {key: settings.get(key) for key in values}
		try:
			settings.update(values).save(ignore_permissions=True)
			frappe.db.commit()  # nosemgrep: frappe-manual-commit -- settings must outlive the rollback
			yield
		finally:
			frappe.get_doc("Clipboard Settings").update(previous_values).save(ignore_permissions=True)
			frappe.db.commit()  # nosemgrep: frappe-manual-commit -- restore must outlive the rollback

	@contextmanager
	def collect_queries(self, queries: list):
		"""Record every query run in the block. Mirrors IntegrationTestCase.assertQueryCount,
		which can only assert an upper bound and so cannot prove a count does not scale.
		"""
		original_sql = frappe.db.__class__.sql

		def sql_with_capture(*args, **kwargs):
			result = original_sql(*args, **kwargs)
			queries.append(str(args[0].last_query))
			return result

		frappe.db.__class__.sql = sql_with_capture
		try:
			yield
		finally:
			frappe.db.__class__.sql = original_sql
