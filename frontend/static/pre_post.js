// rosarium webmap — pre/post tab: search the products before and after an
// event, pick 2 GRD or 4 SLC, then download + preprocess them in one job.
// Uses the map, AOI, status and api() of map.js, followJob() of job.js.
//
// Which products go together — what the SNAP graphs need:
//   GRD  the two images are coregistered in radar geometry before terrain
//        correction: same relative orbit (and pass direction). The framing
//        may differ, each only has to cover the AOI.
//   SLC  the burst numbers are computed on one product and applied as is to
//        its pair: same relative orbit AND same framing, i.e. footprints
//        centred within SAME_FRAME_KM (a burst is ~20 km along the track, so
//        a product shifted by one burst is left out).
// A product that covers only part of the AOI is drawn dashed and cannot be
// picked; a full side hides the rest of that side.

const PP_PER_SIDE = { GRD: 1, SLC: 2 };
const SAME_FRAME_KM = 5;
const SIDES = ['pre', 'post'];
const SIDE_COLOR = { pre: '#38bdf8', post: '#f472b6' };
const PP_MODE_HINT = {
  GRD: 'One GRD before the event, one after, on the same relative orbit → gamma0 VH/VV.',
  SLC: 'Two SLC before the event, two after, all on the same track and framing → gamma0 + coherence VH/VV.',
};

// ─── STATE ───────────────────────────────────────────────────────
let ppMode = 'GRD';
const ppResults = { pre: [], post: [] };     // GeoJSON features of each search
const ppSelected = { pre: [], post: [] };    // features picked, in click order
const ppLayers = { pre: L.layerGroup(), post: L.layerGroup() };
const ppLayerOf = new Map();                 // feature -> its Leaflet layer

// ─── MODE ────────────────────────────────────────────────────────
function ppSetMode(mode) {
  ppMode = mode;
  document.querySelectorAll('.seg .btn').forEach(b => b.classList.toggle('active', b.dataset.mode === mode));
  $('ppModeHint').textContent = PP_MODE_HINT[mode];
  ppClearResults();
}

// ─── SEARCH ──────────────────────────────────────────────────────
function ppSearch() {
  guard(async () => {
    if (!aoi) throw new Error("no AOI: draw one on the map, or paste one and click 'Use this AOI'");
    const d = { preStart: $('ppPreStart').value, preEnd: $('ppPreEnd').value,
                postStart: $('ppPostStart').value, postEnd: $('ppPostEnd').value };
    if (Object.values(d).some(v => !v)) throw new Error('the four dates are needed');
    if (d.preStart > d.preEnd || d.postStart > d.postEnd) throw new Error('a "from" date is after its "to" date');
    if (d.preEnd >= d.postStart) throw new Error('the pre interval must end before the post interval starts');
    $('ppSearchBtn').disabled = true;
    try {
      const common = {
        aoi, product_type: ppMode, mode: $('ppAcq').value || null,
        orbit_direction: $('ppOrbit').value || null, platforms: $('ppPlatform').value || null,
      };
      status(`Searching ${ppMode} before the event…`, 'busy');
      const pre = await api('/api/search', { ...common, start: d.preStart, end: d.preEnd });
      status(`Searching ${ppMode} after the event…`, 'busy');
      const post = await api('/api/search', { ...common, start: d.postStart, end: d.postEnd });
      ppClearResults();
      ppResults.pre = pre.features.map(f => ({ ...f, side: 'pre' }));
      ppResults.post = post.features.map(f => ({ ...f, side: 'post' }));
      ppBuildLayers();
      ppRefresh();
      const more = pre.truncated || post.truncated ? ' — more in the catalogue, narrow the dates' : '';
      status(`${ppResults.pre.length} pre, ${ppResults.post.length} post ${ppMode} product(s)${more}.`);
    } finally {
      $('ppSearchBtn').disabled = false;
    }
  });
}

