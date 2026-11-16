# Copyright (c) 2026, rl0007 and Contributors
# See license.txt

from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase

from misc_helper.devbox import slash_command
from misc_helper.devbox.slash_command import handle, parse_arguments

SITE = "https://boxes.test"
ALLOWED_USER = "rahul@example.com"

ACTIVE_BOX = {
	"name": "abc12345",
	"title": "My Box",
	"status": "active",
	"memory_gb": 8,
	"disk_gb": 60,
	"prevent_deletion": 0,
	"devbox_template": "Frappe v16",
	"server": "Prod-01",
	"site_url": "https://abc12345.boxes.test",
	"code_url": None,
	"term_url": None,
}
STOPPED_BOX = {
	**ACTIVE_BOX,
	"name": "xyz98765",
	"title": "Old Box",
	"status": "stopped",
	"prevent_deletion": 1,
}


class FakeBoxesSite:
	"""Stands in for the boxes site: answers reads from fixed data and records every write."""

	def __init__(self):
		self.writes = []

	def get(self, url: str, **kwargs) -> dict:
		path = url.removeprefix(SITE)
		if path == "/api/resource/Dev Box":
			return {"data": [ACTIVE_BOX, STOPPED_BOX]}
		if path.startswith("/api/resource/Dev Box/"):
			return {"data": {"name": path.rsplit("/", 1)[1]}}
		if path == "/api/resource/DevBox Template":
			return {"data": [{"name": "Frappe v16"}, {"name": "Frappe Develop"}]}
		if path == "/api/resource/DevBox Profile":
			return {
				"data": [
					{"name": "buzz", "app_name": "buzz", "branch": None, "devbox_template": "Frappe v16"}
				]
			}
		if path == "/api/method/frappe.client.get_password":
			return {"message": "s3cret"}
		raise AssertionError(f"unexpected GET {path}")

	def post(self, url: str, **kwargs) -> dict:
		path = url.removeprefix(SITE)
		payload = kwargs.get("json") or {}
		if path == "/api/method/run_doc_method" and payload.get("method") == "fetch_stats":
			return {"message": {"cpu_usage": "3%", "memory_usage": "2.1 GB"}}
		self.writes.append((path, payload))
		if path == "/api/resource/Dev Box":
			return {"data": {"name": "new00001"}}
		if path == "/api/method/benchspace.api.agent_box.provision_from_profile":
			return {"message": {"name": "prov0001"}}
		return {}

	def put(self, url: str, **kwargs) -> dict:
		self.writes.append((url.removeprefix(SITE), kwargs.get("json") or {}))
		return {}


class TestParseArguments(UnitTestCase):
	def test_bare_command_opens_the_menu(self):
		self.assertEqual(parse_arguments("")["command"], "menu")

	def test_command_text_and_options_are_separated(self):
		parsed = parse_arguments("new Frappe v16 --title my box --server fc-01")
		self.assertEqual(parsed["command"], "new")
		self.assertEqual(parsed["rest"], "Frappe v16")
		self.assertEqual(parsed["options"], {"title": "my box", "server": "fc-01"})


