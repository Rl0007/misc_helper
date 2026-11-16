# Copyright (c) 2026, rl0007 and Contributors
# See license.txt

from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase

from misc_helper.devbox import boxes_site
from misc_helper.devbox.slash_command import get_typed_request, handle

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


class FakeBoxesSiteClient:
	writes: list

	def __init__(self, url: str, api_key: str | None = None, api_secret: str | None = None):
		self.url = url

	def get_list(self, doctype: str, **kwargs) -> list[dict]:
		return {
			"Dev Box": [ACTIVE_BOX, STOPPED_BOX],
			"DevBox Template": [{"name": "Frappe v16"}, {"name": "Frappe Develop"}],
			"DevBox Profile": [
				{"name": "buzz", "app_name": "buzz", "branch": None, "devbox_template": "Frappe v16"}
			],
		}[doctype]

	def get_doc(self, doctype: str, name: str) -> dict:
		return {"name": name}

	def get_api(self, method: str, params: dict) -> str:
		return "s3cret"

	def insert(self, doc: dict) -> dict:
		self.writes.append(("insert", doc))
		return frappe._dict(name="new00001")

	def update(self, doc: dict) -> None:
		self.writes.append(("update", doc))

	def post_api(self, method: str, params: dict) -> dict:
		if params.get("method") == "fetch_stats":
			return {"cpu_usage": "3%", "memory_usage": "2.1 GB"}
		self.writes.append((method, params))
		return {"name": "prov0001"}


