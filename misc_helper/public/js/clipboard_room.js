// Avatar families, deliberately one per kind of thing so a message from a model is never
// mistakable for a person's and a room is never mistakable for either.
//
// Rooms are `planets`, replacing `glass` -- whose soft gradient blobs were too alike to tell two
// rooms apart in the rail, which is the one job a room tile has.
//
// `planets` exists only in DiceBear v10, which ships styles as JSON definitions in
// @dicebear/styles instead of the v9 `collection` barrel. That is why avatars are now rendered
// from a real bundle (clipboard_avatars.bundle.js) rather than an ESM import off a CDN.
const HUMAN_AVATAR_STYLE = 'notionists';
const AGENT_AVATAR_STYLE = 'bottts';
const ROOM_AVATAR_STYLE = 'planets';

const SENDER_STORAGE_KEY = 'clipboard_sender';
const NAME_STORAGE_KEY = 'clipboard_name';
const ROOMS_STORAGE_KEY = 'clipboard_rooms';
const THEME_STORAGE_KEY = 'clipboard_theme';
const BUBBLE_STORAGE_KEY = 'clipboard_bubble';
const ACCENT_STORAGE_KEY = 'clipboard_accent';
const APPEARANCE_STORAGE_KEY = 'clipboard_appearance_open';
const SEEN_STORAGE_KEY = 'clipboard_seen_counts';

const SENDER_PATTERN = /^[a-f0-9]{16,64}$/;
// Mirrors ROOM_NAME_PATTERN in misc_helper/clipboard/api.py. The server is still the authority;
// this only stops the new-room modal from navigating somewhere that would be rejected.
const ROOM_NAME_PATTERN = /^[a-z0-9][a-z0-9-]{2,40}$/;

const HOUR_SECONDS = 60 * 60;
const THEMES = ['light', 'dark', 'system'];

// Bubble and accent are deliberately separate: recolouring your own bubble should not repaint
// every button and link in the app.
const PALETTE = {
	slate: { label: 'Slate', value: '#475569', on: '#ffffff' },
	graphite: { label: 'Graphite', value: '#3f3f46', on: '#fafafa' },
	indigo: { label: 'Indigo', value: '#4f46e5', on: '#ffffff' },
	green: { label: 'Green', value: '#00a884', on: '#ffffff' },
	teal: { label: 'Teal', value: '#0d9488', on: '#ffffff' },
	violet: { label: 'Violet', value: '#7c3aed', on: '#ffffff' },
	rose: { label: 'Rose', value: '#e11d48', on: '#ffffff' },
	amber: { label: 'Amber', value: '#f59e0b', on: '#1c1917' },
};

// No DiceBear-equivalent library exists for chat wallpapers, so this is a hand-rolled seamless
// tile. A mid-grey stroke at low alpha reads correctly on both the light and dark surfaces.
const DOODLES = (() => {
	const shapes = [
		"<circle cx='28' cy='26' r='11'/><path d='M28 15v22M17 26h22'/>",
		"<path d='M74 18c6-8 18-4 18 5 0 7-9 12-18 19-9-7-18-12-18-19 0-9 12-13 18-5z'/>",
		"<circle cx='140' cy='28' r='13'/><path d='M135 25v.1M145 25v.1M134 33c4 4 8 4 12 0'/>",
		"<path d='M182 34h26M186 34c0-9 6-15 13-15s13 6 13 15'/><path d='M195 19V13'/>",
		"<rect x='16' y='74' width='30' height='22' rx='4'/><circle cx='31' cy='85' r='6'/>",
		"<path d='M72 96V70l20 6v20'/><circle cx='68' cy='96' r='5'/><circle cx='88' cy='96' r='5'/>",
		"<path d='M124 96c-10-6-16-13-16-20a9 9 0 0 1 16-5 9 9 0 0 1 16 5c0 7-6 14-16 20z'/>",
		"<path d='M176 70l7 15 16 2-12 11 3 16-14-8-14 8 3-16-12-11 16-2z'/>",
		"<path d='M20 150c8-10 20-10 28 0M24 158c5-6 12-6 17 0'/><circle cx='33' cy='166' r='2'/>",
		"<path d='M70 140h30v24H70z'/><path d='M70 140l15 13 15-13'/>",
		"<circle cx='140' cy='152' r='14'/><path d='M140 144v9l6 4'/>",
		"<path d='M180 166c-8-4-14-11-14-18 0-8 7-14 15-14s15 6 15 14c0 9-9 14-16 18z'/><path d='M181 140v10'/>",
		"<path d='M18 200c10-8 22-8 32 0'/><path d='M26 208c6-4 12-4 18 0'/>",
		"<path d='M78 196l14-8 14 8-14 8z'/><path d='M78 196v10l14 8 14-8v-10'/>",
		"<circle cx='146' cy='202' r='10'/><path d='M146 192v-6M146 218v-6M136 202h-6M168 202h-6'/>",
		"<path d='M186 210c-6 0-10-4-10-9s4-9 10-9 10 4 10 9-4 9-10 9z'/><path d='M196 210l8 8'/>",
	].join('');
	const svg = `<svg xmlns='http://www.w3.org/2000/svg' width='220' height='220' viewBox='0 0 220 220'><g fill='none' stroke='#888888' stroke-opacity='0.16' stroke-width='1.4' stroke-linecap='round' stroke-linejoin='round'>${shapes}</g></svg>`;
	return `data:image/svg+xml;utf8,${svg
		.replace(/</g, '%3C')
		.replace(/>/g, '%3E')
		.replace(/#/g, '%23')
		.replace(/"/g, "'")}`;
})();

