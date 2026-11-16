# Copyright (c) 2026, rl0007 and Contributors
# See license.txt

"""The /devbox slash command for Raven. Setup: the `devbox_command` key in site config holds
site, api_key, api_secret, box_owner, allowed_users and (optionally) default_server."""

from functools import partial

import frappe
from frappe import _
from frappe.utils import cint, escape_html

from misc_helper.devbox.boxes_site import BoxesSite

REQUIRED_SETTINGS = ("site", "api_key", "api_secret", "box_owner")
RESIZE_OPTIONS = {"memory": "memory_gb", "disk": "disk_gb", "cores": "cpus"}
BOX_LINKS = {"site_url": "site", "code_url": "code", "term_url": "term"}
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
MENU_BUTTON = {"label": "« Menu", "value": "menu"}


def handle(interaction) -> dict:
	settings = get_settings()
	if interaction.user not in settings.allowed_users:
		frappe.throw(_("You are not allowed to run /devbox."))
	request = (
		get_pressed_request(interaction.pressed)
		if interaction.pressed
		else get_typed_request(interaction.arguments)
	)
	return DevboxCommand(BoxesSite(settings)).run(request)


def get_settings() -> dict:
	settings = frappe._dict(frappe.conf.get("devbox_command") or {})
	missing = [key for key in REQUIRED_SETTINGS if not settings.get(key)]
	if missing:
		frappe.throw(
			_("/devbox is not set up: add {0} to devbox_command in site config.").format(", ".join(missing))
		)
	settings.allowed_users = settings.allowed_users or []
	settings.default_server = settings.default_server or "Prod-01"
	return settings


def get_typed_request(arguments: str) -> dict:
	command, _separator, remainder = (arguments or "").strip().partition(" ")
	text, *option_chunks = f" {remainder}".split(" --")
	options = {}
	for chunk in option_chunks:
		option, _separator, value = chunk.strip().partition(" ")
		options[option] = value.strip()
	text = text.strip()
	if command.lower() == "delete" and text.endswith(" confirm"):
		command, text = "delete_confirmed", text.removesuffix(" confirm").strip()
	return frappe._dict(command=command.lower() or "menu", text=text, options=options)


def get_pressed_request(value: str) -> dict:
	command, _separator, text = value.partition(":")
	return frappe._dict(command=command, text=text, options={})


class DevboxCommand:
	def __init__(self, boxes_site: BoxesSite):
		self.boxes_site = boxes_site

	def run(self, request: dict) -> dict:
		commands = {
			"menu": self.get_menu,
			"help": self.get_help,
			"list": self.get_box_list,
			"templates": self.get_templates,
			"profiles": self.get_profiles,
			"new": self.create_box,
			"provision": self.provision_profile,
			"info": self.get_box_details,
			"start": partial(self.set_box_power, "start_box", "Starting"),
			"stop": partial(self.set_box_power, "stop_box", "Stopping"),
			"protect": partial(self.set_box_protection, True),
			"unprotect": partial(self.set_box_protection, False),
			"password": self.get_box_password,
			"delete": self.get_delete_confirmation,
			"delete_confirmed": self.remove_box,
			"rename": self.rename_box,
			"resize": self.resize_box,
		}
		if request.command not in commands:
			frappe.throw(f"Unknown command `{escape_html(request.command)}`.\n\n{HELP}")
		return commands[request.command](request)

	def get_menu(self, request: dict) -> dict:
		return {"text": _("**Dev boxes.** What do you want to do?"), "buttons": MENU_BUTTONS}

	def get_help(self, request: dict) -> dict:
		return {"text": HELP, "buttons": [MENU_BUTTON]}

	def get_box_list(self, request: dict) -> dict:
		boxes = self.boxes_site.get_boxes()
		buttons = [
			{"label": box.get("title") or box["name"], "value": f"info:{box['name']}"}
			for box in boxes[:MAX_BUTTONS]
		]
		text = "\n\n".join(format_box(box) for box in boxes) or _("No boxes.")
		return {"text": text, "buttons": [*buttons, MENU_BUTTON]}

	def get_templates(self, request: dict) -> dict:
		names = ", ".join(f"`{name}`" for name in self.boxes_site.get_template_names())
		return {"text": f"Templates: {names}", "buttons": [MENU_BUTTON]}

	def get_profiles(self, request: dict) -> dict:
		return {"text": format_profiles(self.boxes_site.get_profiles()), "buttons": [MENU_BUTTON]}

	def create_box(self, request: dict) -> dict:
		if not request.text:
			return get_choices(_("Pick a template:"), "new", self.boxes_site.get_template_names())
		box_name = self.boxes_site.create_box(request.text, request.options)
		text = f"Creating **{box_name}** from {escape_html(request.text)}. It takes about a minute."
		return {"text": text, "buttons": get_new_box_buttons(box_name)}

	def provision_profile(self, request: dict) -> dict:
		if not request.text:
			profiles = self.boxes_site.get_profiles()
			return get_choices(
				format_profiles(profiles), "provision", [profile["name"] for profile in profiles]
			)
		box_name = self.boxes_site.provision_profile(request.text, request.options)
		text = f"Provisioning **{box_name}** from profile {escape_html(request.text)}. The app installs after the box is up."
		return {"text": text, "buttons": get_new_box_buttons(box_name)}

	def get_box_details(self, request: dict) -> dict:
		box = self.boxes_site.get_box(request.text)
		provisioning = self.boxes_site.get_provisioning(box["name"])
		usage = self.boxes_site.get_usage(box["name"]) if box.get("status") == "active" else {}
		return get_box_reply(box, format_box_details(box, provisioning, usage))

	def set_box_power(self, method: str, progress: str, request: dict) -> dict:
		box = self.boxes_site.get_box(request.text)
		self.boxes_site.call_box_method(box["name"], method)
		return get_box_reply(box, f"{progress} **{box['name']}**.")

	def set_box_protection(self, is_protected: bool, request: dict) -> dict:
		box = self.boxes_site.get_box(request.text)
		self.boxes_site.set_protection(box["name"], is_protected)
		outcome = "Protected" if is_protected else "Unprotected"
		return get_box_reply({**box, "prevent_deletion": int(is_protected)}, f"{outcome} **{box['name']}**.")

	def get_box_password(self, request: dict) -> dict:
		box = self.boxes_site.get_box(request.text)
		password = escape_html(self.boxes_site.get_password() or "—")
		return get_box_reply(box, f"**{box['name']}** password: `{password}`")

	def get_delete_confirmation(self, request: dict) -> dict:
		box = self.boxes_site.get_box(request.text)
		buttons = [
			{"label": "Yes, delete it", "value": f"delete_confirmed:{box['name']}"},
			{"label": "Cancel", "value": f"info:{box['name']}"},
		]
		text = (
			f"This deletes **{box['name']}** and everything on it. "
			f"Tap the button, or send `/devbox delete {box['name']} confirm`."
		)
		return {"text": text, "buttons": buttons}

	def remove_box(self, request: dict) -> dict:
		box = self.boxes_site.get_box(request.text)
		self.boxes_site.call_box_method(box["name"], "delete_box")
		return {"text": f"Deleting **{box['name']}**."}

	def rename_box(self, request: dict) -> dict:
		slug, _separator, title = request.text.partition(" ")
		if not title.strip():
			frappe.throw(_("Usage: `/devbox rename <slug> <new title>`"))
		box = self.boxes_site.get_box(slug)
		self.boxes_site.call_box_method(box["name"], "change_title", {"title": title.strip()})
		return {"text": f"Renamed **{box['name']}** to {escape_html(title.strip())}."}

	def resize_box(self, request: dict) -> dict:
		sizes = {
			fieldname: cint(request.options[option])
			for option, fieldname in RESIZE_OPTIONS.items()
			if request.options.get(option)
		}
		if not sizes:
			frappe.throw(_("Give --memory, --disk or --cores."))
		box = self.boxes_site.get_box(request.text)
		self.boxes_site.call_box_method(box["name"], "resize_box", sizes)
		return {"text": f"Resizing **{box['name']}**. A running box restarts when done."}


