/**
 * Watermark Studio.
 *
 * The watermark is burned into the video by the encoder (scripts/lib.sh). This page edits the saved settings and
 * previews them with the same font file and the same layout rules the encoder uses:
 *   - sizes are pixels on a 1920x1080 reference frame, scaled to the preview stage (and to the real output height);
 *   - a named position sits `margin` away from the edge it names; a dragged ("custom") position puts the point
 *     (pos_x%, pos_y%) on the watermark's left/centre/right depending on which third of the frame it is in;
 *   - multi-line text is one strip per line, 1.25 x font size apart, each line aligned on its own.
 *
 * Saving never starts or restarts anything. ON/OFF is a separate, explicit request; a settings save never carries it,
 * so a slow autosave or a second tab cannot flip the watermark back. What is live comes from /api/overlay/status.
 */
(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const parseJson = (id, fallback) => {
    try { const el = $(id); return el ? JSON.parse(el.textContent) : fallback; } catch (e) { return fallback; }
  };

  let state = parseJson('overlay-state-init', {});
  const FONTS = parseJson('overlay-fonts-init', []).filter((f) => f.installed);
  const fontByFamily = {};
  FONTS.forEach((f) => { fontByFamily[f.family] = f; });

  // Everything a settings save may send. NOT `visible` and NOT `image_path` - see the header comment.
  const SAVE_KEYS = [
    'mode', 'text', 'font_family', 'font_size', 'font_color', 'font_opacity',
    'outline_enabled', 'outline_color', 'outline_width',
    'box_enabled', 'box_color', 'box_opacity', 'box_padding',
    'shadow_enabled', 'shadow_color', 'shadow_x', 'shadow_y',
    'anchor', 'margin_x', 'margin_y', 'pos_x', 'pos_y', 'image_opacity', 'image_scale_pct',
  ];

  const stage = $('canvas-stage');
  const box = $('draggable-overlay-box');
  const render = $('overlay-content-render');

  function toast(message, type) {
    if (typeof window.toast === 'function') window.toast(message, type || 'ok');
  }

  function csrf() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') : '';
  }

  function rgba(hex, opacityPct) {
    let c = String(hex || '#000000').replace('#', '');
    if (c.length !== 6) c = '000000';
    const n = parseInt(c, 16) || 0;
    const a = Math.max(0, Math.min(1, (opacityPct == null ? 100 : opacityPct) / 100));
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a.toFixed(2)})`;
  }

  function fontCss(family) {
    const f = fontByFamily[family];
    return f ? `"wm-${f.key}", sans-serif` : 'sans-serif';
  }

  // ------------------------------------------------------------------ preview (mirrors the encoder's layout)

  function third(p) { return p > 70 ? 1 : (p > 30 ? 0.5 : 0); }

  function updatePreview() {
    if (!stage || !box || !render) return;
    const scale = (stage.clientWidth || 960) / 1920;
    const image = (state.mode || 'text') === 'image';
    const anchor = state.anchor || 'bottom-right';
    const mx = (Number(state.margin_x) || 0) * scale;
    const my = (Number(state.margin_y) || 0) * scale;
    const px = Number(state.pos_x);
    const py = Number(state.pos_y);

    // ---- position
    let col = 1, row = 1;                                   // 0 = left/top, 0.5 = centre/middle, 1 = right/bottom
    const s = box.style;
    s.left = s.right = s.top = s.bottom = 'auto';
    if (anchor === 'custom') {
      col = third(px); row = third(py);
      s.left = px + '%'; s.top = py + '%';
      s.transform = `translate(${-col * 100}%, ${-row * 100}%)`;
    } else {
      const [v, h] = anchor === 'center' ? ['middle', 'center'] : anchor.split('-');
      col = h === 'left' ? 0 : (h === 'center' ? 0.5 : 1);
      row = v === 'top' ? 0 : (v === 'middle' ? 0.5 : 1);
      let tx = 0, ty = 0;
      if (col === 0) s.left = mx + 'px'; else if (col === 1) s.right = mx + 'px'; else { s.left = '50%'; tx = -50; }
      if (row === 0) s.top = my + 'px'; else if (row === 1) s.bottom = my + 'px'; else { s.top = '50%'; ty = -50; }
      s.transform = `translate(${tx}%, ${ty}%)`;
    }
    const ra = $('readout-anchor'), rx = $('readout-x'), ry = $('readout-y');
    if (ra) ra.textContent = anchor === 'custom' ? 'free (dragged)' : anchor;
    if (rx) rx.textContent = anchor === 'custom' ? px.toFixed(1) + '%' : '–';
    if (ry) ry.textContent = anchor === 'custom' ? py.toFixed(1) + '%' : '–';

    // ---- content
    s.background = 'transparent'; s.padding = '0'; s.borderRadius = '0';
    render.style.cssText = 'display:flex;flex-direction:column;white-space:pre;';
    render.style.alignItems = col === 0 ? 'flex-start' : (col === 0.5 ? 'center' : 'flex-end');
    render.textContent = '';

    if (image) {
      const w = (stage.clientWidth || 960) * (Number(state.image_scale_pct) || 15) / 100;
      const ph = document.createElement('div');
      ph.textContent = state.image_path ? 'YOUR LOGO' : 'NO IMAGE UPLOADED';
      ph.style.cssText = `width:${w}px;height:${w / 2}px;display:flex;align-items:center;justify-content:center;` +
        `border:1px dashed rgba(255,255,255,.8);background:rgba(255,255,255,.18);color:#fff;font-size:${Math.max(9, w / 12)}px;` +
        `opacity:${(state.image_opacity == null ? 100 : state.image_opacity) / 100};`;
      render.appendChild(ph);
      return;
    }

    const size = (Number(state.font_size) || 42) * scale;
    const pad = state.box_enabled ? (Number(state.box_padding) || 0) * scale : 0;
    const opacity = state.font_opacity == null ? 100 : state.font_opacity;
    const lines = String(state.text || '').split('\n').slice(0, 6);
    while (lines.length && !lines[lines.length - 1].trim()) lines.pop();
    lines.forEach((line, i) => {
      const el = document.createElement('div');
      el.textContent = line || ' ';
      let css = `font-family:${fontCss(state.font_family)};font-size:${size}px;line-height:1;` +
        `color:${rgba(state.font_color || '#FFFFFF', opacity)};padding:${pad}px;` +
        `margin-bottom:${i < lines.length - 1 ? size * 0.25 : 0}px;`;
      if (!line.trim()) css += 'visibility:hidden;';
      if (state.box_enabled) css += `background:${rgba(state.box_color, state.box_opacity)};`;
      if (state.outline_enabled) {
        const w = Math.max(1, (Number(state.outline_width) || 1) * scale);
        css += `-webkit-text-stroke:${2 * w}px ${rgba(state.outline_color, opacity)};paint-order:stroke fill;`;
      }
      if (state.shadow_enabled) {
        css += `text-shadow:${(Number(state.shadow_x) || 0) * scale}px ${(Number(state.shadow_y) || 0) * scale}px 0 ${rgba(state.shadow_color, opacity)};`;
      }
      el.style.cssText = css;
      render.appendChild(el);
    });
    if (!lines.length) {
      const el = document.createElement('div');
      el.textContent = '(no text)';
      el.style.cssText = `font-size:${Math.max(10, size * 0.6)}px;color:rgba(255,255,255,.6);font-style:italic;`;
      render.appendChild(el);
    }
  }

  // ------------------------------------------------------------------ header + status

  const btnToggle = $('btn-toggle-overlay');
  const btnToggleText = $('btn-toggle-text');
  const statusBadge = $('overlay-status-badge');
  const statusText = $('overlay-status-text');
  const liveBadge = $('watermark-encoder-badge');
  const liveBadgeText = $('watermark-encoder-text');
  const liveBanner = $('watermark-live-banner');
  const liveText = $('watermark-live-text');
  const problemsBanner = $('watermark-problems');
  const problemsText = $('watermark-problems-text');

  function updateSwitch() {
    const on = !!state.visible;
    if (statusBadge) statusBadge.setAttribute('data-visible', on ? 'true' : 'false');
    if (statusText) statusText.textContent = on ? 'WATERMARK ON' : 'WATERMARK OFF';
    if (btnToggle) btnToggle.className = `btn btn-touch ${on ? 'btn-danger' : 'btn-primary'}`;
    if (btnToggleText) btnToggleText.textContent = on ? 'TURN WATERMARK OFF' : 'TURN WATERMARK ON';
    if (box) box.style.opacity = on ? '1' : '0.45';
  }

  function applyStatus(st) {
    if (!st) return;
    if (typeof st.configured_enabled === 'boolean' && st.configured_enabled !== !!state.visible && !toggleBusy) {
      state.visible = st.configured_enabled;                       // changed elsewhere (another tab / operator)
      updateSwitch();
    }
    if (liveBadge && liveBadgeText) {
      liveBadge.classList.remove('status-active', 'status-waiting', 'status-hidden');
      if (!st.encoder_live) {
        liveBadge.classList.add('status-hidden');
        liveBadgeText.textContent = 'NOT STREAMING';
      } else if (st.live_known && st.live_state === 'on') {
        liveBadge.classList.add('status-active');
        liveBadgeText.textContent = 'LIVE: WATERMARK ON';
      } else if (st.live_known && st.live_state === 'error') {
        liveBadge.classList.add('status-waiting');
        liveBadgeText.textContent = 'LIVE: WATERMARK FAILED';
      } else if (st.live_known) {
        liveBadge.classList.add('status-hidden');
        liveBadgeText.textContent = 'LIVE: NO WATERMARK';
      } else {
        liveBadge.classList.add('status-waiting');
        liveBadgeText.textContent = st.encoder_active ? 'LIVE: WATERMARK ON (UNVERIFIED)' : 'LIVE: UNVERIFIED';
      }
    }
    if (liveBanner && liveText) {
      liveText.textContent = st.message || '';
      liveBanner.classList.remove('banner-info', 'banner-warning', 'banner-error', 'banner-success');
      const failed = st.encoder_live && st.live_known && st.live_state === 'error';
      liveBanner.classList.add(failed ? 'banner-error' : (st.pending ? 'banner-warning' : 'banner-info'));
    }
    if (problemsBanner && problemsText) {
      const list = st.problems || [];
      problemsBanner.hidden = !list.length;
      problemsText.textContent = list.map((p) => p.message).join(' ');
      problemsBanner.classList.toggle('banner-error', list.some((p) => p.level === 'error'));
      problemsBanner.classList.toggle('banner-warning', !list.some((p) => p.level === 'error'));
    }
  }

  async function pollStatus() {
    try {
      const res = await fetch('/api/overlay/status', { headers: { Accept: 'application/json' } });
      if (res.ok) applyStatus(await res.json());
    } catch (e) { /* keep the last known status on screen */ }
  }

  // ------------------------------------------------------------------ saving

  let saveTimer = null;
  let saveSeq = 0;
  let saveChain = Promise.resolve();
  let toggleBusy = false;

  function payload() {
    const out = {};
    SAVE_KEYS.forEach((k) => { if (state[k] !== undefined) out[k] = state[k]; });
    return out;
  }

  function handleSaveResult(data, explicit) {
    if (!data || !data.state) return;
    // Server truth for the two things this page never sends...
    state.visible = !!data.state.visible;
    state.image_path = data.state.image_path || '';
    // ...and for any value the server refused (so the controls stop showing something that was not saved).
    const rejected = data.rejected || {};
    const names = Object.keys(rejected);
    if (names.length) {
      names.forEach((k) => { state[k] = data.state[k]; });
      syncInputs();
      toast('Not saved: ' + names.map((k) => `${k.replace(/_/g, ' ')} (${rejected[k]})`).join('; '), 'err');
    }
    updateSwitch();
    updatePreview();
    applyStatus(data.status);
    if (data.pipeline_synced === false) toast(data.sync_error || 'Saved, but the streaming pipeline could not be updated.', 'err');
    else if (explicit && !names.length) toast(data.message || 'Saved.', data.status && data.status.pending ? 'warn' : 'ok');
  }

  function saveNow(explicit) {
    clearTimeout(saveTimer);
    saveTimer = null;
    const seq = ++saveSeq;
    const body = JSON.stringify(payload());
    saveChain = saveChain.then(async () => {
      try {
        const res = await fetch('/api/overlay', {
          method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf() }, body,
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.ok === false) {
          toast((data && (data.error || data.detail)) || 'The watermark settings could not be saved.', 'err');
          return;
        }
        if (seq === saveSeq) handleSaveResult(data, explicit);      // an older answer never overwrites newer edits
      } catch (e) {
        toast('The watermark settings could not be saved (network error).', 'err');
      }
    });
    return saveChain;
  }

  function queueSave() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => saveNow(false), 500);
  }

  function changed() { updatePreview(); queueSave(); }

  // ------------------------------------------------------------------ ON / OFF

  if (btnToggle) {
    btnToggle.addEventListener('click', async () => {
      if (toggleBusy) return;
      toggleBusy = true;
      btnToggle.disabled = true;
      const want = !state.visible;
      try {
        if (saveTimer) await saveNow(false); else await saveChain;   // settings first, then the switch - in order
        const res = await fetch('/api/overlay/toggle', {
          method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf() },
          body: JSON.stringify({ enabled: want }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.ok === false) {
          toast((data && (data.error || data.detail)) || 'The watermark could not be switched.', 'err');
        } else {
          state.visible = !!data.state.visible;
          updateSwitch();
          applyStatus(data.status);
          if (data.pipeline_synced === false) toast(data.sync_error, 'err');
          else toast(data.message || (state.visible ? 'Watermark ON.' : 'Watermark OFF.'), data.status && data.status.pending ? 'warn' : 'ok');
        }
      } catch (e) {
        toast('The watermark could not be switched (network error).', 'err');
      } finally {
        toggleBusy = false;
        btnToggle.disabled = false;
      }
    });
  }

  const btnSave = $('btn-save-overlay');
  if (btnSave) btnSave.addEventListener('click', () => saveNow(true));

  // ------------------------------------------------------------------ controls

  function bindRange(id, key, readoutId, unit, parse) {
    const el = $(id);
    if (!el) return;
    el.addEventListener('input', () => {
      state[key] = (parse || parseInt)(el.value, 10);
      const r = $(readoutId);
      if (r) r.textContent = state[key] + unit;
      changed();
    });
  }

  function bindColor(pickerId, hexId, key) {
    const picker = $(pickerId), hex = $(hexId);
    if (picker) picker.addEventListener('input', () => {
      state[key] = picker.value.toUpperCase();
      if (hex) hex.value = state[key];
      changed();
    });
    if (hex) hex.addEventListener('input', () => {
      if (/^#[0-9A-Fa-f]{6}$/.test(hex.value)) {
        state[key] = hex.value.toUpperCase();
        if (picker) picker.value = hex.value;
        changed();
      }
    });
  }

  function bindToggle(id, key, bodyId) {
    const el = $(id), body = $(bodyId);
    const paint = () => {
      if (!body) return;
      body.style.opacity = state[key] ? '1' : '0.5';
      body.style.pointerEvents = state[key] ? 'auto' : 'none';
    };
    paint();
    if (el) el.addEventListener('change', () => { state[key] = el.checked; paint(); changed(); });
    return paint;
  }

  const textInput = $('overlay-text-input');
  if (textInput) textInput.addEventListener('input', () => { state.text = textInput.value; changed(); });

  bindRange('overlay-font-size', 'font_size', 'font-size-val', ' px');
  bindRange('overlay-font-opacity', 'font_opacity', 'font-opacity-val', '%');
  bindRange('overlay-outline-width', 'outline_width', 'outline-width-val', ' px');
  bindRange('overlay-box-opacity', 'box_opacity', 'box-opacity-val', '%');
  bindRange('overlay-box-padding', 'box_padding', 'box-padding-val', ' px');
  bindRange('overlay-shadow-x', 'shadow_x', 'shadow-x-val', ' px');
  bindRange('overlay-shadow-y', 'shadow_y', 'shadow-y-val', ' px');
  bindRange('watermark-image-scale', 'image_scale_pct', 'image-scale-val', '%', parseFloat);
  bindRange('watermark-image-opacity', 'image_opacity', 'image-opacity-val', '%');
  bindColor('overlay-font-color', 'overlay-font-color-hex', 'font_color');
  bindColor('overlay-outline-color', 'overlay-outline-color-hex', 'outline_color');
  bindColor('overlay-box-color', 'overlay-box-color-hex', 'box_color');
  bindColor('overlay-shadow-color', 'overlay-shadow-color-hex', 'shadow_color');
  const paintToggles = [
    bindToggle('toggle-outline', 'outline_enabled', 'group-outline-body'),
    bindToggle('toggle-box', 'box_enabled', 'group-box-body'),
    bindToggle('toggle-shadow', 'shadow_enabled', 'group-shadow-body'),
  ];

  const marginX = $('watermark-margin-x'), marginY = $('watermark-margin-y');
  if (marginX) marginX.addEventListener('input', () => { state.margin_x = Math.max(0, parseInt(marginX.value, 10) || 0); changed(); });
  if (marginY) marginY.addEventListener('input', () => { state.margin_y = Math.max(0, parseInt(marginY.value, 10) || 0); changed(); });

  const anchorButtons = document.querySelectorAll('.wm-anchor-btn');
  const modeButtons = document.querySelectorAll('.wm-mode-btn');

  function syncModeUI() {
    const mode = state.mode || 'text';
    modeButtons.forEach((b) => {
      b.classList.toggle('btn-primary', b.dataset.mode === mode);
      b.classList.toggle('btn-secondary', b.dataset.mode !== mode);
      b.setAttribute('aria-pressed', b.dataset.mode === mode ? 'true' : 'false');
    });
    const textSection = $('watermark-text-section'), imageSection = $('watermark-image-section'), deco = $('watermark-decorations');
    if (textSection) textSection.hidden = mode !== 'text';
    if (imageSection) imageSection.hidden = mode !== 'image';
    if (deco) deco.hidden = mode !== 'text';
    anchorButtons.forEach((b) => b.classList.toggle('is-active', b.dataset.anchor === state.anchor));
    const cur = $('watermark-image-current');
    if (cur) cur.textContent = state.image_path ? ('Current: ' + String(state.image_path).split('/').pop()) : 'No image uploaded yet.';
  }

  /** Puts the server's values back into the controls (after a rejected value). */
  function syncInputs() {
    const set = (id, v) => { const el = $(id); if (el && v !== undefined) el.value = v; };
    set('overlay-text-input', state.text);
    set('overlay-font-size', state.font_size); set('overlay-font-opacity', state.font_opacity);
    set('overlay-font-color', state.font_color); set('overlay-font-color-hex', state.font_color);
    set('overlay-outline-color', state.outline_color); set('overlay-outline-color-hex', state.outline_color);
    set('overlay-outline-width', state.outline_width);
    set('overlay-box-color', state.box_color); set('overlay-box-color-hex', state.box_color);
    set('overlay-box-opacity', state.box_opacity); set('overlay-box-padding', state.box_padding);
    set('overlay-shadow-color', state.shadow_color); set('overlay-shadow-color-hex', state.shadow_color);
    set('overlay-shadow-x', state.shadow_x); set('overlay-shadow-y', state.shadow_y);
    set('watermark-margin-x', state.margin_x); set('watermark-margin-y', state.margin_y);
    set('watermark-image-scale', state.image_scale_pct); set('watermark-image-opacity', state.image_opacity);
    const chk = (id, v) => { const el = $(id); if (el) el.checked = !!v; };
    chk('toggle-outline', state.outline_enabled); chk('toggle-box', state.box_enabled); chk('toggle-shadow', state.shadow_enabled);
    paintToggles.forEach((p) => p());
    paintFontTrigger();
    syncModeUI();
  }

  modeButtons.forEach((btn) => btn.addEventListener('click', () => { state.mode = btn.dataset.mode; syncModeUI(); changed(); }));
  anchorButtons.forEach((btn) => btn.addEventListener('click', () => { state.anchor = btn.dataset.anchor; syncModeUI(); changed(); }));

  // ---- image upload
  const upload = $('watermark-image-upload');
  if (upload) {
    upload.addEventListener('change', async () => {
      const file = upload.files && upload.files[0];
      if (!file) return;
      const form = new FormData();
      form.append('image', file);
      try {
        const res = await fetch('/api/overlay/image', { method: 'POST', headers: { 'X-CSRF-Token': csrf() }, body: form });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) { toast(data.detail || 'Upload failed', 'err'); return; }
        if (data.state) { state.image_path = data.state.image_path; state.mode = data.state.mode; }
        syncModeUI();
        updatePreview();
        applyStatus(data.status);
        toast('Watermark image uploaded. ' + (data.message || ''), data.status && data.status.pending ? 'warn' : 'ok');
      } catch (e) {
        toast('Upload failed', 'err');
      }
    });
  }

  // ------------------------------------------------------------------ dragging (free position)

  let drag = null;

  /**
   * The free-position value (percent) that puts a watermark of `size` percent with its centre at `centre` percent.
   * The encoder anchors a free position by the watermark's near edge, centre or far edge depending on which third of
   * the frame the value is in (third()), so the value is chosen to be consistent with that rule - otherwise the
   * watermark would jump as it is dragged across a third.
   */
  function anchorPoint(centre, size) {
    const half = size / 2;
    const c = Math.max(half, Math.min(100 - half, centre));          // keep the whole watermark inside the frame
    const round = (v) => Math.max(0, Math.min(100, Math.round(v * 10) / 10));
    if (c > 30 && c <= 70) return round(c);                          // anchored by its centre
    if (c - half <= 30) return round(c - half);                      // by its near (left/top) edge
    if (c + half > 70) return round(c + half);                       // by its far (right/bottom) edge
    return round(c);
  }

  function onDown(e) {
    if (!stage || !box) return;
    if (e.button !== undefined && e.button !== 0) return;
    const r = box.getBoundingClientRect(), sr = stage.getBoundingClientRect();
    // The watermark's centre follows the pointer; where it is NOW and how big it is, in percent of the frame.
    drag = {
      x: e.clientX, y: e.clientY,
      cx: ((r.left + r.width / 2) - sr.left) / sr.width * 100,
      cy: ((r.top + r.height / 2) - sr.top) / sr.height * 100,
      w: r.width / sr.width * 100, h: r.height / sr.height * 100,
      moved: false,
    };
    box.setPointerCapture && e.pointerId !== undefined && box.setPointerCapture(e.pointerId);
    e.preventDefault();
  }

  function onMove(e) {
    if (!drag || !stage) return;
    const sr = stage.getBoundingClientRect();
    const dx = (e.clientX - drag.x) / sr.width * 100, dy = (e.clientY - drag.y) / sr.height * 100;
    if (!drag.moved && Math.abs(e.clientX - drag.x) + Math.abs(e.clientY - drag.y) < 4) return;   // a click is not a drag
    drag.moved = true;
    box.classList.add('is-dragging');
    state.anchor = 'custom';
    state.pos_x = anchorPoint(drag.cx + dx, drag.w);
    state.pos_y = anchorPoint(drag.cy + dy, drag.h);
    updatePreview();
  }

  function onUp() {
    if (!drag) return;
    const moved = drag.moved;
    drag = null;
    if (box) box.classList.remove('is-dragging');
    if (moved) { syncModeUI(); queueSave(); }
  }

  if (box) {
    box.addEventListener('pointerdown', onDown);
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    window.addEventListener('pointercancel', onUp);
  }

  // ------------------------------------------------------------------ font picker

  const fontSelect = $('overlay-font-family');
  const triggerName = $('font-trigger-name');
  const triggerBadge = $('font-trigger-badge');

  function paintFontTrigger() {
    const f = fontByFamily[state.font_family];
    if (triggerName) {
      triggerName.textContent = f ? state.font_family : `${state.font_family} (not installed)`;
      triggerName.style.fontFamily = fontCss(state.font_family);
    }
    if (triggerBadge) triggerBadge.textContent = f && (f.scripts || []).includes('sinhala') ? 'අආ ශ්‍රී' : 'Ag 123';
  }

  function initFontPicker() {
    const picker = $('custom-font-picker');
    if (!picker) return;
    const trigger = $('font-picker-trigger');
    const dropdown = $('font-picker-dropdown');
    const search = $('font-picker-search');
    const clear = $('font-picker-search-clear');
    const list = $('font-picker-list');
    const empty = $('font-picker-empty');
    const items = Array.from(picker.querySelectorAll('.font-picker-item'));
    const groups = Array.from(picker.querySelectorAll('.font-picker-category-group'));

    // A specimen is only drawn in its own font (= its file is only downloaded) once it scrolls into view.
    const paintItem = (item) => {
      if (item.dataset.painted) return;
      item.dataset.painted = '1';
      const css = `"wm-${item.dataset.key}", sans-serif`;
      item.querySelectorAll('.font-item-name, .font-item-preview').forEach((el) => { el.style.fontFamily = css; });
    };
    const io = 'IntersectionObserver' in window
      ? new IntersectionObserver((entries) => entries.forEach((en) => { if (en.isIntersecting) paintItem(en.target); }), { root: list, rootMargin: '200px' })
      : null;
    items.forEach((item) => { if (io) io.observe(item); else paintItem(item); });

    const open = () => {
      picker.classList.add('is-open');
      if (trigger) trigger.setAttribute('aria-expanded', 'true');
      if (dropdown) dropdown.removeAttribute('hidden');
      if (search) { search.focus(); search.select(); }
      const active = picker.querySelector('.font-picker-item.is-selected');
      if (active) active.scrollIntoView({ block: 'nearest' });
    };
    const close = () => {
      picker.classList.remove('is-open');
      if (trigger) trigger.setAttribute('aria-expanded', 'false');
      if (dropdown) dropdown.setAttribute('hidden', '');
    };

    if (trigger) trigger.addEventListener('click', (e) => { e.stopPropagation(); picker.classList.contains('is-open') ? close() : open(); });
    document.addEventListener('click', (e) => { if (!picker.contains(e.target)) close(); });
    picker.addEventListener('keydown', (e) => { if (e.key === 'Escape') { close(); if (trigger) trigger.focus(); } });

    const select = (family) => {
      if (!fontByFamily[family]) return;
      state.font_family = family;
      if (fontSelect) fontSelect.value = family;
      items.forEach((item) => {
        const hit = item.dataset.font === family;
        item.classList.toggle('is-selected', hit);
        item.setAttribute('aria-selected', hit ? 'true' : 'false');
      });
      paintFontTrigger();
      close();
      if (trigger) trigger.focus();
      changed();
      // Redraw once the font file has arrived, so the preview is not left showing the fallback face.
      if (document.fonts && document.fonts.load) {
        document.fonts.load(`20px "wm-${fontByFamily[family].key}"`).then(updatePreview).catch(() => {});
      }
    };

    items.forEach((item) => {
      item.addEventListener('click', (e) => { e.stopPropagation(); select(item.dataset.font); });
      item.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); select(item.dataset.font); }
      });
    });

    const filter = () => {
      const q = (search.value || '').trim().toLowerCase();
      if (clear) clear.hidden = !q;
      let visible = 0;
      groups.forEach((grp) => {
        const cat = (grp.dataset.category || '').toLowerCase();
        let n = 0;
        grp.querySelectorAll('.font-picker-item').forEach((item) => {
          const hit = !q || (item.dataset.font || '').toLowerCase().includes(q) || cat.includes(q);
          item.style.display = hit ? 'flex' : 'none';
          if (hit) { n++; visible++; }
        });
        grp.style.display = n ? '' : 'none';
      });
      if (empty) empty.hidden = visible > 0;
    };
    if (search) {
      search.addEventListener('input', filter);
      search.addEventListener('keydown', (e) => {
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          const first = items.find((el) => el.style.display !== 'none');
          if (first) first.focus();
        }
      });
    }
    if (clear) clear.addEventListener('click', (e) => { e.stopPropagation(); search.value = ''; filter(); search.focus(); });
    if (list) {
      list.addEventListener('keydown', (e) => {
        if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
        const shown = items.filter((el) => el.style.display !== 'none');
        const idx = shown.indexOf(document.activeElement);
        e.preventDefault();
        if (e.key === 'ArrowDown') { const next = shown[idx + 1] || shown[0]; if (next) next.focus(); }
        else if (idx <= 0) { if (search) search.focus(); }
        else shown[idx - 1].focus();
      });
    }
  }

  // ------------------------------------------------------------------ start

  window.addEventListener('resize', updatePreview);
  initFontPicker();
  paintFontTrigger();
  syncModeUI();
  updateSwitch();
  updatePreview();
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(updatePreview).catch(() => {});
  pollStatus();
  setInterval(pollStatus, 5000);
  // Leaving the page with an edit still waiting for its autosave: send it now.
  window.addEventListener('pagehide', () => {
    if (!saveTimer) return;
    try {
      fetch('/api/overlay', { method: 'POST', keepalive: true, headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf() }, body: JSON.stringify(payload()) });
    } catch (e) { /* nothing more can be done while unloading */ }
  });
})();