// ─── WHICH PRODUCT CAN BE PICKED ─────────────────────────────────
function kmBetween([lon1, lat1], [lon2, lat2]) {
  const r = Math.PI / 180, dLat = (lat2 - lat1) * r, dLon = (lon2 - lon1) * r;
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin(dLon / 2) ** 2;
  return 12742 * Math.asin(Math.sqrt(a));
}
function compatible(a, b) {
  if (a.relative_orbit !== b.relative_orbit || a.orbit !== b.orbit) return false;
  return ppMode !== 'SLC' || kmBetween(a.centroid, b.centroid) <= SAME_FRAME_KM;
}
function isSelected(f) { return ppSelected[f.side].includes(f); }
// 'selected' | 'ok' | 'partial' (does not cover the whole AOI) | 'hidden'
function ppState(f) {
  if (isSelected(f)) return 'selected';
  if (!f.properties.covers_aoi) return 'partial';
  if (ppSelected[f.side].length >= PP_PER_SIDE[ppMode]) return 'hidden';
  const others = [...ppSelected.pre, ...ppSelected.post];
  if (others.some(s => !compatible(f.properties, s.properties))) return 'hidden';
  if (others.some(s => s.properties.datetime === f.properties.datetime)) return 'hidden';
  return 'ok';
}

// ─── MAP ─────────────────────────────────────────────────────────
function ppStyle(f, state, hover = false) {
  const color = SIDE_COLOR[f.side];
  if (state === 'selected') return { color, weight: 3, fillOpacity: 0.25, dashArray: null };
  if (state === 'partial') return { color, weight: 1, fillOpacity: 0, dashArray: '4 4', opacity: 0.6 };
  return { color, weight: hover ? 2.5 : 1.2, fillOpacity: hover ? 0.18 : 0.05, dashArray: null, opacity: 0.9 };
}
function ppBuildLayers() {
  for (const side of SIDES) {
    for (const f of ppResults[side]) {
      const layer = L.geoJSON(f, { style: () => ppStyle(f, ppState(f)) });
      layer.bindTooltip(`${side} · ${f.properties.name}`, { className: 'fp', sticky: true });
      layer.on('mouseover', () => { if (ppState(f) === 'ok') layer.setStyle(ppStyle(f, 'ok', true)); ppRowOf(f)?.classList.add('hover'); });
      layer.on('mouseout', () => { layer.setStyle(ppStyle(f, ppState(f))); ppRowOf(f)?.classList.remove('hover'); });
      layer.on('click', e => { L.DomEvent.stopPropagation(e); ppPickAt(e.latlng); });
      ppLayerOf.set(f, layer);
    }
  }
}

// Footprints of the same track pile up: a click lists every pickable one
// under the cursor when there are several
function pointInRing([x, y], ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i], [xj, yj] = ring[j];
    if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}
function contains(geom, pt) {
  const polys = geom.type === 'Polygon' ? [geom.coordinates] : geom.type === 'MultiPolygon' ? geom.coordinates : [];
  return polys.some(p => pointInRing(pt, p[0]) && !p.slice(1).some(h => pointInRing(pt, h)));
}
function ppPickAt(latlng) {
  const pt = [latlng.lng, latlng.lat];
  const hits = [...ppResults.pre, ...ppResults.post].filter(f =>
    ppShown(f) && ['ok', 'selected'].includes(ppState(f)) && contains(f.geometry, pt));
  if (hits.length === 1) { ppToggle(hits[0]); return; }
  if (!hits.length) return;
  hits.sort((a, b) => a.properties.datetime.localeCompare(b.properties.datetime));
  const div = document.createElement('div');
  hits.forEach(f => {
    const b = document.createElement('button');
    b.innerHTML = `<span class="dot ${f.side}"></span>${isSelected(f) ? '✓ ' : ''}${f.side} · ${f.properties.datetime.slice(0, 16).replace('T', ' ')} · ${f.properties.platform}`;
    b.onclick = () => { map.closePopup(); ppToggle(f); };
    div.appendChild(b);
  });
  L.popup({ className: 'pick-popup', maxWidth: 320 }).setLatLng(latlng).setContent(div).openOn(map);
}

// ─── SELECTION ───────────────────────────────────────────────────
function ppToggle(f) {
  const list = ppSelected[f.side];
  if (list.includes(f)) list.splice(list.indexOf(f), 1);
  else if (ppState(f) === 'ok') list.push(f);
  else return;
  ppRefresh();
}
function ppClearSelection() { ppSelected.pre = []; ppSelected.post = []; ppRefresh(); }
function ppClearResults() {
  for (const side of SIDES) { ppResults[side] = []; ppSelected[side] = []; ppLayers[side].clearLayers(); }
  ppLayerOf.clear();
  ppRefresh();
}
function ppShown(f) { return $(f.side === 'pre' ? 'ppShowPre' : 'ppShowPost').checked; }

