document.addEventListener('alpine:init', () => {
	Alpine.data('clipboard_room', (context) => ({
		room_name: context.room.room_name,
		items: context.room.items,
		seconds_left: context.expires_in_seconds,
		expires_on: context.room.expires_on,
		draft_text: '',
		error_message: '',
		copied_item_name: '',
		is_busy: false,
		show_qr: false,
		poll_timer: null,
		labels: context.labels,

		init() {
			this.show_qr = window.matchMedia('(min-width: 768px)').matches;
			setInterval(() => this.count_down(), 1000);
			this.add_realtime_listener();
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

		async add_from_paste(event) {
			const image_file = [...(event.clipboardData?.items || [])]
				.filter((clipboard_item) => clipboard_item.type.startsWith('image/'))
				.map((clipboard_item) => clipboard_item.getAsFile())
				.find(Boolean);

			if (image_file) {
				event.preventDefault();
				await this.save_image(image_file);
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

		async add_picked_image(event) {
			const image_file = event.target.files?.[0];
			event.target.value = '';
			if (image_file) {
				await this.save_image(image_file);
			}
		},

		async save_text(content) {
			await this.save_item('misc_helper.clipboard.api.add_text', {
				room_name: this.room_name,
				content,
			});
		},

		async save_image(image_file) {
			const data_base64 = await get_base64(image_file);
			await this.save_item('misc_helper.clipboard.api.add_image', {
				room_name: this.room_name,
				file_name: image_file.name || 'pasted-image.png',
				data_base64,
			});
		},

		async save_item(method, args) {
			this.is_busy = true;
			this.error_message = '';
			try {
				await frappe.call({ method, args, type: 'POST' });
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
				await frappe.call({
					method: 'misc_helper.clipboard.api.delete_item',
					args: { room_name: this.room_name, item_name: item.name },
					type: 'POST',
				});
				await this.refresh_room();
			} catch (error) {
				this.error_message = error.message || this.labels.request_failed;
			}
		},

		async copy_item(item) {
			try {
				if (item.item_type === 'Image') {
					const image_response = await fetch(item.file_url);
					const image_blob = await image_response.blob();
					await navigator.clipboard.write([
						new ClipboardItem({ [image_blob.type]: image_blob }),
					]);
				} else {
					await navigator.clipboard.writeText(item.content);
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

		format_item_meta(item) {
			if (item.item_type !== 'Image') {
				return this.labels.text;
			}
			return `${item.file_name} · ${format_bytes(item.file_size)}`;
		},
	}));
});

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
	return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}