// The rich editor is the one place user markup reaches x-html, so it gets an allow-list rather
// than trust: anything not on it is unwrapped, and every attribute but a safe href goes. The
// server sanitises again on the way in -- this keeps the live editor honest and means a bug on
// either side is not enough on its own.
const ALLOWED_TAGS = new Set([
	'B',
	'STRONG',
	'I',
	'EM',
	'U',
	'CODE',
	'PRE',
	'A',
	'UL',
	'OL',
	'LI',
	'BR',
	'P',
	'DIV',
	'SPAN',
]);

document.addEventListener('alpine:init', () => {
	Alpine.data('clipboard_room', (context) => ({
		room_name: context.room.room_name,
		display_name: context.room.display_name || context.room.room_name,
		items: context.room.items,
		expires_on: context.room.expires_on,
		seconds_left: context.expires_in_seconds,
		validity_hours: context.room.validity_hours,
		allowed_file_types: context.room.allowed_file_types,
		max_file_bytes: context.room.max_file_bytes,
		image_file_types: context.image_file_types,
		video_file_types: context.video_file_types,
		labels: context.labels,

		sender: get_or_create_sender(),
		me: read_storage(NAME_STORAGE_KEY, ''),
		name_draft: '',
		rooms: [],
		seen_counts: {},

		draft: '',
		rich_mode: false,
		rich_html: '',
		attachments: [],
		room_draft: '',
		lightbox: null,
		toast: '',
		copied_item_name: '',
		highlighted_item_name: '',
		pinned_index: 0,

		rail_expanded: false,
		// Colours are a set-once preference; the disclosure starts closed and remembers.
		show_appearance: read_storage(APPEARANCE_STORAGE_KEY, false) === true,
		show_join: false,
		show_new_room: false,
		show_validity: false,
		is_busy: false,
		avatar_version: 0,
		theme: 'system',
		bubble_color: 'slate',
		accent: 'slate',

		// dragenter/dragleave also fire as the pointer crosses each child element, so a boolean
		// flickers the overlay off mid-drag. Counting enters against leaves does not.
		drag_depth: 0,
		poll_timer: null,
		toast_timer: null,
		highlight_timer: null,
		realtime_event: '',
		is_settling: true,

		init() {
			this.theme = read_storage(THEME_STORAGE_KEY, 'system');
			this.bubble_color = read_storage(BUBBLE_STORAGE_KEY, 'slate');
			this.accent = read_storage(ACCENT_STORAGE_KEY, 'slate');
			this.set_theme_class();
			this.set_palette_variables();
			window
				.matchMedia('(prefers-color-scheme: dark)')
				.addEventListener('change', () => this.set_theme_class());

			// The library renders the avatars locally; until it has loaded, dicebear() falls back
			// to the public URL. Bumping the counter re-runs every avatar getter once it lands.
			window.addEventListener('dicebear-ready', () => this.avatar_version++);
			if (window.render_avatar) {
				this.avatar_version++;
			}

			this.show_join = !this.me;
			this.seen_counts = read_storage(SEEN_STORAGE_KEY, {});
			this.remember_room();
			this.run_action(() => this.refresh_rooms());

			setInterval(() => this.count_down(), 1000);
			// Bound once and reused: unsubscribe() removes a listener BY REFERENCE, so a fresh
			// arrow function per call would leave the old room's listener attached and every room
			// switch would add another refresh on every ping.
			this.on_room_change = async () => {
				await this.refresh_room();
			};
			this.add_room_listener();
			window.addEventListener('popstate', () =>
				this.open_room(get_room_name_from_path()),
			);

			this.$nextTick(() => this.scroll_to_latest());
			// Images and video report their height only once decoded, so the first scroll lands
			// short and the newest bubble sits half under the composer. Keep re-pinning until the
			// media in view has settled.
			setTimeout(() => {
				this.is_settling = false;
			}, 3000);
		},

		// ---- rooms this browser has visited -------------------------------------------------
		//
		// Deliberately local, not a server listing. Rooms are public and guessable by design and
		// the name IS the credential (spec §5), so an endpoint enumerating every room would hand
		// each visitor a working key to everyone else's. get_rooms only ever answers for names
		// this browser already knew.
		remember_room() {
			const rooms = load_rooms().filter(
				(room) => room.room_name !== this.room_name,
			);
			rooms.push({ room_name: this.room_name, opened_at: Date.now() });
			write_storage(ROOMS_STORAGE_KEY, rooms);
		},

		async refresh_rooms() {
			const visited = load_rooms();
			if (!visited.length) {
				this.rooms = [];
				return;
			}
			const rooms = await call_api('misc_helper.clipboard.api.get_rooms', {
				room_names: visited.map((room) => room.room_name),
			});
			const opened_at = Object.fromEntries(
				visited.map((room) => [room.room_name, room.opened_at]),
			);
			// get_rooms omits names that no longer exist, so this doubles as the prune.
			this.rooms = rooms.sort(
				(first, second) =>
					(opened_at[second.room_name] || 0) -
					(opened_at[first.room_name] || 0),
			);
			write_storage(
				ROOMS_STORAGE_KEY,
				this.rooms.map((room) => ({
					room_name: room.room_name,
					opened_at: opened_at[room.room_name] || Date.now(),
				})),
			);
		},

		// "Unread" cannot be a session flag: it has to survive a reload, so it is the item count
		// this browser last saw in that room measured against the count the server reports now.
		is_unread(room) {
			return (
				room.room_name !== this.room_name &&
				room.item_count > (this.seen_counts[room.room_name] || 0)
			);
		},

		set_seen(room_name, item_count) {
			this.seen_counts = { ...this.seen_counts, [room_name]: item_count };
			write_storage(SEEN_STORAGE_KEY, this.seen_counts);
		},

		room_preview(room) {
			if (!room.item_count) {
				return this.labels.no_messages;
			}
			const preview = (room.last_preview || '').replace(/\s+/g, ' ').trim();
			return `${room.last_sender_name}: ${preview || this.labels.sent_a_file}`;
		},

		// Swaps the room in place rather than navigating: a page load would drop the socket, the
		// staged attachments and the draft.
		async open_room(room_name) {
			this.rail_expanded = false;
			if (!room_name || room_name === this.room_name) {
				return;
			}
			this.room_name = room_name;
			window.history.pushState({}, '', `/clipboard/${room_name}`);
			await this.run_action(async () => {
				await this.refresh_room();
				this.remember_room();
				this.add_room_listener();
				await this.refresh_rooms();
			});
			this.$nextTick(() => this.scroll_to_latest());
		},

		async create_room() {
			const display_name = this.room_draft.trim();
			const room_name = this.slug(display_name);
			if (!ROOM_NAME_PATTERN.test(room_name)) {
				return;
			}
			this.show_new_room = false;
			this.room_draft = '';
			await this.run_action(async () => {
				await call_api('misc_helper.clipboard.api.set_room_display_name', {
					room_name,
					display_name,
				});
				await this.open_room(room_name);
				this.notify(this.labels.room_created);
			});
		},

		get is_valid_room_draft() {
			return ROOM_NAME_PATTERN.test(this.slug(this.room_draft));
		},

		slug(text) {
			return (text || '')
				.toLowerCase()
				.trim()
				.replace(/[^a-z0-9]+/g, '-')
				.replace(/^-+|-+$/g, '');
		},

		join() {
			this.me = this.name_draft.trim();
			write_storage(NAME_STORAGE_KEY, this.me);
			this.show_join = false;
			this.notify(this.labels.welcome.replace('{0}', this.me));
		},

		// ---- avatars -------------------------------------------------------------------------
		//
		// A person is seeded by their display name so the same person draws the same face for
		// everyone in the room; the opaque sender id is only the fallback for an item posted
		// before anyone named themselves. Agents get a visibly different family so a message from
		// a model is never mistaken for a person's.
		sender_avatar_url(person) {
			this.avatar_version;
			const style =
				String(person.sender_kind).toLowerCase() === 'agent'
					? AGENT_AVATAR_STYLE
					: HUMAN_AVATAR_STYLE;
			return dicebear(style, person.sender_name || person.sender);
		},

		room_avatar_url(room) {
			this.avatar_version;
			return dicebear(ROOM_AVATAR_STYLE, room.display_name || room.room_name);
		},

		get current_room_avatar_url() {
			return this.room_avatar_url({
				room_name: this.room_name,
				display_name: this.display_name,
			});
		},

		get my_avatar_url() {
			this.avatar_version;
			return dicebear(HUMAN_AVATAR_STYLE, this.me || this.sender);
		},

		get draft_avatar_url() {
			this.avatar_version;
			return dicebear(HUMAN_AVATAR_STYLE, this.name_draft || 'guest');
		},

		get new_room_avatar_url() {
			this.avatar_version;
			return dicebear(ROOM_AVATAR_STYLE, this.room_draft || 'new room');
		},

		// ---- reading the room ------------------------------------------------------------------
		is_mine(item) {
			return !!item.sender && item.sender === this.sender;
		},

		is_image(file) {
			return this.image_file_types.includes(file.file_type);
		},

		is_video(file) {
			return this.video_file_types.includes(file.file_type);
		},

		// items stays newest-first, because that is what Ctrl+C and the realtime refresh mean by
		// "latest". Only the render order is reversed, so the pane reads oldest-first like a chat.
		//
		// Every file that shared a group_id in one send collapses into a single bubble with an
		// attachment grid, captioned by the first of them -- the same send drawn as one message
		// rather than as one message per file.
		get messages() {
			const messages = [];
			const groups = new Map();
			for (const item of [...this.items].reverse()) {
				const group = groups.get(item.group_id);
				if (item.group_id && group) {
					group.files.push(item);
					continue;
				}
				const message = {
					key: item.name,
					item_name: item.name,
					sender: item.sender,
					sender_name: item.sender_name,
					sender_kind: item.sender_kind,
					is_mine: this.is_mine(item),
					text_format: item.text_format || 'Plain',
					content: item.content || '',
					creation: item.creation,
					is_pinned: !!Number(item.is_pinned),
					files: item.item_type === 'Text' ? [] : [item],
				};
				messages.push(message);
				if (item.group_id) {
					groups.set(item.group_id, message);
				}
			}
			return messages;
		},

		// Pinned messages ride a banner under the header rather than only carrying a pin icon on
		// their own bubble -- in a fast room the bubble scrolls away and the pin goes with it.
		// Newest pin first, which is the one the banner opens on.
		get pinned_messages() {
			return this.messages.filter((message) => message.is_pinned).reverse();
		},

		get pinned_message() {
			const pinned = this.pinned_messages;
			return pinned.length ? pinned[this.pinned_index % pinned.length] : null;
		},

		pinned_preview(message) {
			if (!message) {
				return '';
			}
			const text =
				message.text_format === 'Rich'
					? html_to_text(message.content)
					: message.content;
			const preview = (text || '').replace(/\s+/g, ' ').trim();
			return preview || message.files[0]?.file_name || this.labels.sent_a_file;
		},

		// One cycling row, not a list: the banner jumps to the pin it is showing and then moves
		// on to the next one, which is the whole interaction Telegram gives the same control.
		show_next_pinned() {
			const pinned = this.pinned_messages;
			if (!pinned.length) {
				return;
			}
			const message = pinned[this.pinned_index % pinned.length];
			this.pinned_index = (this.pinned_index + 1) % pinned.length;
			this.scroll_to_message(message.item_name);
		},

		scroll_to_message(item_name) {
			const element = document.getElementById(`item-${item_name}`);
			if (!element) {
				return;
			}
			element.scrollIntoView({ behavior: 'smooth', block: 'center' });
			this.highlighted_item_name = item_name;
			clearTimeout(this.highlight_timer);
			this.highlight_timer = setTimeout(() => {
				this.highlighted_item_name = '';
			}, 2000);
		},

		get members() {
			const people = new Map();
			for (const item of this.items) {
				if (!people.has(item.sender)) {
					people.set(item.sender, {
						sender: item.sender,
						sender_name: item.sender_name,
						sender_kind: item.sender_kind,
					});
				}
			}
			return [...people.values()];
		},

		get wallpaper_style() {
			return `background-image:url("${DOODLES}");background-size:220px;`;
		},

		get draft_placeholder() {
			return this.labels.message_placeholder.replace('{0}', this.display_name);
		},

		clock(timestamp) {
			return new Date(timestamp.replace(' ', 'T')).toLocaleTimeString([], {
				hour: '2-digit',
				minute: '2-digit',
			});
		},

		get time_left() {
			return this.format_duration(this.seconds_left);
		},

		// Only the shift between two SERVER strings is measured, never a server string against
		// browser time: the site and the browser can sit in different timezones.
		room_time_left(room) {
			const shift =
				(Date.parse(room.expires_on.replace(' ', 'T')) -
					Date.parse(this.expires_on.replace(' ', 'T'))) /
				1000;
			return this.format_duration(this.seconds_left + Math.round(shift));
		},

		format_duration(seconds) {
			if (seconds <= 0) {
				return this.labels.expired;
			}
			const hours = Math.floor(seconds / HOUR_SECONDS);
			const minutes = Math.floor((seconds % HOUR_SECONDS) / 60);
			return hours ? `${hours}h ${minutes}m` : `${minutes}m`;
		},

		count_down() {
			if (this.seconds_left > 0) {
				this.seconds_left -= 1;
			}
		},

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

		// ---- scrolling -------------------------------------------------------------------------
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

		on_media_load() {
			if (this.is_settling || this.is_following_latest()) {
				this.scroll_to_latest();
			}
		},

		// Bound once, so unsubscribe() can actually find the same reference to remove.
		on_room_change: null,

		get is_realtime_connected() {
			if (window.clipboard_realtime) {
				return window.clipboard_realtime.is_connected;
			}
			return !!window.frappe?.realtime?.socket?.connected;
		},

		// ---- live updates ----------------------------------------------------------------------
		//
		// Two transports, because this component renders under two document shells: the standalone
		// one, which wires socket.io itself (clipboard_realtime.js), and the base.html one, which
		// gets frappe.realtime from the website bundle. Neither is assumed present.
		add_room_listener() {
			const next_event = `clipboard_update:${this.room_name}`;

			if (window.clipboard_realtime) {
				if (this.realtime_event) {
					window.clipboard_realtime.unsubscribe(
						this.realtime_event,
						this.on_room_change,
					);
				}
				this.realtime_event = next_event;
				if (
					!window.clipboard_realtime.subscribe(next_event, this.on_room_change)
				) {
					this.add_poll_backstop();
				}
			} else if (window.frappe?.realtime) {
				// The website bundle only builds the socket from its own frappe.ready handler, which
				// runs after alpine:init -- until then realtime.on() hits an `if (this.socket)` guard
				// and silently registers nothing. init() returns early once a socket exists, so
				// claiming it here is safe whichever of the two gets there first.
				frappe.realtime.init(window.socketio_port, true);
				if (this.realtime_event) {
					frappe.realtime.off(this.realtime_event);
				}
				this.realtime_event = next_event;
				frappe.realtime.on(next_event, this.on_room_change);
			} else {
				this.add_poll_backstop();
				return;
			}

			setTimeout(() => this.add_poll_backstop(), 5000);
		},

		// Only if the socket never came up -- otherwise the realtime ping is the trigger.
		add_poll_backstop() {
			if (this.poll_timer || this.is_realtime_connected) {
				return;
			}
			this.poll_timer = setInterval(async () => {
				await this.refresh_room();
			}, 10000);
		},

		async refresh_room() {
			const was_following = this.is_following_latest();
			const room = await call_api('misc_helper.clipboard.api.get_room', {
				room_name: this.room_name,
			});
			this.display_name = room.display_name || room.room_name;
			this.items = room.items;
			this.validity_hours = room.validity_hours;
			this.allowed_file_types = room.allowed_file_types;
			this.max_file_bytes = room.max_file_bytes;
			this.set_expiry(room.expires_on);
			this.set_seen(this.room_name, room.items.length);
			if (was_following) {
				this.$nextTick(() => this.scroll_to_latest());
			}
		},

		// ---- composing ---------------------------------------------------------------------
		get draft_length() {
			return this.rich_mode
				? html_to_text(this.rich_html).trim().length
				: this.draft.trim().length;
		},

		// A one-liner belongs in the plain box; anything longer earns formatting. Pasted code is
		// the exception -- it has newlines by definition and belongs in a code block, not in a
		// WYSIWYG editor that would swallow its indentation.
		set_rich_mode_for_draft() {
			if (
				!this.rich_mode &&
				!looks_like_code(this.draft) &&
				(this.draft.length > 140 || this.draft.includes('\n'))
			) {
				this.toggle_rich();
			}
		},

		toggle_rich() {
			this.rich_mode = !this.rich_mode;
			this.$nextTick(() => {
				if (this.rich_mode) {
					this.rich_html = escape_html(this.draft).replace(/\n/g, '<br>');
					this.$refs.rich_draft.innerHTML = this.rich_html;
					this.$refs.rich_draft.focus();
					place_caret_at_end(this.$refs.rich_draft);
					return;
				}
				this.draft = html_to_text(this.rich_html);
				this.rich_html = '';
				this.$refs.plain_draft?.focus();
			});
		},

		set_rich_format(command) {
			this.$refs.rich_draft?.focus();
			if (command === 'createLink') {
				const url = window.prompt(this.labels.link_url);
				if (!url) {
					return;
				}
				document.execCommand('createLink', false, url);
			} else {
				document.execCommand(command, false, null);
			}
			this.rich_html = this.$refs.rich_draft.innerHTML;
		},

		resize_draft(element) {
			if (!element?.getClientRects().length) {
				return; // hidden: scrollHeight would be 0 and the box would collapse
			}
			element.style.height = 'auto';
			element.style.height = `${element.scrollHeight}px`;
			this.set_rich_mode_for_draft();
		},

		reset_draft_height() {
			this.$nextTick(() => this.resize_draft(this.$refs.plain_draft));
		},

		// Attachments stage here rather than uploading on drop, so the draft text can ride along
		// as the caption. Pasted TEXT still posts instantly with no Send press -- that is the
		// point of the product, and there is no file for it to be a caption for.
		async send() {
			const text = this.rich_mode
				? html_to_text(this.rich_html).trim()
				: this.draft.trim();
			const files = this.attachments;
			if (!text && !files.length) {
				return;
			}
			const content = this.rich_mode ? sanitize_html(this.rich_html) : text;
			const text_format = get_text_format(this.rich_mode, text);

			this.attachments = [];
			this.draft = '';
			this.rich_html = '';
			this.rich_mode = false;
			if (this.$refs.rich_draft) {
				this.$refs.rich_draft.innerHTML = '';
			}
			this.reset_draft_height();

			await this.run_action(async () => {
				if (!files.length) {
					await this.save_text(content, text_format);
					return;
				}
				// One group_id per send action: every item carrying it renders as a single bubble.
				// Sequential, not Promise.all -- each upload slides the room expiry, publishes a
				// realtime ping and counts against the per-room write limit, and only the first
				// carries the caption so one caption never repeats across a batch.
				const group_id = get_random_id();
				for (const [index, file] of files.entries()) {
					await this.save_file(file, index === 0 ? text : '', group_id);
					release_preview(file);
				}
			});
		},

		async save_text(content, text_format) {
			await call_api('misc_helper.clipboard.api.add_text', {
				room_name: this.room_name,
				content,
				sender: this.sender,
				sender_name: this.me,
				text_format,
			});
			await this.refresh_room();
		},

		async save_file(attachment, caption, group_id) {
			await call_api('misc_helper.clipboard.api.add_file', {
				room_name: this.room_name,
				file_name: attachment.name,
				data_base64: await get_base64(attachment.file),
				content: caption,
				sender: this.sender,
				sender_name: this.me,
				group_id,
			});
			await this.refresh_room();
		},

		async save_validity() {
			await this.run_action(async () => {
				const room = await call_api('misc_helper.clipboard.api.set_validity', {
					room_name: this.room_name,
					validity_hours: this.validity_hours,
				});
				this.set_expiry(room.expires_on);
			});
		},

		async toggle_pin(message) {
			await this.run_action(async () => {
				await call_api('misc_helper.clipboard.api.set_pinned', {
					room_name: this.room_name,
					item_name: message.item_name,
					is_pinned: message.is_pinned ? 0 : 1,
				});
				await this.refresh_room();
			});
		},

		async delete_message(message) {
			await this.run_action(async () => {
				for (const file of message.files) {
					await call_api('misc_helper.clipboard.api.delete_item', {
						room_name: this.room_name,
						item_name: file.name,
					});
				}
				if (!message.files.length) {
					await call_api('misc_helper.clipboard.api.delete_item', {
						room_name: this.room_name,
						item_name: message.item_name,
					});
				}
				await this.refresh_room();
			});
		},

		// Every server action reports failure the same way -- the text frappe.throw() sent, or a
		// generic fallback -- so no caller can forget to surface it.
		async run_action(action) {
			this.is_busy = true;
			try {
				await action();
			} catch (error) {
				this.notify(error.message || this.labels.request_failed);
			} finally {
				this.is_busy = false;
			}
		},

		// ---- attachments -----------------------------------------------------------------------
		stage_files(files) {
			for (const file of files) {
				if (!this.is_allowed(file)) {
					return;
				}
				this.attachments.push({
					id: get_random_id(),
					file,
					name: file.name,
					preview_url: file.type.startsWith('image/')
						? URL.createObjectURL(file)
						: '',
				});
			}
			if (files.length) {
				this.notify(
					files.length === 1
						? this.labels.file_ready
						: this.labels.files_ready.replace('{0}', files.length),
				);
			}
		},

		remove_attachment(id) {
			const attachment = this.attachments.find((file) => file.id === id);
			release_preview(attachment);
			this.attachments = this.attachments.filter((file) => file.id !== id);
		},

		// Checked here as well as on the server: an over-long body is rejected by Werkzeug before
		// our endpoint is entered, so without this the user gets a bare 413 and no explanation.
		is_allowed(file) {
			if (!(file.name || '').includes('.')) {
				this.notify(this.labels.no_extension);
				return false;
			}
			const file_type = file.name.split('.').pop().toUpperCase();
			if (!this.allowed_file_types.includes(file_type)) {
				this.notify(this.labels.type_not_allowed.replace('{0}', file_type));
				return false;
			}
			if (file.size > this.max_file_bytes) {
				this.notify(
					this.labels.file_too_large
						.replace('{0}', format_bytes(file.size))
						.replace('{1}', format_bytes(this.max_file_bytes)),
				);
				return false;
			}
			return true;
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

			// Let a field the user is typing in keep normal paste behaviour.
			if (is_editable(event.target)) {
				return;
			}

			const content = (event.clipboardData?.getData('text') || '').trim();
			if (content) {
				event.preventDefault();
				await this.run_action(() =>
					this.save_text(content, looks_like_code(content) ? 'Code' : 'Plain'),
				);
			}
		},

		get is_dragging() {
			return this.drag_depth > 0;
		},

		// ---- copying -------------------------------------------------------------------------
		//
		// Ctrl+C with nothing selected copies the newest item, so a phone-to-laptop handoff is
		// paste, switch machine, copy. A real selection or a focused field keeps normal copy --
		// silently hijacking Ctrl+C over selected text would lose what the user meant to take.
		async copy_latest(event) {
			if (
				is_editable(event.target) ||
				window.getSelection().toString() ||
				!this.items.length
			) {
				return;
			}
			event.preventDefault();
			const item = this.items[0];
			if (item.item_type === 'Text') {
				await this.copy_text(item.content, this.labels.message_copied);
				this.set_copied(item.name);
				return;
			}
			await this.copy_file(item);
		},

		// Text first, then one URL per attached file. A caption and its picture cannot both ride
		// the clipboard at once, and the URL is the half that can be pasted anywhere -- a chat
		// box, a mail, a terminal -- which is what sharing an image out of here means.
		async copy_message(message) {
			this.set_copied(message.item_name);
			const text =
				message.text_format === 'Rich'
					? html_to_text(message.content)
					: message.content;
			const links = message.files.map((file) => file_link(file));
			const payload = [text, ...links].filter(Boolean).join('\n');
			if (!payload) {
				return;
			}
			await this.copy_text(
				payload,
				text ? this.labels.message_copied : this.labels.file_link_copied,
			);
		},

		// Deliberately the link and never the bytes, images included: an image blob on the
		// clipboard only pastes into apps that accept one, and it cannot be shared as a link.
		async copy_file(file) {
			if (!file) {
				return;
			}
			await this.copy_text(file_link(file), this.labels.file_link_copied);
		},

		async copy_text(text, message) {
			this.notify(
				(await write_to_clipboard(text)) ? message : this.labels.copy_blocked,
			);
		},

		set_copied(item_name) {
			this.copied_item_name = item_name;
			setTimeout(() => {
				if (this.copied_item_name === item_name) {
					this.copied_item_name = '';
				}
			}, 1500);
		},

		async share_link() {
			await this.copy_text(
				`${window.location.origin}/clipboard/${this.room_name}`,
				this.labels.link_copied,
			);
		},

		// ---- appearance ------------------------------------------------------------------------
		get palette() {
			return Object.entries(PALETTE).map(([name, swatch]) => ({
				name,
				...swatch,
			}));
		},

		toggle_appearance() {
			this.show_appearance = !this.show_appearance;
			write_storage(APPEARANCE_STORAGE_KEY, this.show_appearance);
		},

		set_bubble_color(name) {
			this.bubble_color = name;
			write_storage(BUBBLE_STORAGE_KEY, name);
			this.set_palette_variables();
		},

		set_accent(name) {
			this.accent = name;
			write_storage(ACCENT_STORAGE_KEY, name);
			this.set_palette_variables();
		},

		// The utilities keep their var() indirection (plain @theme, not @theme inline), so
		// overriding the variable on the root repaints every bubble and accent at once.
		set_palette_variables() {
			const root = document.documentElement;
			const bubble = PALETTE[this.bubble_color] || PALETTE.slate;
			const accent = PALETTE[this.accent] || PALETTE.slate;
			root.style.setProperty('--color-bubble', bubble.value);
			root.style.setProperty('--color-on-bubble', bubble.on);
			root.style.setProperty('--color-primary', accent.value);
			root.style.setProperty('--color-on-primary', accent.on);
		},

		get theme_title() {
			const next = THEMES[(THEMES.indexOf(this.theme) + 1) % THEMES.length];
			return this.labels.theme_title
				.replace('{0}', this.labels[`theme_${this.theme}`])
				.replace('{1}', this.labels[`theme_${next}`]);
		},

		cycle_theme() {
			this.theme = THEMES[(THEMES.indexOf(this.theme) + 1) % THEMES.length];
			write_storage(THEME_STORAGE_KEY, this.theme);
			this.set_theme_class();
			this.notify(
				this.labels.theme_changed.replace(
					'{0}',
					this.labels[`theme_${this.theme}`],
				),
			);
		},

		set_theme_class() {
			const prefers_dark = window.matchMedia(
				'(prefers-color-scheme: dark)',
			).matches;
			document.documentElement.classList.toggle(
				'dark',
				this.theme === 'dark' || (this.theme === 'system' && prefers_dark),
			);
		},

		notify(message) {
			this.toast = message;
			clearTimeout(this.toast_timer);
			this.toast_timer = setTimeout(() => {
				this.toast = '';
			}, 2500);
		},
	}));
});

