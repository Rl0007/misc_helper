# Copyright (c) 2026, rl0007 and Contributors
# See license.txt

"""The /devbox slash command for Raven: list, create and manage dev boxes from a channel.

Raven calls `handle` for the typed command and again for every button press. A bare
`/devbox` answers with a menu of buttons; `/devbox <command> ...` runs that command directly.

Connection details come from the `devbox_command` key in site config:

	{
		"site": "https://boxes.example.com",
		"api_key": "...",
		"api_secret": "...",
		"box_owner": "owner@example.com",
		"allowed_users": ["someone@example.com"],
		"default_server": "Prod-01"
	}
"""

import json

import frappe
from frappe import _
from frappe.integrations.utils import make_get_request, make_post_request, make_put_request
from frappe.utils import cint, escape_html, strip_html

BOX_COMMANDS = ("info", "start", "stop", "delete", "protect", "unprotect", "resize", "rename", "password")
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
RESIZE_OPTIONS = {"memory": "memory_gb", "disk": "disk_gb", "cores": "cpus"}
MAX_BUTTONS = 12

HELP = "  \n".join(
	[
		"**/devbox commands**",
		"",
		"`list` · `templates` · `info <box>`",
		"`new <template> [--title my box] [--server Prod-01]`",
		"`profiles` · `provision <profile> [--branch develop] [--title my box] [--server Prod-01]`",
		"`start <box>` · `stop <box>` · `delete <box> confirm`",
		"`protect <box>` · `unprotect <box>`",
		"`resize <box> [--memory 8] [--disk 60] [--cores 4]`",
		"`rename <box> <title>` · `password <box>`",
		"",
		"`<box>` is a slug or an exact title. Send `/devbox` alone for buttons.",
	]
)

MENU_BUTTONS = [
	{"label": "My boxes", "value": "list"},
	{"label": "New box", "value": "new"},
	{"label": "Provision a profile", "value": "provision"},
	{"label": "Templates", "value": "templates"},
	{"label": "Help", "value": "help"},
]
BACK_BUTTON = {"label": "« Menu", "value": "menu"}


class DevboxCommandError(frappe.ValidationError):
	"""A mistake in the command that the person can fix; its message is shown as the reply."""


def handle(interaction) -> dict:
	settings = get_settings()
	if interaction.user not in settings["allowed_users"]:
		return {"text": _("You are not allowed to run /devbox.")}
	devbox = Devbox(settings)
	try:
		if interaction.pressed:
			return devbox.press(interaction.pressed)
		return devbox.run(interaction.arguments)
	except DevboxCommandError as error:
		return {"text": str(error)}
	except Exception as error:
		if not hasattr(error, "response"):
			raise
		return {"text": "Failed: " + escape_html(get_server_message(error))}


def get_settings() -> dict:
	settings = frappe.conf.get("devbox_command") or {}
	missing = [key for key in ("site", "api_key", "api_secret", "box_owner") if not settings.get(key)]
	if missing:
		frappe.throw(
			_("/devbox is not set up: add {0} to devbox_command in site config.").format(", ".join(missing))
		)
	return {
		"site": settings["site"].rstrip("/"),
		"headers": {"Authorization": f"token {settings['api_key']}:{settings['api_secret']}"},
		"box_owner": settings["box_owner"],
		"allowed_users": settings.get("allowed_users") or [],
		"default_server": settings.get("default_server") or "Prod-01",
	}


def get_server_message(error: Exception) -> str:
	"""The readable message the boxes site sent with a failed request, or the error itself."""
	try:
		server_messages = json.loads(error.response.json().get("_server_messages") or "[]")
		if server_messages:
			return strip_html(json.loads(server_messages[0]).get("message") or str(error))
	except Exception:
		pass
	return str(error)


def parse_arguments(arguments: str) -> dict:
	"""Split `info my box --memory 8` into the command, the text after it and the --options."""
	words = (arguments or "").split(None, 1)
	command = words[0].lower() if words else "menu"
	chunks = (" " + (words[1] if len(words) > 1 else "")).split(" --")
	options = {}
	for chunk in chunks[1:]:
		option_name, _separator, option_value = chunk.strip().partition(" ")
		options[option_name] = option_value.strip()
	return {"command": command, "rest": chunks[0].strip(), "options": options}


