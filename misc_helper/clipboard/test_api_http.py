# Copyright (c) 2026, rl0007 and Contributors
# See license.txt

import frappe
from frappe.tests.test_api import make_request
from frappe.utils import get_test_client

from misc_helper.clipboard.test_base import ClipboardTestBase

# is_whitelisted sanitises form_dict for Guest callers before the endpoint body runs, so these
# only round-trip over real HTTP if add_text is registered xss_safe. Angle brackets are what
# triggers bleach; an ampersand on its own is never touched.
GUEST_HTTP_PAYLOADS = {
	"ampersand_with_angle_bracket": "http & test <ok> end",
	"script_tag": "<script>alert(1)</script>",
	"indented_code": 'if a < b:\n\tprint("x & y")',
}


class TestGuestHttpRoundTrip(ClipboardTestBase):
	"""The in-process tests call the endpoints directly, which never reaches is_whitelisted and so
	cannot see the guest form_dict sanitiser. These drive the real WSGI app with no cookie.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		# use_cookies=False: no jar, so nothing another test logged in with can leak into ours.
		cls.test_client = get_test_client(use_cookies=False)

	def tearDown(self):
		# The request ran on its own connection and committed. Drop this connection's stale
		# snapshot, or the base class cleanup cannot see the rooms it has to delete.
		frappe.db.rollback()
		super().tearDown()

	def post_method(self, method: str, payload: dict):
		return make_request(
			target=self.test_client.post, args=(f"/api/method/{method}",), kwargs={"json": payload}
		)

	def get_method(self, method: str, params: dict):
		return make_request(
			target=self.test_client.get, args=(f"/api/method/{method}",), kwargs={"query_string": params}
		)

	def add_text_over_http(self, room_name: str, content: str):
		response = self.post_method(
			"misc_helper.clipboard.api.add_text", {"room_name": room_name, "content": content}
		)
		self.assertEqual(response.status_code, 200, response.get_data(as_text=True))

		return response.json["message"]

	def test_request_is_really_a_guest(self):
		"""If this ever authenticates, every assertion in this class becomes worthless: the
		sanitiser only runs for Guest."""
		room_name = self.make_room_name("http")

		item = self.add_text_over_http(room_name, "who is writing this")

		frappe.db.rollback()
		self.assertEqual(frappe.db.get_value("Clipboard Item", item["name"], "owner"), "Guest")
		self.assertEqual(frappe.db.get_value("Clipboard", room_name, "owner"), "Guest")

	def test_guest_pasted_text_survives_the_round_trip(self):
		room_name = self.make_room_name("http")

		for label, payload in GUEST_HTTP_PAYLOADS.items():
			with self.subTest(payload=label):
				item = self.add_text_over_http(room_name, payload)
				self.assertEqual(item["content"], payload)

				response = self.get_method("misc_helper.clipboard.api.get_room", {"room_name": room_name})
				self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
				stored = next(
					entry for entry in response.json["message"]["items"] if entry["name"] == item["name"]
				)
				self.assertEqual(stored["content"], payload)
				self.assertEqual(stored["content"].encode(), payload.encode())