// Map, list, slots and buttons all follow the selection
function ppRefresh() {
  for (const side of SIDES) {
    ppLayers[side].clearLayers();
    for (const f of ppResults[side]) {
      const state = ppState(f);
      if (state === 'hidden' || !ppShown(f)) continue;
      const layer = ppLayerOf.get(f);
      layer.setStyle(ppStyle(f, state));
      ppLayers[side].addLayer(layer);
    }
  }
  SIDES.forEach(side => ppSelected[side].forEach(f => ppLayerOf.get(f)?.bringToFront()));
  ppRenderList();
  ppRenderSlots();
  ppRenderWarning();
}

// ─── LIST ────────────────────────────────────────────────────────
function ppRowOf(f) { return $('ppList').querySelector(`.item[data-key="${CSS.escape(f.side + '|' + f.properties.name)}"]`); }
function ppRenderList() {
  const list = $('ppList');
  list.innerHTML = '';
  const n = ppResults.pre.length + ppResults.post.length;
  $('ppCount').textContent = n ? `· ${ppResults.pre.length} pre · ${ppResults.post.length} post` : '';
  if (!n) { list.innerHTML = '<div class="empty">No search yet</div>'; return; }
  for (const side of SIDES) {
    const group = document.createElement('div');
    group.className = 'list-group';
    group.innerHTML = `<span class="dot ${side}"></span>${side}-event · ${ppResults[side].length}`;
    list.appendChild(group);
    for (const f of ppResults[side]) {
      const p = f.properties, state = ppState(f);
      const div = document.createElement('div');
      div.className = 'item' + (state === 'selected' ? ' sel' : '') + (state === 'partial' || state === 'hidden' ? ' dim' : '');
      div.dataset.key = side + '|' + p.name;
      div.title = p.name + (state === 'partial' ? ' — covers only part of the AOI' : '');
      const date = p.datetime.slice(0, 16).replace('T', ' ');
      const size = p.size_gb == null ? '?' : `${p.size_gb.toFixed(1)} GB`;
      div.innerHTML = `
        <div class="side ${side}"></div>
        <div class="box"></div>
        <div class="body">
          <div class="date">${date} · ${p.platform}</div>
          <div class="sub">${(p.orbit || '?').slice(0, 4)} · rel ${p.relative_orbit ?? '?'} · ${p.polarisations}
            ${state === 'partial' ? '<span class="tag">· partial AOI</span>' : ''}</div>
        </div>
        <div class="size">${size}</div>`;
      if (state === 'ok' || state === 'selected') {
        div.onclick = () => ppToggle(f);
        div.onmouseenter = () => { if (state === 'ok' && ppShown(f)) ppLayerOf.get(f)?.setStyle(ppStyle(f, 'ok', true)); };
        div.onmouseleave = () => ppLayerOf.get(f)?.setStyle(ppStyle(f, ppState(f)));
      }
      list.appendChild(div);
    }
  }
}

// ─── SLOTS: pre1, pre2, post1, post2 by date, as the pipeline uses them ──
function ppSorted(side) { return [...ppSelected[side]].sort((a, b) => a.properties.datetime.localeCompare(b.properties.datetime)); }
function ppRenderSlots() {
  const n = PP_PER_SIDE[ppMode];
  const html = [];
  for (const side of SIDES) {
    const picked = ppSorted(side);
    for (let i = 0; i < n; i++) {
      const f = picked[i];
      const role = n === 1 ? side : `${side} ${i + 1}`;
      if (!f) { html.push(`<div class="slot"><span class="role"><span class="dot ${side}"></span>${role}</span><span class="what">—</span></div>`); continue; }
      const p = f.properties;
      html.push(`<div class="slot filled ${side}" data-key="${side}|${p.name}" title="${p.name} — click to unselect">
        <span class="role"><span class="dot ${side}"></span>${role}</span>
        <span class="what">${p.datetime.slice(0, 10)} · ${p.platform} · rel ${p.relative_orbit}</span><span class="x">✕</span></div>`);
    }
  }
  $('ppSlots').innerHTML = html.join('');
  $('ppSlots').querySelectorAll('.slot.filled').forEach(el => {
    const [side, name] = el.dataset.key.split('|');
    el.onclick = () => ppToggle(ppSelected[side].find(f => f.properties.name === name));
  });
  const all = [...ppSelected.pre, ...ppSelected.post];
  const gbTotal = all.reduce((a, f) => a + (f.properties.size_gb || 0), 0);
  $('ppSelSize').textContent = all.length ? `${gbTotal.toFixed(1)} GB` : '';
  $('ppRunBtn').disabled = !(ppSelected.pre.length === n && ppSelected.post.length === n);
  if (activeTab === 'pre_post') $('selInfo').textContent = `${all.length} / ${2 * n} selected`;
}