class Devbox:
	def __init__(self, settings: dict):
		self.site = settings["site"]
		self.headers = settings["headers"]
		self.box_owner = settings["box_owner"]
		self.default_server = settings["default_server"]

	# Requests to the boxes site

	def get_list(
		self, doctype: str, filters: list, fields: list | None = None, order_by: str | None = None
	) -> list:
		params = {"filters": json.dumps(filters), "limit_page_length": 0}
		if fields:
			params["fields"] = json.dumps(fields)
		if order_by:
			params["order_by"] = order_by
		return make_get_request(f"{self.site}/api/resource/{doctype}", headers=self.headers, params=params)[
			"data"
		]

	def call_box_method(self, box_name: str, method: str, arguments: dict | None = None) -> dict:
		payload = {"dt": "Dev Box", "dn": box_name, "method": method}
		if arguments:
			payload["args"] = json.dumps(arguments)
		return make_post_request(f"{self.site}/api/method/run_doc_method", headers=self.headers, json=payload)

	def get_boxes(self) -> list:
		return self.get_list(
			"Dev Box",
			[["owner_user", "=", self.box_owner], ["status", "!=", "deleted"], ["is_pool", "=", 0]],
			BOX_FIELDS,
			"status asc, creation desc",
		)

	def get_box(self, slug_or_title: str) -> dict:
		if not slug_or_title:
			raise DevboxCommandError(_("Name a box: its slug or exact title."))
		boxes = self.get_boxes()
		matches = [box for box in boxes if box["name"] == slug_or_title]
		if not matches:
			matches = [box for box in boxes if (box.get("title") or "").lower() == slug_or_title.lower()]
		if not matches:
			raise DevboxCommandError(_("No box {0} among your boxes.").format(slug_or_title))
		if len(matches) > 1:
			raise DevboxCommandError(
				_("More than one box is titled {0}. Use the slug.").format(slug_or_title)
			)
		return matches[0]

	def get_template_names(self) -> list[str]:
		return [template["name"] for template in self.get_list("DevBox Template", [["enabled", "=", 1]])]

	def get_profiles(self) -> list:
		return self.get_list(
			"DevBox Profile", [["enabled", "=", 1]], ["name", "app_name", "branch", "devbox_template"]
		)

	# Typed commands: /devbox <command> ...

	def run(self, arguments: str) -> dict:
		parsed = parse_arguments(arguments)
		command, rest, options = parsed["command"], parsed["rest"], parsed["options"]
		if command == "menu":
			return self.show_menu()
		if command == "help":
			return {"text": HELP}
		if command == "list":
			return self.list_boxes(with_buttons=False)
		if command == "templates":
			return {"text": "Templates: " + ", ".join(f"`{name}`" for name in self.get_template_names())}
		if command == "profiles":
			return {"text": self.describe_profiles(self.get_profiles())}
		if command == "new":
			if not rest:
				raise DevboxCommandError(_("Name a template. Send /devbox templates to see them."))
			return self.create_box(rest, options)
		if command == "provision":
			if not rest:
				raise DevboxCommandError(_("Name a profile. Send /devbox profiles to see them."))
			return self.provision_profile(rest, options)
		if command == "delete":
			confirmed = rest.endswith(" confirm")
			box = self.get_box(rest[: -len(" confirm")].strip() if confirmed else rest)
			return self.delete_box(box) if confirmed else self.ask_before_delete(box, with_buttons=False)
		if command == "rename":
			slug, _separator, new_title = rest.partition(" ")
			if not new_title.strip():
				raise DevboxCommandError(_("Usage: /devbox rename <slug> <new title>"))
			return self.rename_box(self.get_box(slug), new_title.strip())
		if command == "resize":
			return self.resize_box(self.get_box(rest), options)
		if command in BOX_COMMANDS:
			return self.run_box_action(command, self.get_box(rest), with_buttons=False)
		return {"text": f"Unknown command `{escape_html(command)}`.\n\n{HELP}"}

	# Button presses. A value is an action, or `action:target` for a box, template or profile.

	def press(self, value: str) -> dict:
		action, _separator, target = value.partition(":")
		if action == "menu":
			return self.show_menu()
		if action == "help":
			return {"text": HELP, "buttons": [BACK_BUTTON]}
		if action == "list":
			return self.list_boxes(with_buttons=True)
		if action == "templates":
			names = self.get_template_names()
			return {
				"text": "Templates: " + ", ".join(f"`{name}`" for name in names),
				"buttons": [BACK_BUTTON],
			}
		if action == "new":
			return (
				self.create_box(target, {})
				if target
				else self.pick("template", "new", self.get_template_names())
			)
		if action == "provision":
			if target:
				return self.provision_profile(target, {})
			profiles = self.get_profiles()
			reply = self.pick("profile", "provision", [profile["name"] for profile in profiles])
			reply["text"] = self.describe_profiles(profiles) + "\n\n" + reply["text"]
			return reply
		box = self.get_box(target)
		if action == "delete":
			return self.ask_before_delete(box, with_buttons=True)
		if action == "delete_confirmed":
			return self.delete_box(box)
		return self.run_box_action(action, box, with_buttons=True)

	def show_menu(self) -> dict:
		return {"text": _("**Dev boxes.** What do you want to do?"), "buttons": MENU_BUTTONS}

	def pick(self, kind: str, action: str, names: list[str]) -> dict:
		if not names:
			return {"text": _("No {0} available.").format(kind), "buttons": [BACK_BUTTON]}
		buttons = [{"label": name, "value": f"{action}:{name}"} for name in names[:MAX_BUTTONS]]
		return {"text": _("Pick a {0}:").format(kind), "buttons": [*buttons, BACK_BUTTON]}

	# What each command does

	def list_boxes(self, with_buttons: bool) -> dict:
		boxes = self.get_boxes()
		if not boxes:
			return (
				{"text": _("No boxes."), "buttons": [BACK_BUTTON]}
				if with_buttons
				else {"text": _("No boxes.")}
			)
		reply = {"text": "\n\n".join(self.describe_box(box) for box in boxes)}
		if with_buttons:
			buttons = [
				{"label": box.get("title") or box["name"], "value": f"info:{box['name']}"}
				for box in boxes[:MAX_BUTTONS]
			]
			reply["buttons"] = [*buttons, BACK_BUTTON]
		return reply

	def describe_box(self, box: dict) -> str:
		details = [escape_html(box.get("title") or ""), box.get("status") or ""]
		if box.get("memory_gb"):
			details.append(f"{box['memory_gb']} GB")
		if box.get("prevent_deletion"):
			details.append("protected")
		links = [
			f"[{label}]({box[fieldname]})"
			for fieldname, label in (("site_url", "site"), ("code_url", "code"), ("term_url", "term"))
			if box.get(fieldname)
		]
		lines = [f"**{box['name']}** · " + " · ".join(details)]
		if links:
			lines.append(" · ".join(links))
		return "  \n".join(lines)

	def describe_profiles(self, profiles: list) -> str:
		if not profiles:
			return _("No profiles.")
		return "  \n".join(
			f"`{profile['name']}` · {profile.get('devbox_template') or ''} · branch {profile.get('branch') or 'default'}"
			for profile in profiles
		)

	def create_box(self, template: str, options: dict) -> dict:
		created = make_post_request(
			f"{self.site}/api/resource/Dev Box",
			headers=self.headers,
			json={
				"devbox_template": template,
				"title": options.get("title") or f"{template} dev box",
				"server": options.get("server") or self.default_server,
				"owner_user": self.box_owner,
			},
		)["data"]
		return {
			"text": f"Creating **{created['name']}** from {escape_html(template)}. It takes about a minute.",
			"buttons": [{"label": "Check status", "value": f"info:{created['name']}"}, BACK_BUTTON],
		}

	def provision_profile(self, profile: str, options: dict) -> dict:
		arguments = {
			"profile": profile,
			"server": options.get("server") or self.default_server,
			"owner_user": self.box_owner,
		}
		for option_name in ("branch", "title"):
			if options.get(option_name):
				arguments[option_name] = options[option_name]
		created = make_post_request(
			f"{self.site}/api/method/benchspace.api.agent_box.provision_from_profile",
			headers=self.headers,
			json=arguments,
		)["message"]
		return {
			"text": (
				f"Provisioning **{created['name']}** from profile {escape_html(profile)}. "
				"The app installs after the box is up."
			),
			"buttons": [{"label": "Check status", "value": f"info:{created['name']}"}, BACK_BUTTON],
		}

	def run_box_action(self, action: str, box: dict, with_buttons: bool) -> dict:
		if action == "info":
			reply = {"text": self.describe_box_in_detail(box)}
		elif action in ("start", "stop"):
			self.call_box_method(box["name"], f"{action}_box")
			reply = {"text": ("Starting" if action == "start" else "Stopping") + f" **{box['name']}**."}
		elif action in ("protect", "unprotect"):
			make_put_request(
				f"{self.site}/api/resource/Dev Box/{box['name']}",
				headers=self.headers,
				json={"prevent_deletion": 1 if action == "protect" else 0},
			)
			box["prevent_deletion"] = 1 if action == "protect" else 0
			reply = {"text": ("Protected" if action == "protect" else "Unprotected") + f" **{box['name']}**."}
		elif action == "password":
			password = make_get_request(
				f"{self.site}/api/method/frappe.client.get_password",
				headers=self.headers,
				params={"doctype": "Box Password", "name": self.box_owner, "fieldname": "password"},
			).get("message")
			reply = {"text": f"**{box['name']}** password: `{escape_html(password or '—')}`"}
		else:
			raise DevboxCommandError(_("That option is not available."))
		if with_buttons:
			reply["buttons"] = self.get_box_buttons(box)
		return reply

	def get_box_buttons(self, box: dict) -> list[dict]:
		name = box["name"]
		power = (
			{"label": "Stop", "value": f"stop:{name}"}
			if box.get("status") == "active"
			else {"label": "Start", "value": f"start:{name}"}
		)
		protection = (
			{"label": "Unprotect", "value": f"unprotect:{name}"}
			if box.get("prevent_deletion")
			else {"label": "Protect", "value": f"protect:{name}"}
		)
		return [
			{"label": "Refresh", "value": f"info:{name}"},
			power,
			protection,
			{"label": "Password", "value": f"password:{name}"},
			{"label": "Delete", "value": f"delete:{name}"},
			{"label": "« Boxes", "value": "list"},
		]

	def describe_box_in_detail(self, box: dict) -> str:
		lines = [
			self.describe_box(box),
			f"template: {box.get('devbox_template') or '—'} · server: {box.get('server') or '—'}",
		]
		full_box = make_get_request(f"{self.site}/api/resource/Dev Box/{box['name']}", headers=self.headers)[
			"data"
		]
		if full_box.get("devbox_profile"):
			lines.append(
				f"profile: {full_box['devbox_profile']} ({full_box.get('profile_branch') or 'default branch'})"
				f" · provisioning: {full_box.get('provision_status') or '—'}"
			)
			if full_box.get("provision_error"):
				lines.append("error: " + escape_html(full_box["provision_error"]))
		if box.get("disk_gb"):
			lines.append(f"disk: {box['disk_gb']} GB")
		if box.get("status") == "active":
			stats = self.call_box_method(box["name"], "fetch_stats").get("message") or {}
			usage = [
				f"{key.split('_')[0]} {stats[key]}"
				for key in ("cpu_usage", "memory_usage", "disk_usage")
				if stats.get(key)
			]
			if usage:
				lines.append(", ".join(usage))
		return "  \n".join(lines)

	def ask_before_delete(self, box: dict, with_buttons: bool) -> dict:
		warning = f"This deletes **{box['name']}** and everything on it."
		if not with_buttons:
			return {"text": f"{warning} Send `/devbox delete {box['name']} confirm` to go ahead."}
		return {
			"text": warning,
			"buttons": [
				{"label": "Yes, delete it", "value": f"delete_confirmed:{box['name']}"},
				{"label": "Cancel", "value": f"info:{box['name']}"},
			],
		}

	def delete_box(self, box: dict) -> dict:
		self.call_box_method(box["name"], "delete_box")
		return {"text": f"Deleting **{box['name']}**."}

	def rename_box(self, box: dict, new_title: str) -> dict:
		self.call_box_method(box["name"], "change_title", {"title": new_title})
		return {"text": f"Renamed **{box['name']}** to {escape_html(new_title)}."}

	def resize_box(self, box: dict, options: dict) -> dict:
		arguments = {
			fieldname: cint(options[option])
			for option, fieldname in RESIZE_OPTIONS.items()
			if options.get(option)
		}
		if not arguments:
			raise DevboxCommandError(_("Give --memory, --disk or --cores."))
		self.call_box_method(box["name"], "resize_box", arguments)
		return {"text": f"Resizing **{box['name']}**. A running box restarts when done."}
