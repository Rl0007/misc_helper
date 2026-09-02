import frappe

from misc_helper.clipboard.shell import add_shell_context


def get_context(context):
	context.title = frappe._("Clipboard Rooms")
	# templates/clipboard_shell.html renders this on <body>. Kept in the controller rather than
	# hardcoded in the shell because the two pages need different ones: this is also where the
	# reference's base-layer body colours live.
	context.body_class = (
		"cb-page min-h-screen antialiased"
		" bg-surface-alt text-on-surface dark:bg-surface-dark dark:text-on-surface-dark"
	)
	context.no_cache = 1
	add_shell_context(context)
