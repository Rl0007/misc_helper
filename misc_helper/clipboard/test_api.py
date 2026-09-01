# Copyright (c) 2026, rl0007 and Contributors
# See license.txt

import os
from unittest.mock import patch

import frappe
from frappe.utils import get_files_path
from frappe.utils.data import add_to_date, cint, get_datetime, now_datetime

from misc_helper.clipboard.api import (
	DEFAULT_TRANSPORT_BYTES,
	JSON_ENVELOPE_BYTES,
	add_file,
	add_text,
	delete_item,
	get_max_file_bytes,
	get_room,
	get_room_validity_hours,
	get_settings,
	set_pinned,
	set_validity,
)
from misc_helper.clipboard.test_base import ClipboardTestBase


class TestRoomLifecycle(ClipboardTestBase):
	def test_room_is_created_lazily_on_first_get_room(self):
		room_name = self.make_room_name()
		self.assertFalse(frappe.db.exists("Clipboard", room_name))

		result = get_room(room_name)

		self.assertEqual(result["room_name"], room_name)
		self.assertTrue(frappe.db.exists("Clipboard", room_name))
		self.assertGreater(get_datetime(result["expires_on"]), now_datetime())
		self.assertEqual(result["items"], [])

	def test_lazy_creation_asks_the_framework_to_commit(self):
		"""get_room is reached over GET, which Frappe does not commit; without the flag the room
		a reader just created is rolled back at the end of the request and never persists."""
		frappe.flags.commit = False
		room_name = self.make_room_name()

		get_room(room_name)
		self.assertTrue(frappe.flags.commit)

		# Reading a room that already exists writes nothing, so it must not force a commit.
		frappe.flags.commit = False
		get_room(room_name)
		self.assertFalse(frappe.flags.commit)

	def test_second_get_room_returns_the_same_room(self):
		room_name = self.make_room_name()
		get_room(room_name)
		created_at = frappe.db.get_value("Clipboard", room_name, "creation")
		add_text(room_name, "written between the two reads")

		result = get_room(room_name)

		# A recreated room would have a later creation stamp and an empty item list.
		self.assertEqual(frappe.db.get_value("Clipboard", room_name, "creation"), created_at)
		self.assertEqual(len(result["items"]), 1)
		self.assertEqual(result["items"][0]["content"], "written between the two reads")

	def test_expired_room_is_recreated_empty(self):
		room_name = self.make_room_name()
		get_room(room_name)
		stale_item = add_text(room_name, "secret from the previous occupant")["name"]
		created_at = frappe.db.get_value("Clipboard", room_name, "creation")

		frappe.db.set_value(
			"Clipboard",
			room_name,
			"expires_on",
			add_to_date(now_datetime(), hours=-1),
			update_modified=False,
		)

		result = get_room(room_name)

		self.assertEqual(result["items"], [])
		self.assertFalse(frappe.db.exists("Clipboard Item", stale_item))
		self.assertEqual(frappe.db.count("Clipboard Item", {"clipboard": room_name}), 0)
		self.assertNotEqual(frappe.db.get_value("Clipboard", room_name, "creation"), created_at)
		self.assertGreater(get_datetime(result["expires_on"]), now_datetime())

	def test_write_slides_the_expiry(self):
		room_name = self.make_room_name()
		get_room(room_name)

		# Still live, but far short of a full validity window, so a slide is unmistakable.
		old_expiry = add_to_date(now_datetime(), minutes=1)
		frappe.db.set_value("Clipboard", room_name, "expires_on", old_expiry, update_modified=False)

		add_text(room_name, "activity")

		new_expiry = get_datetime(frappe.db.get_value("Clipboard", room_name, "expires_on"))
		self.assertGreater(new_expiry, get_datetime(old_expiry))

		validity_hours = cint(get_settings().validity_hours)
		expected_expiry = add_to_date(now_datetime(), hours=validity_hours)
		self.assertLess(abs((new_expiry - expected_expiry).total_seconds()), 60)