def get_choices(text: str, command: str, names: list[str]) -> dict:
	buttons = [{"label": name, "value": f"{command}:{name}"} for name in names[:MAX_BUTTONS]]
	return {"text": text, "buttons": [*buttons, MENU_BUTTON]}


def get_new_box_buttons(box_name: str) -> list[dict]:
	return [{"label": "Check status", "value": f"info:{box_name}"}, MENU_BUTTON]


def get_box_reply(box: dict, text: str) -> dict:
	name = box["name"]
	is_running = box.get("status") == "active"
	is_protected = box.get("prevent_deletion")
	buttons = [
		{"label": "Refresh", "value": f"info:{name}"},
		{"label": "Stop", "value": f"stop:{name}"}
		if is_running
		else {"label": "Start", "value": f"start:{name}"},
		{"label": "Unprotect", "value": f"unprotect:{name}"}
		if is_protected
		else {"label": "Protect", "value": f"protect:{name}"},
		{"label": "Password", "value": f"password:{name}"},
		{"label": "Delete", "value": f"delete:{name}"},
		{"label": "« Boxes", "value": "list"},
	]
	return {"text": text, "buttons": buttons}


def format_box(box: dict) -> str:
	details = [f"**{box['name']}**", escape_html(box.get("title") or ""), box.get("status") or ""]
	if box.get("memory_gb"):
		details.append(f"{box['memory_gb']} GB")
	if box.get("prevent_deletion"):
		details.append("protected")
	links = [f"[{label}]({box[fieldname]})" for fieldname, label in BOX_LINKS.items() if box.get(fieldname)]
	return "  \n".join(filter(None, [" · ".join(details), " · ".join(links)]))


def format_box_details(box: dict, provisioning: dict, usage: dict) -> str:
	lines = [
		format_box(box),
		f"template: {box.get('devbox_template') or '—'} · server: {box.get('server') or '—'}",
	]
	if provisioning.get("devbox_profile"):
		branch = provisioning.get("profile_branch") or "default branch"
		status = provisioning.get("provision_status") or "—"
		lines.append(f"profile: {provisioning['devbox_profile']} ({branch}) · provisioning: {status}")
	if provisioning.get("provision_error"):
		lines.append("error: " + escape_html(provisioning["provision_error"]))
	if box.get("disk_gb"):
		lines.append(f"disk: {box['disk_gb']} GB")
	usage_parts = [
		f"{key.removesuffix('_usage')} {usage[key]}"
		for key in ("cpu_usage", "memory_usage", "disk_usage")
		if usage.get(key)
	]
	return "  \n".join(filter(None, [*lines, ", ".join(usage_parts)]))


def format_profiles(profiles: list[dict]) -> str:
	if not profiles:
		return _("No profiles.")
	return "  \n".join(
		f"`{profile['name']}` · {profile.get('devbox_template') or ''} · branch {profile.get('branch') or 'default'}"
		for profile in profiles
	)
