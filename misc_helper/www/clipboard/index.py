import frappe


def get_context(context):
	context.title = frappe._("Clipboard Rooms")
	context.body_class = "bg-surface-base"
	context.no_cache = 1
