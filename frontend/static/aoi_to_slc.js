// rosarium webmap — "Browse & download" tab (aoi_to_slc): search the CDSE
// catalogue, tick products, download them into data/raw/<folder>/ (a job,
// followed by job.js), or only write their S3 paths. Uses the map, AOI,
// status and api() of map.js.

// ─── STATE ───────────────────────────────────────────────────────
let results = [];           // features returned by /api/search
let selected = new Set();   // product names
const layersByName = {};    // product name -> Leaflet layer of its footprint

// Added to the map by the tab hook below, when this tab is shown
const footLayer = L.geoJSON(EMPTY, { style: FOOT_STYLE, onEachFeature: onFootprint });

// ─── SEARCH ──────────────────────────────────────────────────────
function search() {
  guard(async () => {
    if (!aoi) throw new Error("no AOI: draw one on the map, or paste one and click 'Use this AOI'");
    if (!$('start').value || !$('end').value) throw new Error('both dates are needed');
    status('Searching the CDSE catalogue…', 'busy');
    $('searchBtn').disabled = true;
    try {
      const js = await api('/api/search', {
        aoi, start: $('start').value, end: $('end').value,
        product_type: $('product').value, mode: $('mode').value || null,
        orbit_direction: $('orbit').value || null, platforms: $('platform').value || null,
      });
      results = js.features; selected.clear();
      footLayer.clearLayers();
      Object.keys(layersByName).forEach(k => delete layersByName[k]);
      footLayer.addData(js);
      renderList(); syncSelection();
      const gb = results.reduce((a, f) => a + (f.properties.size_gb || 0), 0);
      const more = js.truncated ? ' — more in the catalogue, narrow the dates' : '';
      status(`${results.length} product(s), ${gb.toFixed(0)} GB in total${more}.`);
    } finally {
      $('searchBtn').disabled = false;
    }
  });
}

// ─── FOOTPRINTS ──────────────────────────────────────────────────
function onFootprint(feature, layer) {
  const name = feature.properties.name;
  layersByName[name] = layer;
  layer.bindTooltip(name, { className: 'fp', sticky: true });
  layer.on('mouseover', () => { if (!selected.has(name)) layer.setStyle(HOVER_STYLE); rowOf(name)?.classList.add('hover'); });
  layer.on('mouseout',  () => { restyle(name); rowOf(name)?.classList.remove('hover'); });
  layer.on('click', () => toggle(name));
}
function restyle(name) {
  const layer = layersByName[name];
  if (!layer) return;
  layer.setStyle(selected.has(name) ? SEL_STYLE : FOOT_STYLE);
  if (selected.has(name)) layer.bringToFront();
}

// ─── LIST ────────────────────────────────────────────────────────
function rowOf(name) { return $('list').querySelector(`.item[data-name="${CSS.escape(name)}"]`); }
function renderList() {
  const list = $('list');
  list.innerHTML = '';
  $('listCount').textContent = results.length ? `· ${results.length}` : '';
  if (!results.length) { list.innerHTML = '<div class="empty">No product listed</div>'; return; }
  for (const f of results) {
    const p = f.properties;
    const div = document.createElement('div');
    div.className = 'item';
    div.dataset.name = p.name;
    div.title = p.name;
    const date = p.datetime.slice(0, 16).replace('T', ' ');
    const orbit = (p.orbit || '?').slice(0, 4);
    const size = p.size_gb == null ? '?' : `${p.size_gb.toFixed(1)} GB`;
    div.innerHTML = `
      <div class="box"></div>
      <div class="body">
        <div class="date">${date} · ${p.platform}</div>
        <div class="sub">${orbit} · rel ${p.relative_orbit ?? '?'} · ${p.product_type} · ${p.polarisations}</div>
      </div>
      <div class="size">${size}</div>`;
    div.onclick = () => toggle(p.name);
    div.onmouseenter = () => { if (!selected.has(p.name)) layersByName[p.name]?.setStyle(HOVER_STYLE); };
    div.onmouseleave = () => restyle(p.name);
    list.appendChild(div);
  }
}
function toggle(name) {
  if (selected.has(name)) selected.delete(name); else selected.add(name);
  syncSelection();
}
function selectAll(on) {
  selected.clear();
  if (on) results.forEach(f => selected.add(f.properties.name));
  syncSelection();
}
function selectedRows() { return results.filter(f => selected.has(f.properties.name)); }
function syncSelection() {
  $('list').querySelectorAll('.item').forEach(el => el.classList.toggle('sel', selected.has(el.dataset.name)));
  Object.keys(layersByName).forEach(restyle);
  const rows = selectedRows();
  $('preview').value = rows.map(f => f.properties.path).join('\n');
  const gb = rows.reduce((a, f) => a + (f.properties.size_gb || 0), 0);
  if (activeTab === 'browse')
    $('selInfo').textContent = rows.length ? `${rows.length} selected · ${gb.toFixed(1)} GB` : '0 selected';
}

// ─── DOWNLOAD ────────────────────────────────────────────────────
function downloadSelected() {
  guard(async () => {
    const rows = selectedRows();
    if (!rows.length) throw new Error('nothing selected');
    followJob(await api('/api/download', {
      products: rows.map(f => ({ name: f.properties.name, s3_key: f.properties.s3_key })),
      folder: $('dlFolder').value,
      aoi,
    }));
    status(`Downloading ${rows.length} product(s)…`, 'busy');
  });
}

// ─── PATH FILE ───────────────────────────────────────────────────
function writePaths() {
  guard(async () => {
    const rows = selectedRows();
    if (!rows.length) throw new Error('nothing selected');
    const js = await api('/api/write', {
      products: rows.map(f => ({ name: f.properties.name, s3_key: f.properties.s3_key })),
      path_file: $('pathFile').value,
      aoi: $('withAoi').checked ? aoi : null,
    });
    status(`${js.count} S3 path(s) written to ${js.path}${js.aoi_path ? ' (+ ' + js.aoi_path + ')' : ''}.`);
    $('pathInfo').textContent = js.path;
  });
}

// ─── HOOKS INTO THE SHARED PAGE ──────────────────────────────────
clearHooks.push(() => {
  results = []; selected.clear();
  footLayer.clearLayers();
  Object.keys(layersByName).forEach(k => delete layersByName[k]);
  renderList(); syncSelection();
});
initHooks.push(cfg => {
  $('start').value = cfg.start;
  $('end').value = cfg.end;
  $('pathFile').value = cfg.path_file;
  $('dlFolder').placeholder = cfg.default_folder;
  $('pathInfo').textContent = `${cfg.path_file} (${cfg.style})`;
});
tabHooks.push(name => {
  if (name === 'browse') { footLayer.addTo(map); syncSelection(); }
  else map.removeLayer(footLayer);
});
