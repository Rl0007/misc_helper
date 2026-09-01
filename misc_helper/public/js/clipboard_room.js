const SENDER_STORAGE_KEY = 'clipboard_sender';
const ROOMS_STORAGE_KEY = 'clipboard_rooms';
const SENDER_PATTERN = /^[a-f0-9]{16,64}$/;

document.addEventListener('alpine:init', () => {
	Alpine.data('clipboard_room', (context) => ({
		room_name: context.room.room_name,
		items: context.room.items,
		seconds_left: context.expires_in_seconds,
		expires_on: context.room.expires_on,
		validity_hours: context.room.validity_hours,
		allowed_file_types: context.room.allowed_file_types,
		max_file_bytes: context.room.max_file_bytes,
		video_file_types: context.video_file_types,
		sender: get_or_create_sender(),
		rooms: [],
		pending_files: [],
		draft_text: '',
		error_message: '',
		copied_item_name: '',
		renaming_slug: '',
		rename_draft: '',
		is_busy: false,
		show_qr: false,
		show_sidebar: false,
		// dragenter/dragleave also fire as the pointer crosses each child element, so a boolean
		// flickers the overlay off mid-drag. Counting enters against leaves does not.
		drag_depth: 0,
		poll_timer: null,
		is_settling: true,
		labels: context.labels,

		init() {
			this.remember_room();
			setInterval(() => this.count_down(), 1000);
			this.add_realtime_listener();
			this.$nextTick(() => this.scroll_to_latest());
			// Images and video report their height only once decoded, so the first scroll lands
			// short and the newest bubble sits half under the composer. Keep re-pinning until
			// the media in view has settled.
			setTimeout(() => {
				this.is_settling = false;
			}, 3000);
		},

		on_media_load() {
			if (this.is_settling || this.is_following_latest()) {
				this.scroll_to_latest();
			}
		},

		// ---- rooms this browser has visited -------------------------------------------------
		//
		// Deliberately local, not a server listing. Rooms are public and guessable by design and
		// the name IS the credential (spec §5), so an endpoint enumerating every room would hand
		// each visitor a working key to everyone else's. The rename lives here for the same
		// reason: the slug is the address and must not change, so the label is this browser's
		// private nickname for it.
		remember_room() {
			const rooms = load_rooms();
			const existing = rooms.find((room) => room.slug === this.room_name);
			if (existing) {
				existing.opened_at = Date.now();
			} else {
				rooms.push({
					slug: this.room_name,
					label: this.room_name,
					opened_at: Date.now(),
				});
			}
			this.sort_and_save(rooms);
		},

		sort_and_save(rooms) {
			this.rooms = rooms.sort(
				(first, second) => second.opened_at - first.opened_at,
			);
			write_storage(ROOMS_STORAGE_KEY, this.rooms);
		},

		get current_label() {
			const room = this.rooms.find((entry) => entry.slug === this.room_name);
			return room ? room.label : this.room_name;
		},

		start_rename(room) {
			this.renaming_slug = room.slug;
			this.rename_draft = room.label;
			this.$nextTick(() => this.$refs[`rename_${room.slug}`]?.focus());
		},

		save_rename() {
			const room = this.rooms.find(
				(entry) => entry.slug === this.renaming_slug,
			);
			if (room) {
				// An emptied name falls back to the slug rather than leaving an unclickable blank.
				room.label = this.rename_draft.trim() || room.slug;
				this.sort_and_save(this.rooms);
			}
			this.cancel_rename();
		},

		cancel_rename() {
			this.renaming_slug = '';
			this.rename_draft = '';
		},

		forget_room(room) {
			this.sort_and_save(
				this.rooms.filter((entry) => entry.slug !== room.slug),
			);
		},

		// ---- chat sides ----------------------------------------------------------------------
		is_mine(item) {
			return !!item.sender && item.sender === this.sender;
		},

		// ---- item grouping ---------------------------------------------------------------------
		//
		// items stays newest-first, because that is what Ctrl+C and the realtime refresh mean by
		// "latest". Only the render order is reversed, so the pane reads oldest-first like a chat.
		get pinned_items() {
			return this.items.filter((item) => Number(item.is_pinned));
		},

		get ordered_items() {
			return this.items.filter((item) => !Number(item.is_pinned)).reverse();
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

		scroll_to_latest() {
			const pane = this.$refs.item_pane;
			if (pane) {
				pane.scrollTop = pane.scrollHeight;
			}
		},

		// Only follow along if the reader was already at the bottom. Yanking someone away from
		// history they are scrolled up reading is the classic chat-view bug.
		is_following_latest() {
			const pane = this.$refs.item_pane;
			if (!pane) {
				return true;
			}
			return pane.scrollHeight - pane.scrollTop - pane.clientHeight < 120;
		},

		resize_draft(textarea) {
			textarea.style.height = 'auto';
			textarea.style.height = `${textarea.scrollHeight}px`;
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
			const was_following = this.is_following_latest();
			const response = await frappe.call({
				method: 'misc_helper.clipboard.api.get_room',
				args: { room_name: this.room_name },
			});
			const room = response.message;
			this.items = room.items;
			this.validity_hours = room.validity_hours;
			this.set_expiry(room.expires_on);
			if (was_following) {
				this.$nextTick(() => this.scroll_to_latest());
			}
		},

		// ---- composing ---------------------------------------------------------------------
		//
		// Attachments stage here rather than uploading on drop, so the draft text can ride along
		// as the caption. Pasted TEXT still posts instantly with no Send press — that is the
		// point of the product, and there is no file for it to be a caption for.
		async send_draft() {
			const caption = this.draft_text.trim();
			if (!this.pending_files.length) {
				if (caption) {
					this.draft_text = '';
					this.reset_draft_height();
					await this.save_text(caption);
				}
				return;
			}

			const files = this.pending_files;
			this.pending_files = [];
			this.draft_text = '';
			this.reset_draft_height();

			// Sequential, not Promise.all: each upload slides the room expiry and publishes a
			// realtime ping, and every one counts against the per-room write rate limit. Only the
			// first file carries the caption, so one caption never repeats across a batch.
			for (const [index, file] of files.entries()) {
				await this.save_file(file, index === 0 ? caption : '');
				if (this.error_message) {
					return;
				}
			}
		},

		reset_draft_height() {
			const draft = document.getElementById('draft_input');
			if (draft) {
				this.$nextTick(() => this.resize_draft(draft));
			}
		},

		stage_files(files) {
			for (const file of files) {
				if (!this.is_allowed(file)) {
					return;
				}
				this.pending_files.push(file);
			}
			this.error_message = '';
		},

		remove_pending(index) {
			this.pending_files.splice(index, 1);
		},

		pending_preview(file) {
			return file.type.startsWith('image/') ? URL.createObjectURL(file) : '';
		},

		async add_from_paste(event) {
			const pasted_files = [...(event.clipboardData?.items || [])]
				.filter((clipboard_item) => clipboard_item.kind === 'file')
				.map((clipboard_item) => clipboard_item.getAsFile())
				.filter(Boolean);

			if (pasted_files.length) {
				event.preventDefault();
				this.stage_files(pasted_files);
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

		add_dropped_files(event) {
			this.drag_depth = 0;
			this.stage_files([...(event.dataTransfer?.files || [])]);
		},

		add_picked_files(event) {
			const picked_files = [...(event.target.files || [])];
			event.target.value = '';
			this.stage_files(picked_files);
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
				sender: this.sender,
			});
		},

		async save_file(file, caption) {
			const data_base64 = await get_base64(file);
			await this.save_item('misc_helper.clipboard.api.add_file', {
				room_name: this.room_name,
				file_name: file.name,
				data_base64,
				content: caption || '',
				sender: this.sender,
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

		async toggle_pin(item) {
			this.error_message = '';
			try {
				await call_api('misc_helper.clipboard.api.set_pinned', {
					room_name: this.room_name,
					item_name: item.name,
					is_pinned: Number(item.is_pinned) ? 0 : 1,
				});
				await this.refresh_room();
			} catch (error) {
				this.error_message = error.message || this.labels.request_failed;
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

		// ---- copying -------------------------------------------------------------------------
		//
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

		get is_dragging() {
			return this.drag_depth > 0;
		},
	}));
});

// localStorage throws outright in some contexts — a private window, a browser set to block site
// data, a thumbnail capture — so every read and write goes through these and the page renders
// correctly with nothing stored. The sidebar is a convenience, never state the room depends on.
function read_storage(key, fallback) {
	try {
		const raw = window.localStorage.getItem(key);
		return raw ? JSON.parse(raw) : fallback;
	} catch {
		return fallback;
	}
}

function write_storage(key, value) {
	try {
		window.localStorage.setItem(key, JSON.stringify(value));
	} catch {
		// Nothing to recover: the room itself is entirely server-side.
	}
}

function load_rooms() {
	const rooms = read_storage(ROOMS_STORAGE_KEY, []);
	if (!Array.isArray(rooms)) {
		return [];
	}
	// Anything the user could have hand-edited in devtools lands here, so rebuild each entry
	// rather than trusting its shape.
	return rooms
		.filter((room) => room && typeof room.slug === 'string' && room.slug)
		.map((room) => ({
			slug: room.slug,
			label:
				typeof room.label === 'string' && room.label ? room.label : room.slug,
			opened_at: Number(room.opened_at) || 0,
		}));
}

function get_or_create_sender() {
	const stored = read_storage(SENDER_STORAGE_KEY, '');
	if (typeof stored === 'string' && SENDER_PATTERN.test(stored)) {
		return stored;
	}
	const sender = [...crypto.getRandomValues(new Uint8Array(16))]
		.map((byte) => byte.toString(16).padStart(2, '0'))
		.join('');
	write_storage(SENDER_STORAGE_KEY, sender);
	return sender;
}

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
