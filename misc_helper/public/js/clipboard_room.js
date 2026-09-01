document.addEventListener('alpine:init', () => {
	Alpine.data('clipboard_room', (context) => ({
		room_name: context.room.room_name,
		items: context.room.items,
		seconds_left: context.expires_in_seconds,
		expires_on: context.room.expires_on,
		validity_hours: context.room.validity_hours,
		allowed_file_types: context.room.allowed_file_types,
		video_file_types: context.video_file_types,
		max_file_bytes: context.room.max_file_bytes,
		draft_text: '',
		error_message: '',
		copied_item_name: '',
		is_busy: false,
		show_qr: false,
		// dragenter/dragleave also fire as the pointer crosses each child element, so a boolean
		// flickers the overlay off mid-drag. Counting enters against leaves does not.
		drag_depth: 0,
		poll_timer: null,
		labels: context.labels,

		init() {
			this.show_qr = window.matchMedia('(min-width: 768px)').matches;
			setInterval(() => this.count_down(), 1000);
			this.add_realtime_listener();
		},

		get is_dragging() {
			return this.drag_depth > 0;
		},

		get expiry_label() {
			if (this.seconds_left <= 0) {
				return this.labels.expired;
			}
			const hours = Math.floor(this.seconds_left / 3600);
			const minutes = Math.floor((this.seconds_left % 3600) / 60);
			if (hours > 0) {
				return `${this.labels.in_prefix} ${hours}h ${minutes}m`;
			}
			if (minutes > 0) {
				return `${this.labels.in_prefix} ${minutes}m`;
			}
			return `${this.labels.in_prefix} ${this.seconds_left}s`;
		},

		count_down() {
			if (this.seconds_left > 0) {
				this.seconds_left -= 1;
			}
		},

		// The site and the browser can sit in different timezones, so the absolute expiry
		// string is never parsed against browser time — only the shift between two server
		// strings is, which is timezone-independent.
		set_expiry(expires_on) {
			const shift =
				Date.parse(expires_on.replace(' ', 'T')) -
				Date.parse(this.expires_on.replace(' ', 'T'));
			this.seconds_left = Math.max(
				0,
				this.seconds_left + Math.round(shift / 1000),
			);
			this.expires_on = expires_on;
		},

		add_realtime_listener() {
			if (!window.frappe || !frappe.realtime) {
				this.add_poll_backstop();
				return;
			}
			// The website bundle only builds the socket from its own frappe.ready handler, which
			// runs after alpine:init — until then realtime.on() hits an `if (this.socket)` guard
			// and silently registers nothing. init() returns early once a socket exists, so
			// claiming it here is safe whichever of the two gets there first.
			frappe.realtime.init(window.socketio_port, true);
			frappe.realtime.on(`clipboard_update:${this.room_name}`, async () => {
				await this.refresh_room();
			});
			setTimeout(() => this.add_poll_backstop(), 5000);
		},

		// Only if the socket never came up — otherwise the realtime ping is the trigger.
		add_poll_backstop() {
			if (this.poll_timer || frappe.realtime?.socket?.connected) {
				return;
			}
			this.poll_timer = setInterval(async () => {
				await this.refresh_room();
			}, 10000);
		},

		async refresh_room() {
			const response = await frappe.call({
				method: 'misc_helper.clipboard.api.get_room',
				args: { room_name: this.room_name },
			});
			const room = response.message;
			this.items = room.items;
			this.validity_hours = room.validity_hours;
			this.set_expiry(room.expires_on);
		},

		async send_draft() {
			const content = this.draft_text.trim();
			if (!content) {
				return;
			}
			await this.save_text(content);
			this.draft_text = '';
		},

		async save_validity() {
			this.error_message = '';
			try {
				const room = await call_api('misc_helper.clipboard.api.set_validity', {
					room_name: this.room_name,
					validity_hours: this.validity_hours,
				});
				this.set_expiry(room.expires_on);
			} catch (error) {
				this.error_message = error.message || this.labels.request_failed;
			}
		},

		async add_from_paste(event) {
			const pasted_files = [...(event.clipboardData?.items || [])]
				.filter((clipboard_item) => clipboard_item.kind === 'file')
				.map((clipboard_item) => clipboard_item.getAsFile())
				.filter(Boolean);

			if (pasted_files.length) {
				event.preventDefault();
				await this.upload_files(pasted_files);
				return;
			}

			// Let the textarea keep normal paste behaviour when the user is typing in it.
			if (event.target === document.getElementById('draft_input')) {
				return;
			}

			const content = (event.clipboardData?.getData('text') || '').trim();
			if (content) {
				event.preventDefault();
				await this.save_text(content);
			}
		},

		// Ctrl+C with nothing selected copies the newest item, so a phone-to-laptop handoff is
		// paste, switch machine, copy. A real selection or a focused field keeps normal copy —
		// silently hijacking Ctrl+C over selected text would lose what the user meant to take.
		async copy_latest(event) {
			const is_editable = ['INPUT', 'TEXTAREA'].includes(event.target?.tagName);
			if (
				is_editable ||
				window.getSelection().toString() ||
				!this.items.length
			) {
				return;
			}
			event.preventDefault();
			await this.copy_item(this.items[0]);
		},

		async add_dropped_files(event) {
			this.drag_depth = 0;
			await this.upload_files([...(event.dataTransfer?.files || [])]);
		},

		async add_picked_files(event) {
			const picked_files = [...(event.target.files || [])];
			event.target.value = '';
			await this.upload_files(picked_files);
		},

		// Sequential, not Promise.all: each upload slides the room expiry and publishes a realtime
		// ping, and every one of them counts against the per-room write rate limit.
		async upload_files(files) {
			for (const file of files) {
				if (!this.is_allowed(file)) {
					return;
				}
				await this.save_file(file);
				if (this.error_message) {
					return;
				}
			}
		},

		// Checked here as well as on the server: an over-long body is rejected by Werkzeug before
		// our endpoint is entered, so without this the user gets a bare 413 and no explanation.
		is_allowed(file) {
			if (!(file.name || '').includes('.')) {
				this.error_message = this.labels.no_extension;
				return false;
			}
			const file_type = file.name.split('.').pop().toUpperCase();
			if (!this.allowed_file_types.includes(file_type)) {
				this.error_message = this.labels.type_not_allowed.replace(
					'{0}',
					file_type,
				);
				return false;
			}
			if (file.size > this.max_file_bytes) {
				this.error_message = this.labels.file_too_large
					.replace('{0}', format_bytes(file.size))
					.replace('{1}', format_bytes(this.max_file_bytes));
				return false;
			}
			return true;
		},

		async save_text(content) {
			await this.save_item('misc_helper.clipboard.api.add_text', {
				room_name: this.room_name,
				content,
			});
		},

		async save_file(file) {
			const data_base64 = await get_base64(file);
			await this.save_item('misc_helper.clipboard.api.add_file', {
				room_name: this.room_name,
				file_name: file.name || 'pasted-image.png',
				data_base64,
			});
		},

		async save_item(method, args) {
			this.is_busy = true;
			this.error_message = '';
			try {
				await call_api(method, args);
				await this.refresh_room();
			} catch (error) {
				this.error_message = error.message || this.labels.request_failed;
			} finally {
				this.is_busy = false;
			}
		},

		async delete_item(item) {
			this.error_message = '';
			try {
				await call_api('misc_helper.clipboard.api.delete_item', {
					room_name: this.room_name,
					item_name: item.name,
				});
				await this.refresh_room();
			} catch (error) {
				this.error_message = error.message || this.labels.request_failed;
			}
		},

		async copy_item(item) {
			try {
				if (item.item_type === 'Text') {
					await navigator.clipboard.writeText(item.content);
				} else if (item.item_type === 'Image') {
					const image_response = await fetch(item.file_url);
					const image_blob = await image_response.blob();
					await navigator.clipboard.write([
						new ClipboardItem({ [image_blob.type]: image_blob }),
					]);
				} else {
					// A video or archive is not a clipboard flavour any OS would paste, so the
					// useful thing to hand over is the link to it.
					await navigator.clipboard.writeText(
						new URL(item.file_url, window.location.origin).href,
					);
				}
				this.set_copied(item.name);
			} catch {
				this.error_message = this.labels.copy_blocked;
			}
		},

		set_copied(item_name) {
			this.copied_item_name = item_name;
			setTimeout(() => {
				if (this.copied_item_name === item_name) {
					this.copied_item_name = '';
				}
			}, 1500);
		},

		is_video(item) {
			return this.video_file_types.includes(item.file_type);
		},

		format_item_meta(item) {
			if (item.item_type === 'Text') {
				return this.labels.text;
			}
			return `${item.file_name} · ${format_bytes(item.file_size)}`;
		},
	}));
});

