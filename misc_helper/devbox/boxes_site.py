# Copyright (c) 2026, rl0007 and Contributors
# See license.txt

import frappe
from frappe import _
from frappe.frappeclient import FrappeClient, FrappeException
from frappe.utils import strip_html

BOX_FIELDS = [
	"name",
	"title",
	"status",
	"memory_gb",
	"disk_gb",
	"prevent_deletion",
	"devbox_template",
	"server",
	"site_url",
	"code_url",
	"term_url",
]


class BoxesSiteClient(FrappeClient):
	def post_process(self, response):
		try:
			return super().post_process(response)
		except FrappeException:
			# FrappeClient reports the traceback or nothing at all; the person needs the message the site showed.
			frappe.throw(_("Failed: {0}").format(get_server_message(response)))


def get_server_message(response) -> str:
	error = response.json()
	server_messages = frappe.parse_json(error.get("_server_messages") or "[]")
	if not server_messages:
		return error.get("exc_type") or response.reason
	return strip_html(frappe.parse_json(server_messages[0]).get("message") or "")


class BoxesSite:
	def __init__(self, settings: dict):
		self.client = BoxesSiteClient(settings.site, api_key=settings.api_key, api_secret=settings.api_secret)
		self.box_owner = settings.box_owner
		self.default_server = settings.default_server

	def get_boxes(self) -> list[dict]:
		return self.client.get_list(
			"Dev Box",
			fields=BOX_FIELDS,
			filters=[["owner_user", "=", self.box_owner], ["status", "!=", "deleted"], ["is_pool", "=", 0]],
			order_by="status asc, creation desc",
			limit_page_length=0,
		)

	def get_box(self, slug_or_title: str) -> dict:
		if not slug_or_title:
			frappe.throw(_("Name a box: its slug or exact title."))
		boxes = self.get_boxes()
		matches = [box for box in boxes if box["name"] == slug_or_title] or [
			box for box in boxes if (box.get("title") or "").lower() == slug_or_title.lower()
		]
		if not matches:
			frappe.throw(_("No box {0} among your boxes.").format(slug_or_title))
		if len(matches) > 1:
			frappe.throw(_("More than one box is titled {0}. Use the slug.").format(slug_or_title))
		return matches[0]

	def get_provisioning(self, box_name: str) -> dict:
		return self.client.get_doc("Dev Box", box_name)

	def get_usage(self, box_name: str) -> dict:
		return self.call_box_method(box_name, "fetch_stats") or {}

	def get_template_names(self) -> list[str]:
		templates = self.client.get_list(
			"DevBox Template", filters=[["enabled", "=", 1]], limit_page_length=0
		)
		return [template["name"] for template in templates]

	def get_profiles(self) -> list[dict]:
		return self.client.get_list(
			"DevBox Profile",
			fields=["name", "app_name", "branch", "devbox_template"],
			filters=[["enabled", "=", 1]],
			limit_page_length=0,
		)

	def get_password(self) -> str | None:
		return self.client.get_api(
			"frappe.client.get_password",
			{"doctype": "Box Password", "name": self.box_owner, "fieldname": "password"},
		)

	def create_box(self, template: str, options: dict) -> str:
		box = self.client.insert(
			{
				"doctype": "Dev Box",
				"devbox_template": template,
				"title": options.get("title") or f"{template} dev box",
				"server": options.get("server") or self.default_server,
				"owner_user": self.box_owner,
			}
		)
		return box.name

	def provision_profile(self, profile: str, options: dict) -> str:
		arguments = {
			"profile": profile,
			"server": options.get("server") or self.default_server,
			"owner_user": self.box_owner,
			**{option: options[option] for option in ("branch", "title") if options.get(option)},
		}
		return self.client.post_api("benchspace.api.agent_box.provision_from_profile", arguments)["name"]

	def set_protection(self, box_name: str, is_protected: bool) -> None:
		self.client.update({"doctype": "Dev Box", "name": box_name, "prevent_deletion": int(is_protected)})

	def call_box_method(self, box_name: str, method: str, arguments: dict | None = None):
		params = {"dt": "Dev Box", "dn": box_name, "method": method}
		if arguments:
			params["args"] = arguments
		return self.client.post_api("run_doc_method", params)