class TestValidity(ClipboardTestBase):
	def test_room_inherits_the_site_default_and_can_override_it(self):
		room_name = self.make_room_name()
		with self.temporary_settings(validity_hours=24):
			get_room(room_name)
			self.assertEqual(get_room_validity_hours(room_name), 24)

			set_validity(room_name, 6)
			self.assertEqual(get_room_validity_hours(room_name), 6)

	def test_setting_validity_moves_the_expiry_immediately(self):
		room_name = self.make_room_name()
		get_room(room_name)

		set_validity(room_name, 1)

		expires_on = get_datetime(frappe.db.get_value("Clipboard", room_name, "expires_on"))
		self.assertLess(expires_on, add_to_date(now_datetime(), hours=2))
		self.assertGreater(expires_on, now_datetime())

	def test_one_room_cannot_retune_another(self):
		"""The reason this is per-room rather than a guest setter on Clipboard Settings: rooms
		are public and guessable, so a site-wide setter would hand every stranger the dial."""
		first_room = self.make_room_name("first")
		second_room = self.make_room_name("second")
		get_room(first_room)
		get_room(second_room)

		set_validity(first_room, 1)

		self.assertEqual(get_room_validity_hours(second_room), cint(get_settings().validity_hours))

	def test_validity_outside_the_cap_is_rejected(self):
		room_name = self.make_room_name()
		get_room(room_name)

		with self.temporary_settings(max_validity_hours=48):
			self.assertRaises(frappe.ValidationError, set_validity, room_name, 49)
			self.assertRaises(frappe.ValidationError, set_validity, room_name, 0)
			self.assertRaises(frappe.ValidationError, set_validity, room_name, -1)

	def test_lowering_the_cap_shortens_rooms_already_set_above_it(self):
		"""Clamped on read, not on write -- otherwise a room set to 7 days before the cap
		dropped would stay grandfathered past the new limit forever."""
		room_name = self.make_room_name()
		get_room(room_name)
		set_validity(room_name, 168)

		with self.temporary_settings(max_validity_hours=24):
			self.assertEqual(get_room_validity_hours(room_name), 24)


class TestRoomNameValidation(ClipboardTestBase):
	def test_valid_names_are_accepted(self):
		for room_name in ("rahul-standup", "a1b", "a" * 41):
			with self.subTest(room_name=room_name):
				self.use_room_name(room_name)
				self.assertEqual(get_room(room_name)["room_name"], room_name)

	def test_invalid_names_are_rejected(self):
		for room_name in ("ab", "-nope", "has spaces!", "", "a" * 42, "room/../etc"):
			with self.subTest(room_name=room_name):
				self.assertRaises(frappe.ValidationError, get_room, room_name)

	def test_uppercase_name_is_slugified(self):
		suffix = frappe.generate_hash(length=8)
		self.use_room_name(f"upper-room-{suffix}")

		result = get_room(f"UPPER ROOM {suffix.upper()}")

		self.assertEqual(result["room_name"], f"upper-room-{suffix}")
		self.assertTrue(frappe.db.exists("Clipboard", f"upper-room-{suffix}"))

	def test_weak_names_are_flagged(self):
		for room_name, expected in (("test", True), ("notes", True), ("abc", True), ("rahul-standup", False)):
			with self.subTest(room_name=room_name):
				self.use_room_name(room_name)
				self.assertEqual(get_room(room_name)["is_weak_name"], expected)


class TestAddText(ClipboardTestBase):
	def test_text_item_is_stored_and_returned(self):
		room_name = self.make_room_name()

		item = add_text(room_name, "  paste me  ")

		self.assertEqual(item["item_type"], "Text")
		self.assertEqual(item["content"], "paste me")
		self.assertEqual(frappe.db.get_value("Clipboard Item", item["name"], "clipboard"), room_name)

	def test_items_are_returned_newest_first(self):
		room_name = self.make_room_name()
		add_text(room_name, "oldest")
		add_text(room_name, "middle")
		add_text(room_name, "newest")

		contents = [item["content"] for item in get_room(room_name)["items"]]

		self.assertEqual(contents, ["newest", "middle", "oldest"])

	def test_empty_text_is_rejected(self):
		room_name = self.make_room_name()

		for content in ("", "   ", None):
			with self.subTest(content=content):
				self.assertRaises(frappe.ValidationError, add_text, room_name, content)

		self.assertEqual(frappe.db.count("Clipboard Item", {"clipboard": room_name}), 0)

	def test_text_at_the_size_limit_is_accepted_and_one_byte_over_is_rejected(self):
		room_name = self.make_room_name()

		with self.temporary_settings(max_text_size_kb=1):
			limit_bytes = cint(get_settings().max_text_size_kb) * 1024

			item = add_text(room_name, "a" * limit_bytes)
			self.assertEqual(len(item["content"]), limit_bytes)

			self.assertRaises(frappe.ValidationError, add_text, room_name, "a" * (limit_bytes + 1))

		self.assertEqual(frappe.db.count("Clipboard Item", {"clipboard": room_name}), 1)

	def test_item_cap_per_room_is_enforced(self):
		room_name = self.make_room_name()

		with self.temporary_settings(max_items_per_room=2):
			max_items = cint(get_settings().max_items_per_room)
			for index in range(max_items):
				add_text(room_name, f"item {index}")

			self.assertRaises(frappe.ValidationError, add_text, room_name, "one too many")

		self.assertEqual(frappe.db.count("Clipboard Item", {"clipboard": room_name}), 2)


