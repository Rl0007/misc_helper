// Avatar rendering for the clipboard pages, from the real npm packages.
//
// This replaces an ESM import of @dicebear off jsdelivr. Two reasons it is worth bundling:
//
//   1. `planets` exists only in DiceBear v10, which ships styles as JSON definitions in
//      @dicebear/styles rather than as a `collection` barrel -- the v9 import shape cannot reach it.
//   2. A tool whose pitch is that third-party clipboard sites are untrusted should not tell a CDN
//      who is looking at which page. Bundled, the avatars are same-origin and work offline.
//
// A `*.bundle.js` name is load-bearing: Frappe's esbuild only picks up files matching it, and it
// is what gives this file a content hash (unlike the raw .js files this app serves, which need a
// hand-bumped ?v=).
//
// Built with: bench build --app misc_helper
import { Avatar, Style } from '@dicebear/core';
import bottts from '@dicebear/styles/bottts.json';
import notionists from '@dicebear/styles/notionists.json';
import planets from '@dicebear/styles/planets.json';

// Style instances are built once and reused: the v10 docs are explicit that a Style should be
// constructed once and shared across avatars, and a busy room asks for a dozen of these per render.
const STYLES = {
	notionists: new Style(notionists),
	bottts: new Style(bottts),
	planets: new Style(planets),
};

// Returns null for an unknown style rather than throwing, so a caller can fall back to the HTTP
// API instead of rendering a broken image.
window.render_avatar = (style_name, seed) => {
	const style = STYLES[style_name];
	if (!style) {
		return null;
	}
	return new Avatar(style, { seed }).toDataUri();
};

// The component re-runs every avatar getter once this lands, because the getters may already have
// run against the (missing) local renderer and fallen back to a URL.
window.dispatchEvent(new Event('dicebear-ready'));
