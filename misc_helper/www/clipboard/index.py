import frappe


def get_context(context):
	context.title = frappe._("Clipboard Rooms")
	context.body_class = "bg-surface-base"
	context.no_cache = 1
	context.no_sidebar = 1
	context.no_breadcrumbs = 1