// A plain fetch, not frappe.call: on website pages frappe.call resolves to the lightweight
// frappe/website/js/website.js implementation (not the desk request.js), whose process_response
// unconditionally pops its own desk-style "Message" dialog for any _server_messages — with no
// `silent` escape hatch — which is jarring on this bare page and duplicates the inline .cb-alert
// below. Doing the request ourselves keeps that dialog from ever firing.
async function call_api(method, args) {
	const response = await fetch(`/api/method/${method}`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			'X-Frappe-CSRF-Token': frappe.csrf_token,
		},
		body: JSON.stringify(args),
	});
	const body = await response.json().catch(() => ({}));
	if (!response.ok) {
		throw new Error(get_server_message(body) || '');
	}
	return body.message;
}

// The real text from frappe.throw() lives in _server_messages (a JSON-encoded array of
// JSON-encoded {message, ...} objects; see frappe/public/js/frappe/widgets/chart_widget.js for
// the same pattern) — never in a plain .message field.
function get_server_message(body) {
	try {
		const server_messages = JSON.parse(body?._server_messages || '[]');
		return server_messages.length
			? JSON.parse(server_messages.at(-1)).message
			: null;
	} catch {
		return null;
	}
}

async function get_base64(file) {
	const bytes = new Uint8Array(await file.arrayBuffer());
	let binary = '';
	for (let start = 0; start < bytes.length; start += 8192) {
		binary += String.fromCharCode.apply(
			null,
			bytes.subarray(start, start + 8192),
		);
	}
	return btoa(binary);
}

function format_bytes(size) {
	if (size < 1024) {
		return `${size} B`;
	}
	if (size < 1024 * 1024) {
		return `${Math.round(size / 1024)} KB`;
	}
	if (size < 1024 * 1024 * 1024) {
		return `${(size / (1024 * 1024)).toFixed(1)} MB`;
	}
	return `${(size / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}