// No product of a side covers the whole AOI: it straddles two products along
// the track, which the pipelines do not stitch
function ppRenderWarning() {
  const sides = SIDES.filter(s => ppResults[s].length && !ppResults[s].some(f => f.properties.covers_aoi));
  $('ppWarn').hidden = !sides.length;
  $('ppWarn').textContent = sides.length
    ? `No ${sides.join(' / ')} product covers the whole AOI: it straddles two products along the track. ` +
      'Split the AOI in two and process each half separately. A fix for this case is coming.'
    : '';
}

// ─── OUTPUT ──────────────────────────────────────────────────────
// The preprocessed name follows the raw folder until typed in
function ppSyncName() {
  const raw = $('ppRaw').value.trim() || config.default_folder;
  $('ppName').placeholder = raw;
  ppShowWhere();
}
function ppShowWhere() {
  const raw = $('ppRaw').value.trim() || config.default_folder;
  const name = $('ppName').value.trim() || raw;
  $('ppWhere').textContent = `→ data/raw/${raw}/ and data/preprocessed/pre_post/${name}/`;
}
function ppProducts(side) {
  return ppSorted(side).map(f => ({ name: f.properties.name, s3_key: f.properties.s3_key, datetime: f.properties.datetime }));
}
function ppAllSelected() { return [...ppProducts('pre'), ...ppProducts('post')]; }

function ppRun() {
  guard(async () => {
    const body = {
      mode: ppMode, aoi, pre: ppProducts('pre'), post: ppProducts('post'),
      raw_folder: $('ppRaw').value, name: $('ppName').value,
    };
    const check = await api('/api/pre_post/check', body);
    if (check.existing.length &&
        !confirm(`These files already exist and will be overwritten:\n\n${check.existing.join('\n')}\n\nContinue?`)) return;
    followJob(await api('/api/pre_post/run', { ...body, overwrite: true }));
    status(`Running: data/raw/${check.raw_folder}/ → data/preprocessed/pre_post/${check.name}/`, 'busy');
  });
}

function ppDownloadOnly() {
  guard(async () => {
    const products = ppAllSelected();
    if (!products.length) throw new Error('nothing selected');
    followJob(await api('/api/download', { products, folder: $('ppRaw').value, aoi }));
    status(`Downloading ${products.length} product(s)…`, 'busy');
  });
}

function ppWritePaths() {
  guard(async () => {
    const products = ppAllSelected();
    if (!products.length) throw new Error('nothing selected');
    const folder = $('ppRaw').value.trim() || config.default_folder;
    const js = await api('/api/write', { products, path_file: `data/utils/${folder}.txt`, aoi });
    status(`${js.count} S3 path(s) written to ${js.path} (+ ${js.aoi_path}).`);
    $('pathInfo').textContent = js.path;
  });
}

// ─── HOOKS INTO THE SHARED PAGE ──────────────────────────────────
clearHooks.push(ppClearResults);
tabHooks.push(name => {
  for (const side of SIDES) {
    if (name === 'pre_post') ppLayers[side].addTo(map); else map.removeLayer(ppLayers[side]);
  }
  if (name === 'pre_post') ppRenderSlots();
});
initHooks.push(cfg => {
  // Post: the last days of the default range; pre: as long, just before
  const end = new Date(cfg.end), start = new Date(cfg.start);
  const span = end - start, day = 86400000;
  const iso = d => d.toISOString().slice(0, 10);
  $('ppPostStart').value = cfg.start;
  $('ppPostEnd').value = cfg.end;
  $('ppPreEnd').value = iso(new Date(start - day));
  $('ppPreStart').value = iso(new Date(start - day - span));
  $('ppRaw').placeholder = cfg.default_folder;
  $('ppName').oninput = ppShowWhere;
  ppSyncName();
  ppSetMode('GRD');
});