function dicebear(style, seed) {
	const safe_seed = seed || 'anonymous';
	// The API is only the fallback for a browser that could not load the local ESM bundle.
	return (
		window.render_avatar?.(style, safe_seed) ||
		`https://api.dicebear.com/9.x/${style}/svg?seed=${encodeURIComponent(safe_seed)}`
	);
}

// localStorage throws outright in some contexts -- a private window, a browser set to block site
// data, a thumbnail capture -- so every read and write goes through these and the page renders
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
	// rather than trusting its shape. `slug` is the pre-rename key this list used to carry.
	return rooms
		.map((room) => ({
			room_name: room?.room_name || room?.slug,
			opened_at: Number(room?.opened_at) || 0,
		}))
		.filter((room) => typeof room.room_name === 'string' && room.room_name);
}

// Hex, not crypto.randomUUID(): the server keeps `sender` and `group_id` only if they match
// OPAQUE_ID_PATTERN (^[a-f0-9]{16,64}$), and it drops a malformed one silently rather than
// throwing -- so a UUID's dashes cost you the chat sides and the multi-file grouping with no
// error anywhere.
function get_random_id() {
	return [...crypto.getRandomValues(new Uint8Array(16))]
		.map((byte) => byte.toString(16).padStart(2, '0'))
		.join('');
}