class TestGetTypedRequest(UnitTestCase):
	def test_bare_command_opens_the_menu(self):
		self.assertEqual(get_typed_request("").command, "menu")

	def test_command_text_and_options_are_separated(self):
		request = get_typed_request("new Frappe v16 --title my box --server fc-01")
		self.assertEqual((request.command, request.text), ("new", "Frappe v16"))
		self.assertEqual(request.options, {"title": "my box", "server": "fc-01"})

	def test_delete_with_confirm_becomes_the_confirmed_command(self):
		request = get_typed_request("delete My Box confirm")
		self.assertEqual((request.command, request.text), ("delete_confirmed", "My Box"))


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

		self.writes = FakeBoxesSiteClient.writes = []
		patcher = patch.object(boxes_site, "BoxesSiteClient", FakeBoxesSiteClient)
		patcher.start()
		self.addCleanup(patcher.stop)

	def send(self, arguments: str = "", pressed: str | None = None, user: str = ALLOWED_USER) -> dict:
		return handle(SimpleNamespace(user=user, arguments=arguments, pressed=pressed, state={}))

	def button_values(self, reply: dict) -> list[str]:
		return [button["value"] for button in reply.get("buttons", [])]

	def test_other_users_are_refused(self):
		with self.assertRaises(frappe.ValidationError) as raised:
			self.send("list", user="stranger@example.com")
		self.assertIn("not allowed", str(raised.exception))

	def test_missing_settings_are_named(self):
		frappe.conf["devbox_command"] = {"site": SITE}
		with self.assertRaises(frappe.ValidationError) as raised:
			self.send("list")
		self.assertIn("api_key", str(raised.exception))

	def test_bare_command_shows_the_menu_buttons(self):
		self.assertEqual(self.button_values(self.send("")), ["list", "new", "provision", "templates", "help"])

	def test_list_shows_each_box_with_a_button(self):
		reply = self.send("list")
		self.assertIn("**abc12345** · My Box · active · 8 GB", reply["text"])
		self.assertIn("[site](https://abc12345.boxes.test)", reply["text"])
		self.assertIn("protected", reply["text"])
		self.assertEqual(self.button_values(reply), ["info:abc12345", "info:xyz98765", "menu"])

	def test_box_buttons_match_the_box_state(self):
		running = self.button_values(self.send(pressed="info:abc12345"))
		self.assertIn("stop:abc12345", running)
		self.assertIn("protect:abc12345", running)
		stopped = self.button_values(self.send(pressed="info:xyz98765"))
		self.assertIn("start:xyz98765", stopped)
		self.assertIn("unprotect:xyz98765", stopped)

	def test_info_finds_a_box_by_title_and_shows_live_usage(self):
		self.assertIn("cpu 3%, memory 2.1 GB", self.send("info My Box")["text"])

	def test_new_offers_templates_then_creates_the_box(self):
		self.assertEqual(
			self.button_values(self.send(pressed="new")), ["new:Frappe v16", "new:Frappe Develop", "menu"]
		)

		reply = self.send(pressed="new:Frappe Develop")
		action, box = self.writes[0]
		self.assertEqual(action, "insert")
		self.assertEqual(
			(box["devbox_template"], box["server"], box["owner_user"]),
			("Frappe Develop", "Prod-01", "owner@example.com"),
		)
		self.assertIn("info:new00001", self.button_values(reply))

	def test_typed_new_passes_title_and_server(self):
		self.send("new Frappe v16 --title scratch --server fc-01")
		box = self.writes[0][1]
		self.assertEqual((box["title"], box["server"]), ("scratch", "fc-01"))

	def test_provision_passes_the_branch(self):
		self.send("provision buzz --branch develop")
		method, arguments = self.writes[0]
		self.assertEqual(method, "benchspace.api.agent_box.provision_from_profile")
		self.assertEqual((arguments["profile"], arguments["branch"]), ("buzz", "develop"))

	def test_delete_asks_first(self):
		reply = self.send("delete abc12345")
		self.assertEqual(self.button_values(reply), ["delete_confirmed:abc12345", "info:abc12345"])
		self.assertEqual(self.writes, [])

	def test_delete_goes_ahead_once_confirmed(self):
		self.send("delete abc12345 confirm")
		self.send(pressed="delete_confirmed:xyz98765")
		self.assertEqual(
			[arguments for _method, arguments in self.writes],
			[
				{"dt": "Dev Box", "dn": "abc12345", "method": "delete_box"},
				{"dt": "Dev Box", "dn": "xyz98765", "method": "delete_box"},
			],
		)

	def test_protect_updates_the_box(self):
		reply = self.send("protect abc12345")
		self.assertEqual(
			self.writes[0], ("update", {"doctype": "Dev Box", "name": "abc12345", "prevent_deletion": 1})
		)
		self.assertIn("unprotect:abc12345", self.button_values(reply))

	def test_resize_sends_only_the_given_sizes(self):
		self.send("resize abc12345 --memory 16")
		self.assertEqual(self.writes[0][1]["args"], {"memory_gb": 16})

	def test_resize_needs_a_size(self):
		with self.assertRaises(frappe.ValidationError) as raised:
			self.send("resize abc12345")
		self.assertIn("--memory", str(raised.exception))

	def test_rename_needs_a_title(self):
		with self.assertRaises(frappe.ValidationError):
			self.send("rename abc12345")
		self.send("rename abc12345 Fresh Name")
		self.assertEqual(self.writes[0][1]["args"], {"title": "Fresh Name"})

	def test_unknown_box_and_unknown_command_explain_themselves(self):
		with self.assertRaises(frappe.ValidationError) as unknown_box:
			self.send("info nope")
		self.assertIn("No box nope", str(unknown_box.exception))
		with self.assertRaises(frappe.ValidationError) as unknown_command:
			self.send("frappe")
		self.assertIn("Unknown command", str(unknown_command.exception))


class TestBoxesSiteClient(UnitTestCase):
	def test_a_failed_request_shows_the_message_from_the_boxes_site(self):
		response = SimpleNamespace(
			reason="Expectation Failed",
			json=lambda: {
				"exc_type": "ValidationError",
				"_server_messages": '["{\\"message\\": \\"<b>No capacity</b> on Prod-01\\"}"]',
			},
		)
		client = boxes_site.BoxesSiteClient(SITE, api_key="key", api_secret="secret")
		with self.assertRaises(frappe.ValidationError) as raised:
			client.post_process(response)
		self.assertEqual(str(raised.exception), "Failed: No capacity on Prod-01")