class TestDeleteItem(ClipboardTestBase):
	def test_own_item_is_deleted(self):
		room_name = self.make_room_name()
		item_name = add_text(room_name, "delete me")["name"]

		delete_item(room_name, item_name)

		self.assertFalse(frappe.db.exists("Clipboard Item", item_name))

	def test_item_from_another_room_is_refused_and_survives(self):
		attacker_room = self.make_room_name("attacker")
		victim_room = self.make_room_name("victim")
		get_room(attacker_room)
		victim_item = add_text(victim_room, "not yours")["name"]

		self.assertRaises(frappe.PermissionError, delete_item, attacker_room, victim_item)

		self.assertTrue(frappe.db.exists("Clipboard Item", victim_item))
		self.assertEqual(frappe.db.get_value("Clipboard Item", victim_item, "content"), "not yours")

	def test_unknown_item_name_is_refused(self):
		room_name = self.make_room_name()
		get_room(room_name)

		self.assertRaises(
			frappe.PermissionError, delete_item, room_name, f"no-such-item-{frappe.generate_hash(length=8)}"
		)


class TestAddFile(ClipboardTestBase):
	def get_stored_file(self, item_name: str) -> str:
		return frappe.db.get_value(
			"File", {"attached_to_doctype": "Clipboard Item", "attached_to_name": item_name}, "name"
		)

	def test_real_png_is_accepted(self):
		room_name = self.make_room_name()
		image_bytes = self.make_image_bytes()

		item = add_file(room_name, "screenshot.png", self.encode(image_bytes))

		self.assertEqual(item["item_type"], "Image")
		self.assertEqual(item["file_size"], len(image_bytes))
		self.assertTrue(item["file_url"].startswith("/files/"))
		self.assertEqual(frappe.db.get_value("Clipboard Item", item["name"], "clipboard"), room_name)

	def test_stored_file_is_public_with_a_hashed_name(self):
		room_name = self.make_room_name()

		item = add_file(room_name, "screenshot.png", self.encode(self.make_image_bytes()))

		stored_file = frappe.db.get_value(
			"File",
			self.get_stored_file(item["name"]),
			["file_name", "is_private", "file_url"],
			as_dict=True,
		)
		self.assertEqual(stored_file.is_private, 0)
		self.assertNotEqual(stored_file.file_name, "screenshot.png")
		self.assertRegex(stored_file.file_name, r"^[a-f0-9]{32}\.png$")
		self.assertNotIn("screenshot", stored_file.file_url)

	def test_path_traversal_file_name_does_not_escape_the_files_directory(self):
		room_name = self.make_room_name()

		item = add_file(room_name, "../../etc/passwd.png", self.encode(self.make_image_bytes()))

		stored_file_name = frappe.db.get_value("File", self.get_stored_file(item["name"]), "file_name")
		self.assertRegex(stored_file_name, r"^[a-f0-9]{32}\.png$")
		self.assertNotIn("passwd", item["file_url"])

		on_disk_path = os.path.realpath(self.get_on_disk_path(item["file_url"]))
		public_files_directory = os.path.realpath(get_files_path(is_private=0))
		self.assertTrue(on_disk_path.startswith(public_files_directory + os.sep), on_disk_path)
		self.assertTrue(os.path.exists(on_disk_path))

		# The display label is sanitised too -- it must never carry a path.
		self.assertNotIn("/", item["file_name"])
		self.assertNotIn("..", item["file_name"])

	def test_non_image_bytes_named_png_are_rejected(self):
		room_name = self.make_room_name()
		payload = self.encode(b"<?php system($_GET['c']); ?>" * 4)

		self.assertRaises(frappe.ValidationError, add_file, room_name, "innocent.png", payload)

		self.assertEqual(frappe.db.count("Clipboard Item", {"clipboard": room_name}), 0)

	def test_empty_and_malformed_payloads_are_rejected(self):
		room_name = self.make_room_name()

		self.assertRaises(frappe.ValidationError, add_file, room_name, "a.png", "")
		self.assertRaises(frappe.ValidationError, add_file, room_name, "a.png", "not base64 !!!")

	def test_size_cap_is_applied_to_the_decoded_bytes(self):
		room_name = self.make_room_name()
		cap_bytes = 2048

		# base64 of 2048 bytes is ~2732 chars, so a cap measured on the encoded string instead of
		# the decoded bytes would reject the at-limit file below.
		with patch("misc_helper.clipboard.api.get_max_file_bytes", return_value=cap_bytes):
			at_limit = self.encode(self.make_image_bytes(cap_bytes))
			self.assertGreater(len(at_limit), cap_bytes, "test payload does not exercise the distinction")

			item = add_file(room_name, "at-limit.png", at_limit)
			self.assertEqual(item["file_size"], cap_bytes)

			over_limit = self.encode(self.make_image_bytes(cap_bytes + 1))
			self.assertRaises(frappe.ValidationError, add_file, room_name, "over.png", over_limit)

		self.assertEqual(frappe.db.count("Clipboard Item", {"clipboard": room_name}), 1)

	def test_max_file_bytes_is_bounded_by_the_transport_cap_not_the_system_setting(self):
		"""Werkzeug rejects an over-long body before this module runs (app.py:206), so raising
		only System Settings.max_file_size would advertise a limit that 413s in practice."""
		with self.change_settings("System Settings", {"max_file_size": 512}):
			max_file_bytes = get_max_file_bytes()

		self.assertEqual(max_file_bytes, (DEFAULT_TRANSPORT_BYTES - JSON_ENVELOPE_BYTES) * 3 // 4)
		self.assertLess(max_file_bytes, 512 * 1024 * 1024)
		# Below the transport cap, because base64 inflates the body by a third.
		self.assertLess(max_file_bytes, DEFAULT_TRANSPORT_BYTES)

	def test_video_is_stored_as_a_file_item_not_an_image(self):
		room_name = self.make_room_name()
		# A minimal ISO base media header: a size-prefixed `ftyp` box is always the first box.
		video_bytes = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64

		item = add_file(room_name, "clip.mp4", self.encode(video_bytes))

		self.assertEqual(item["item_type"], "File")
		self.assertEqual(item["file_type"], "MP4")
		stored_file_name = frappe.db.get_value("File", self.get_stored_file(item["name"]), "file_name")
		self.assertRegex(stored_file_name, r"^[a-f0-9]{32}\.mp4$")

	def test_type_outside_the_allowlist_is_rejected(self):
		"""SVG is the one that matters: these files are served public from this site's own
		origin, so an SVG carrying a script would be stored XSS against every later visitor."""
		room_name = self.make_room_name()
		payload = self.encode(b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>")

		self.assertRaises(frappe.ValidationError, add_file, room_name, "logo.svg", payload)
		self.assertRaises(frappe.ValidationError, add_file, room_name, "shell.php", payload)
		self.assertRaises(frappe.ValidationError, add_file, room_name, "noextension", payload)

		self.assertEqual(frappe.db.count("Clipboard Item", {"clipboard": room_name}), 0)

	def test_allowlist_is_read_from_settings_not_frozen_in_code(self):
		room_name = self.make_room_name()
		payload = self.encode(self.make_image_bytes())

		with self.temporary_settings(allowed_file_types="MP4"):
			self.assertRaises(frappe.ValidationError, add_file, room_name, "shot.png", payload)

		item = add_file(room_name, "shot.png", payload)
		self.assertEqual(item["file_type"], "PNG")

	def test_bytes_must_match_the_extension_they_claim(self):
		"""The extension is what the allowlist checks, so a trusted extension over untrusted
		bytes would be the way around it for every format we can actually recognise."""
		room_name = self.make_room_name()

		self.assertRaises(
			frappe.ValidationError, add_file, room_name, "innocent.mp4", self.encode(b"<html>hi</html>")
		)
		# A real PNG renamed to .mp4 is still a mismatch: it would be served as a video.
		self.assertRaises(
			frappe.ValidationError, add_file, room_name, "shot.mp4", self.encode(self.make_image_bytes())
		)

		self.assertEqual(frappe.db.count("Clipboard Item", {"clipboard": room_name}), 0)

	def test_same_image_in_two_rooms_gets_independent_file_docs(self):
		"""Regression: the File used to be inserted before `attached_to_*` was set, so
		File.validate_duplicate_entry matched on content hash alone and both rooms ended up
		sharing ONE File doc -- deleting either item took the other room's image with it."""
		first_room = self.make_room_name("first")
		second_room = self.make_room_name("second")
		payload = self.encode(self.make_image_bytes())

		first_item = add_file(first_room, "shared.png", payload)
		second_item = add_file(second_room, "shared.png", payload)

		first_file = self.get_stored_file(first_item["name"])
		second_file = self.get_stored_file(second_item["name"])
		self.assertTrue(first_file)
		self.assertTrue(second_file)
		self.assertNotEqual(first_file, second_file)

		delete_item(first_room, first_item["name"])

		self.assertFalse(frappe.db.exists("File", first_file))
		self.assertTrue(frappe.db.exists("File", second_file))

		surviving = get_room(second_room)["items"][0]
		self.assertEqual(surviving["file_url"], second_item["file_url"])
		self.assertTrue(surviving["file_url"])
		surviving_path = self.get_on_disk_path(surviving["file_url"])
		self.assertTrue(os.path.exists(surviving_path), surviving_path)

	def test_deleting_an_image_item_deletes_its_file(self):
		room_name = self.make_room_name()
		item = add_file(room_name, "screenshot.png", self.encode(self.make_image_bytes()))
		file_name = self.get_stored_file(item["name"])
		on_disk_path = self.get_on_disk_path(item["file_url"])
		self.assertTrue(os.path.exists(on_disk_path))

		delete_item(room_name, item["name"])

		self.assertFalse(frappe.db.exists("File", file_name))
		self.assertFalse(os.path.exists(on_disk_path))


class TestSender(ClipboardTestBase):
	"""The sender is an opaque per-browser id used only to pick a side of the chat. It proves
	nothing and grants nothing -- knowing the room name already grants everything (spec §5)."""

	SENDER = "a1b2c3d4e5f60718"

	def test_sender_round_trips_on_both_write_endpoints(self):
		room_name = self.make_room_name()

		text_item = add_text(room_name, "hello", sender=self.SENDER)
		file_item = add_file(room_name, "shot.png", self.encode(self.make_image_bytes()), sender=self.SENDER)

		self.assertEqual(text_item["sender"], self.SENDER)
		self.assertEqual(file_item["sender"], self.SENDER)

	def test_a_malformed_sender_is_dropped_rather_than_rejected(self):
		"""A client that sends nothing usable must still be able to post -- it just does not get
		to claim a side of the chat."""
		room_name = self.make_room_name()

		for bad_sender in (None, "", "not-a-valid-id", "<script>", "z" * 20, "ab"):
			with self.subTest(sender=bad_sender):
				item = add_text(room_name, f"posted with {bad_sender!r}", sender=bad_sender)
				self.assertEqual(item["sender"], "")

	def test_sender_is_normalised_to_lowercase(self):
		room_name = self.make_room_name()

		item = add_text(room_name, "hello", sender=self.SENDER.upper())

		self.assertEqual(item["sender"], self.SENDER)


class TestCaption(ClipboardTestBase):
	def test_file_carries_a_caption(self):
		room_name = self.make_room_name()

		item = add_file(room_name, "shot.png", self.encode(self.make_image_bytes()), content="look at this")

		self.assertEqual(item["content"], "look at this")
		self.assertEqual(item["item_type"], "Image")

	def test_caption_is_optional_and_normalised_to_empty(self):
		room_name = self.make_room_name()

		for caption in (None, "", "   "):
			with self.subTest(caption=caption):
				item = add_file(room_name, "shot.png", self.encode(self.make_image_bytes()), content=caption)
				self.assertEqual(item["content"], "")

	def test_oversized_caption_is_rejected_and_no_file_is_stored(self):
		room_name = self.make_room_name()

		with self.temporary_settings(max_text_size_kb=1):
			self.assertRaises(
				frappe.ValidationError,
				add_file,
				room_name,
				"shot.png",
				self.encode(self.make_image_bytes()),
				"x" * 2048,
			)

		self.assertEqual(frappe.db.count("Clipboard Item", {"clipboard": room_name}), 0)
		self.assertFalse(
			frappe.db.exists("File", {"attached_to_doctype": "Clipboard Item", "attached_to_name": room_name})
		)

	def test_caption_round_trips_byte_for_byte(self):
		"""add_file is xss_safe, so nothing bleaches the caption on the way in and render-time
		escaping is the only defence. This pins the storage half of that bargain."""
		room_name = self.make_room_name()
		caption = "<script>alert(1)</script> & <ok> a < b"

		item = add_file(room_name, "shot.png", self.encode(self.make_image_bytes()), content=caption)

		self.assertEqual(item["content"], caption)
		self.assertEqual(frappe.db.get_value("Clipboard Item", item["name"], "content"), caption)


class TestPinning(ClipboardTestBase):
	def test_pin_and_unpin_round_trip(self):
		room_name = self.create_room()
		item_name = self.create_text_item(room_name)

		set_pinned(room_name, item_name, True)
		self.assertEqual(cint(frappe.db.get_value("Clipboard Item", item_name, "is_pinned")), 1)

		set_pinned(room_name, item_name, False)
		self.assertEqual(cint(frappe.db.get_value("Clipboard Item", item_name, "is_pinned")), 0)

	def test_pinning_does_not_slide_the_room_expiry(self):
		"""A pin is not new content. Letting it slide the expiry would keep a room alive
		indefinitely without anybody adding anything to it."""
		room_name = self.create_room()
		item_name = self.create_text_item(room_name)
		expires_on = frappe.db.get_value("Clipboard", room_name, "expires_on")

		set_pinned(room_name, item_name, True)

		self.assertEqual(frappe.db.get_value("Clipboard", room_name, "expires_on"), expires_on)

	def test_pinning_an_item_from_another_room_is_refused(self):
		first_room = self.create_room(prefix="first")
		second_room = self.create_room(prefix="second")
		item_name = self.create_text_item(second_room)

		self.assertRaises(frappe.PermissionError, set_pinned, first_room, item_name, True)

		self.assertEqual(cint(frappe.db.get_value("Clipboard Item", item_name, "is_pinned")), 0)

	def test_pinned_state_is_returned_by_get_room(self):
		room_name = self.create_room()
		pinned_name = self.create_text_item(room_name, "pinned")
		self.create_text_item(room_name, "ordinary")
		set_pinned(room_name, pinned_name, True)

		items = {item["name"]: item["is_pinned"] for item in get_room(room_name)["items"]}

		self.assertEqual(cint(items[pinned_name]), 1)
		self.assertEqual(sum(cint(value) for value in items.values()), 1)


class TestGuestAccess(ClipboardTestBase):
	def test_guest_can_read_and_write_a_room(self):
		room_name = self.make_room_name()

		with self.set_user("Guest"):
			self.assertEqual(frappe.session.user, "Guest")

			created = get_room(room_name)
			self.assertEqual(created["items"], [])

			item = add_text(room_name, "pasted by a guest")
			self.assertEqual(item["content"], "pasted by a guest")

			reread = get_room(room_name)

		self.assertEqual(len(reread["items"]), 1)
		self.assertEqual(reread["items"][0]["content"], "pasted by a guest")
		self.assertEqual(frappe.db.get_value("Clipboard Item", item["name"], "clipboard"), room_name)

	def test_guest_can_delete_an_item(self):
		room_name = self.make_room_name()

		with self.set_user("Guest"):
			item_name = add_text(room_name, "guest item")["name"]
			delete_item(room_name, item_name)

		self.assertFalse(frappe.db.exists("Clipboard Item", item_name))


# Every payload except "ampersand_only" carries an angle bracket, because
# base_document._sanitize_content only bleaches a value that contains "<" or ">". Those are the
# ones that actually go red if `ignore_xss_filter` is ever dropped from the content field.
RAW_TEXT_PAYLOADS = {
	"ampersand_only": "a & b",
	"ampersand_with_angle_bracket": "a & b < c > d",
	"script_tag": "<script>alert(1)</script>",
	"bold_tag": "<b>bold</b>",
	"anchor_with_quotes": '<a href="/x?a=1&b=2">it\'s "quoted"</a>',
	"emoji": "ship it \U0001f680 \u2705 \u65e5\u672c\u8a9e <3",
	"indented_code": ('def main():\n\tif a < b and c > d:\n\t\tprint("<ok> & done")\n\n    return 0'),
}


class TestRawTextRoundTrip(ClipboardTestBase):
	"""A clipboard that edits what you pasted is broken. Frappe bleaches Long Text containing
	angle brackets unless the field sets `ignore_xss_filter`, which silently deleted script
	blocks and entity-escaped ampersands."""

	def test_content_round_trips_byte_for_byte(self):
		room_name = self.make_room_name()

		for label, payload in RAW_TEXT_PAYLOADS.items():
			with self.subTest(payload=label):
				item = add_text(room_name, payload)

				self.assertEqual(item["content"], payload)
				stored = frappe.db.get_value("Clipboard Item", item["name"], "content")
				self.assertEqual(stored, payload)
				self.assertEqual(stored.encode(), payload.encode())

				read_back = next(
					entry for entry in get_room(room_name)["items"] if entry["name"] == item["name"]
				)
				self.assertEqual(read_back["content"], payload)

				delete_item(room_name, item["name"])

	def test_indented_snippet_keeps_its_whitespace_and_newlines(self):
		room_name = self.make_room_name()
		payload = RAW_TEXT_PAYLOADS["indented_code"]

		get_room(room_name)  # create the room first, so the write path is all this test exercises
		item = add_text(room_name, payload)

		stored = frappe.db.get_value("Clipboard Item", item["name"], "content")
		self.assertEqual(stored.splitlines(), payload.splitlines())
		self.assertEqual(stored.count("\n"), payload.count("\n"))
		self.assertEqual(stored.splitlines()[1], "\tif a < b and c > d:")
		self.assertEqual(stored.splitlines()[4], "    return 0")


class TestWhitelisting(ClipboardTestBase):
	"""The tests above call the endpoints directly, which bypasses the whitelist entirely.
	These assert the registration itself, so a dropped `allow_guest` or a write reachable over
	GET is caught."""

	def test_endpoints_are_reachable_by_a_guest(self):
		for method in (get_room, add_text, add_file, delete_item, set_validity, set_pinned):
			with self.subTest(method=method.__name__), self.set_user("Guest"):
				frappe.is_whitelisted(method)

	def test_writes_are_post_only_and_reads_are_not(self):
		for method in (add_text, add_file, delete_item, set_validity, set_pinned):
			with self.subTest(method=method.__name__):
				self.assertEqual(frappe.allowed_http_methods_for_whitelisted_func[method], ("POST",))

		self.assertIn("GET", frappe.allowed_http_methods_for_whitelisted_func[get_room])

	def test_only_the_free_text_endpoints_are_xss_safe(self):
		"""For a Guest caller is_whitelisted bleaches every string in form_dict before the body
		runs. The two endpoints that store what was typed must opt out; nothing else may, because
		xss_safe makes render-time escaping the only remaining defence."""
		for method in (add_text, add_file):
			with self.subTest(method=method.__name__):
				self.assertIn(method, frappe.xss_safe_methods)

		# Deliberate: none of these takes free text -- room_name is re-derived through a strict
		# filter, item_name is a hash id, the rest are numbers.
		for method in (get_room, delete_item, set_validity, set_pinned):
			with self.subTest(method=method.__name__):
				self.assertNotIn(method, frappe.xss_safe_methods)


class TestGetRoomQueryCount(ClipboardTestBase):
	def test_get_room_does_not_scale_queries_with_item_count(self):
		small_room = self.make_room_name("small")
		large_room = self.make_room_name("large")
		for room_name, item_count in ((small_room, 3), (large_room, 30)):
			get_room(room_name)
			for index in range(item_count):
				self.create_text_item(room_name, f"item {index}")

		# Warm the meta/permission caches so the measurement is of the read path only.
		get_room(small_room)
		get_room(large_room)

		small_room_queries = []
		with self.collect_queries(small_room_queries):
			get_room(small_room)

		large_room_queries = []
		with self.collect_queries(large_room_queries):
			result = get_room(large_room)

		self.assertEqual(len(result["items"]), 30)
		self.assertEqual(
			len(large_room_queries),
			len(small_room_queries),
			msg="get_room scales with item count:\n" + "\n\n".join(large_room_queries),
		)
		self.assertLessEqual(len(large_room_queries), 4, msg="\n\n".join(large_room_queries))