function get_or_create_sender() {
	const stored = read_storage(SENDER_STORAGE_KEY, '');
	if (typeof stored === 'string' && SENDER_PATTERN.test(stored)) {
		return stored;
	}
	const sender = get_random_id();
	write_storage(SENDER_STORAGE_KEY, sender);
	return sender;
}

function get_room_name_from_path() {
	// request.path is not available to Frappe Jinja, and the room can change without a page load.
	return window.location.pathname.split('/').filter(Boolean).pop();
}

function is_editable(element) {
	return (
		!!element &&
		(element.isContentEditable ||
			['INPUT', 'TEXTAREA'].includes(element.tagName))
	);
}

// Empty for a Guest, whose session carries no token -- frappe/auth.py:83 validate_csrf_token
// returns early in that case. A logged-in visitor DOES need it, or every write is rejected as
// "Invalid Request". The standalone shell supplies it through window.clipboard_boot; the base.html
// shell through frappe.csrf_token.
function get_csrf_token() {
	return window.clipboard_boot?.csrf_token || window.frappe?.csrf_token || '';
}

// A plain fetch, not frappe.call: on website pages frappe.call resolves to the lightweight
// frappe/website/js/website.js implementation (not the desk request.js), whose process_response
// unconditionally pops its own desk-style "Message" dialog for any _server_messages -- with no
// `silent` escape hatch -- which is jarring on this bare page and duplicates the toast.
async function call_api(method, args) {
	const response = await fetch(`/api/method/${method}`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			'X-Frappe-CSRF-Token': get_csrf_token(),
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
// JSON-encoded {message, ...} objects) -- never in a plain .message field.
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

// navigator.clipboard is undefined outside a secure context, and a LAN address over plain http is
// not one -- which is exactly how this tool gets used between a phone and a laptop.
async function write_to_clipboard(text) {
	if (navigator.clipboard?.writeText) {
		try {
			await navigator.clipboard.writeText(text);
			return true;
		} catch {
			// fall through to the legacy path below
		}
	}
	const scratch = document.createElement('textarea');
	scratch.value = text;
	scratch.setAttribute('readonly', '');
	scratch.style.position = 'fixed';
	scratch.style.opacity = '0';
	document.body.appendChild(scratch);
	scratch.select();
	const copied = document.execCommand('copy');
	scratch.remove();
	return copied;
}

function file_link(file) {
	return new URL(file.file_url, window.location.origin).href;
}

function release_preview(attachment) {
	if (attachment?.preview_url) {
		URL.revokeObjectURL(attachment.preview_url);
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

function escape_html(text) {
	const holder = document.createElement('div');
	holder.textContent = text;
	return holder.innerHTML;
}

function html_to_text(markup) {
	const holder = document.createElement('div');
	holder.innerHTML = markup;
	return holder.innerText;
}

function sanitize_html(markup) {
	const holder = document.createElement('div');
	holder.innerHTML = markup;
	for (const element of [...holder.querySelectorAll('*')]) {
		if (element.tagName === 'SCRIPT' || element.tagName === 'STYLE') {
			element.remove(); // unwrapping these would leak their source as visible text
			continue;
		}
		if (!ALLOWED_TAGS.has(element.tagName)) {
			element.replaceWith(...element.childNodes);
			continue;
		}
		for (const attribute of [...element.attributes]) {
			const is_safe_href =
				attribute.name === 'href' && /^https?:\/\//i.test(attribute.value);
			if (!is_safe_href) {
				element.removeAttribute(attribute.name);
			}
		}
		if (element.tagName === 'A') {
			element.setAttribute('target', '_blank');
			element.setAttribute('rel', 'noopener noreferrer');
		}
	}
	return holder.innerHTML;
}

function place_caret_at_end(element) {
	const range = document.createRange();
	range.selectNodeContents(element);
	range.collapse(false);
	const selection = window.getSelection();
	selection.removeAllRanges();
	selection.addRange(range);
}

function looks_like_code(text) {
	return text.includes('\n') && /[{};=<>()]|^\s{2,}/m.test(text);
}

// Rich markup is stored as HTML; anything else is stored verbatim, and pasted code earns a code
// block rather than a paragraph that would swallow its indentation.
function get_text_format(rich_mode, text) {
	if (rich_mode) {
		return 'Rich';
	}
	return looks_like_code(text) ? 'Code' : 'Plain';
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