class TestDevboxSlashCommand(UnitTestCase):
	def setUp(self):
		super().setUp()
		previous_settings = frappe.conf.get("devbox_command")
		frappe.conf["devbox_command"] = {
			"site": SITE,
			"api_key": "key",
			"api_secret": "secret",
			"box_owner": "owner@example.com",
			"allowed_users": [ALLOWED_USER],
		}
		self.addCleanup(frappe.conf.__setitem__, "devbox_command", previous_settings)

		self.boxes_site = FakeBoxesSite()
		for request_function, fake in (
			("make_get_request", self.boxes_site.get),
			("make_post_request", self.boxes_site.post),
			("make_put_request", self.boxes_site.put),
		):
			patcher = patch.object(slash_command, request_function, side_effect=fake)
			patcher.start()
			self.addCleanup(patcher.stop)

	def send(self, arguments: str = "", pressed: str | None = None, user: str = ALLOWED_USER) -> dict:
		return handle(SimpleNamespace(user=user, arguments=arguments, pressed=pressed, state={}))

	def button_values(self, reply: dict) -> list[str]:
		return [button["value"] for button in reply.get("buttons", [])]

	def test_other_users_are_refused_before_anything_is_fetched(self):
		reply = self.send("list", user="stranger@example.com")
		self.assertIn("not allowed", reply["text"])
		slash_command.make_get_request.assert_not_called()

	def test_missing_settings_are_named(self):
		frappe.conf["devbox_command"] = {"site": SITE}
		with self.assertRaises(frappe.ValidationError) as raised:
			self.send("list")
		self.assertIn("api_key", str(raised.exception))

	def test_bare_command_shows_the_menu_buttons(self):
		reply = self.send("")
		self.assertEqual(self.button_values(reply), ["list", "new", "provision", "templates", "help"])

	def test_typed_list_shows_boxes_without_buttons(self):
		reply = self.send("list")
		self.assertIn("**abc12345** · My Box · active · 8 GB", reply["text"])
		self.assertIn("[site](https://abc12345.boxes.test)", reply["text"])
		self.assertIn("protected", reply["text"])
		self.assertNotIn("buttons", reply)

	def test_list_button_offers_each_box(self):
		reply = self.send(pressed="list")
		self.assertEqual(self.button_values(reply), ["info:abc12345", "info:xyz98765", "menu"])

	def test_box_buttons_match_the_box_state(self):
		running = self.button_values(self.send(pressed="info:abc12345"))
		self.assertIn("stop:abc12345", running)
		self.assertIn("protect:abc12345", running)
		stopped = self.button_values(self.send(pressed="info:xyz98765"))
		self.assertIn("start:xyz98765", stopped)
		self.assertIn("unprotect:xyz98765", stopped)

	def test_info_shows_live_usage_for_a_running_box(self):
		self.assertIn("cpu 3%, memory 2.1 GB", self.send("info My Box")["text"])

	def test_new_button_lists_templates_then_creates_the_box(self):
		self.assertEqual(
			self.button_values(self.send(pressed="new")), ["new:Frappe v16", "new:Frappe Develop", "menu"]
		)

		reply = self.send(pressed="new:Frappe Develop")
		path, payload = self.boxes_site.writes[0]
		self.assertEqual(path, "/api/resource/Dev Box")
		self.assertEqual(payload["devbox_template"], "Frappe Develop")
		self.assertEqual(payload["server"], "Prod-01")
		self.assertEqual(payload["owner_user"], "owner@example.com")
		self.assertIn("info:new00001", self.button_values(reply))

	def test_typed_new_passes_title_and_server(self):
		self.send("new Frappe v16 --title scratch --server fc-01")
		payload = self.boxes_site.writes[0][1]
		self.assertEqual((payload["title"], payload["server"]), ("scratch", "fc-01"))

	def test_provision_passes_the_branch(self):
		self.send("provision buzz --branch develop")
		path, payload = self.boxes_site.writes[0]
		self.assertEqual(path, "/api/method/benchspace.api.agent_box.provision_from_profile")
		self.assertEqual((payload["profile"], payload["branch"]), ("buzz", "develop"))

	def test_typed_delete_needs_the_word_confirm(self):
		reply = self.send("delete abc12345")
		self.assertIn("/devbox delete abc12345 confirm", reply["text"])
		self.assertEqual(self.boxes_site.writes, [])

		self.send("delete abc12345 confirm")
		self.assertEqual(self.boxes_site.writes[0][1]["method"], "delete_box")

	def test_delete_button_asks_first(self):
		reply = self.send(pressed="delete:abc12345")
		self.assertEqual(self.button_values(reply), ["delete_confirmed:abc12345", "info:abc12345"])
		self.assertEqual(self.boxes_site.writes, [])

		self.send(pressed="delete_confirmed:abc12345")
		self.assertEqual(
			self.boxes_site.writes[0][1], {"dt": "Dev Box", "dn": "abc12345", "method": "delete_box"}
		)

	def test_protect_updates_the_box(self):
		self.send("protect abc12345")
		self.assertEqual(
			self.boxes_site.writes[0], ("/api/resource/Dev Box/abc12345", {"prevent_deletion": 1})
		)

	def test_resize_sends_only_the_given_sizes(self):
		self.send("resize abc12345 --memory 16")
		self.assertEqual(self.boxes_site.writes[0][1]["args"], '{"memory_gb": 16}')
		self.assertIn("--memory", self.send("resize abc12345")["text"])

	def test_rename_needs_a_title(self):
		self.assertIn("Usage", self.send("rename abc12345")["text"])
		self.send("rename abc12345 Fresh Name")
		self.assertEqual(self.boxes_site.writes[0][1]["args"], '{"title": "Fresh Name"}')

	def test_unknown_box_and_unknown_command_explain_themselves(self):
		self.assertIn("No box nope", self.send("info nope")["text"])
		self.assertIn("Unknown command", self.send("frappe")["text"])

	def test_a_failed_request_shows_the_server_message(self):
		response = SimpleNamespace(
			json=lambda: {"_server_messages": '["{\\"message\\": \\"<b>No capacity</b>\\"}"]'}
		)
		error = Exception("500 Server Error")
		error.response = response
		with patch.object(slash_command, "make_post_request", side_effect=error):
			self.assertEqual(self.send("start abc12345")["text"], "Failed: No capacity")
