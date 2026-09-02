# Copyright (c) 2026, rl0007 and contributors
# For license information, please see license.txt

import frappe
from frappe.sessions import get_csrf_token

# Every asset the shell loads is versioned from this one number, because a Tailwind-built
# stylesheet and a raw .js get no content hash from bundled_asset() -- that only fingerprints real
# *.bundle.* files. Browsers will otherwise serve a stale copy across deploys indefinitely, and a
# stale stylesheet is not cosmetic here: the cascade fixes live in it. BUMP THIS on any change to
# clipboard.css, clipboard_room.js or clipboard_realtime.js.
ASSET_VERSION = 24


def add_shell_context(context) -> None:
	"""Give templates/clipboard_shell.html what it needs in place of frappe's boot.

	The clipboard pages do not extend templates/base.html, so none of frappe's website bundle is
	present -- see the comment at the top of the shell for why that is deliberate. These three
	values are everything those pages actually used it for.
	"""
	context.boot = get_boot()
	context.asset_version = ASSET_VERSION


def get_boot() -> dict:
	return {
		"sitename": frappe.local.site,
		"socketio_port": frappe.conf.socketio_port,
		# Behind nginx the socket is proxied on the site's own origin and needs no port; only a dev
		# bench serves it separately. frappe's own socketio_client get_host() keys off the same flag.
		"dev_server": bool(frappe.conf.developer_mode),
		# Empty for a Guest whose session carries no token, in which case auth.py:83
		# validate_csrf_token returns early. A logged-in visitor DOES need it, or every write is
		# rejected as "Invalid Request" -- which is invisible until someone tests while signed in.
		"csrf_token": get_csrf_token(),
	}
