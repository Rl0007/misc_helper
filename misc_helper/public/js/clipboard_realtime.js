// Realtime for a standalone clipboard page -- socket.io wired by hand, with no dependency on
// frappe-web.bundle.js.
//
// The page it serves does not load Frappe's website bundle at all (that bundle is 692 KB of
// Bootstrap whose .bg-primary / .text-sm / .hidden rules fight this design system), and
// frappe.realtime lives inside it. Everything below is the ~30 lines of that bundle we actually
// used, reproduced against the same server contract.
//
// The contract, from frappe/realtime/middlewares/authenticate.js and handlers.js:
//   - the namespace MUST be the site name, or the server rejects with "Invalid namespace"
//   - the request's Origin hostname must match its Host hostname, or "Invalid origin"
//   - a `sid` cookie is required; a Guest visitor already has one, which is why this works with
//     no login (spec §5: the room name is the only credential)
//   - every socket, Guest included, is auto-joined to the `website` room, which is the room
//     misc_helper.clipboard.api.publish_room_change publishes to

const RECONNECTION_ATTEMPTS = 5;

// In dev each bench serves socketio on its own port, so the page's own port is the web port and
// has to be swapped. Behind nginx in production the socket is proxied on the site origin itself
// and no swap is right -- get_socket_url mirrors frappe's own get_host() on both counts.
function get_socket_url(boot) {
	if (!boot.dev_server) {
		return `${window.location.origin}/${boot.sitename}`;
	}

	return `${window.location.protocol}//${window.location.hostname}:${boot.socketio_port}/${boot.sitename}`;
}

class ClipboardRealtime {
	constructor(boot) {
		this.boot = boot;
		this.socket = null;
	}

	// Returns false when the socket could not be built, so the caller can fall back to polling
	// rather than sitting silently on a dead connection. This is the same failure the old page hit
	// when frappe.realtime.on() ran before the socket existed: no listener, no error.
	subscribe(event, handler) {
		if (!window.io) {
			console.error('socket.io client is not loaded; realtime is disabled');
			return false;
		}
		if (!this.socket) {
			this.socket = window.io(get_socket_url(this.boot), {
				withCredentials: true,
				reconnectionAttempts: RECONNECTION_ATTEMPTS,
				// The site is served over http in dev and https in production; socket.io reads the
				// scheme from the URL itself, so `secure` does not need setting separately.
			});
			this.socket.on('connect_error', (error) => {
				console.error('clipboard realtime could not connect:', error.message);
			});
		}
		this.socket.on(event, handler);
		return true;
	}

	unsubscribe(event, handler) {
		this.socket?.off(event, handler);
	}

	get is_connected() {
		return !!this.socket?.connected;
	}
}

window.clipboard_realtime = new ClipboardRealtime(window.clipboard_boot || {});
