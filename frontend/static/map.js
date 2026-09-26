// rosarium webmap — the shared part: map, basemaps, AOI, status line, API calls,
// tabs. Loaded before the feature scripts, which use what is declared here.

// ─── SHARED STATE ────────────────────────────────────────────────
let aoi = null;             // GeoJSON geometry, lon/lat — the one AOI of the page
let config = {};            // /api/config, filled by init()

const $ = id => document.getElementById(id);
const EMPTY = { type: 'FeatureCollection', features: [] };
const AOI_STYLE  = { color: '#ff6b35', weight: 2, fillOpacity: 0.05 };
const FOOT_STYLE = { color: '#64748b', weight: 1, fillOpacity: 0.03 };
const HOVER_STYLE = { color: '#00d4ff', weight: 2, fillOpacity: 0.12 };
const SEL_STYLE  = { color: '#4ade80', weight: 2.5, fillOpacity: 0.15 };

// ─── MAP ─────────────────────────────────────────────────────────
// [tile url, subdomains, css class]. Key-free sources only.
const OSM = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png';
const BASEMAPS = {
  dark:      [OSM, 'abc', 'dark-tiles'],
  satellite: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', '', ''],
  osm:       [OSM, 'abc', ''],
};
const map = L.map('map', { center: [46.5, 2.5], zoom: 6, preferCanvas: false });
let basemap = null;
function setBasemap(key, btn) {
  if (basemap) map.removeLayer(basemap);
  const [url, subdomains, className] = BASEMAPS[key];
  basemap = L.tileLayer(url, { maxZoom: 19, subdomains, className, attribution: '' }).addTo(map);
  basemap.bringToBack();
  if (btn) {
    document.querySelectorAll('.basemap-btns .btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
  }
}
setBasemap('dark');
L.control.scale({ position: 'bottomright', imperial: false }).addTo(map);
map.on('mousemove', e => {
  $('coords').textContent = `${e.latlng.lat.toFixed(5)}°  ${e.latlng.lng.toFixed(5)}°`;
});

const aoiLayer = L.geoJSON(EMPTY, { style: AOI_STYLE, interactive: false }).addTo(map);

// ─── AREA ────────────────────────────────────────────────────────
// Leaflet.draw 1.0.4's readableArea assigns an undeclared variable (`type`),
// which throws with recent Leaflet as soon as an area is shown, and never
// uses km² anyway: replaced by a km² formatter, used by the draw tooltips.
function formatKm2(km2) {
  const digits = km2 >= 100 ? 0 : km2 >= 1 ? 2 : 4;
  return `${km2.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })} km²`;
}
L.GeometryUtil.readableArea = area => formatKm2(area / 1e6);
// Geodesic area of a GeoJSON (Multi)Polygon in lon/lat: outer rings minus holes
function areaKm2(geom) {
  const ring = r => L.GeometryUtil.geodesicArea(r.map(([lon, lat]) => L.latLng(lat, lon)));
  const poly = p => ring(p[0]) - p.slice(1).reduce((a, h) => a + ring(h), 0);
  const polys = geom.type === 'Polygon' ? [geom.coordinates] : geom.type === 'MultiPolygon' ? geom.coordinates : [];
  return polys.reduce((a, p) => a + poly(p), 0) / 1e6;
}

// Leaflet.draw: polygon and rectangle for the AOI, both showing their area
// while drawn; the polyline tool is only the ruler (below), not in the toolbar.
map.addControl(new L.Control.Draw({
  position: 'topleft',
  draw: {
    polygon:   { shapeOptions: AOI_STYLE, showArea: true, metric: true, allowIntersection: false },
    rectangle: { shapeOptions: AOI_STYLE, showArea: true, metric: true },
    circle: false, circlemarker: false, marker: false, polyline: false,
  },
  edit: false,
}));
map.on(L.Draw.Event.CREATED, e => {
  if (e.layerType === 'polyline') { showMeasure(e.layer); return; }
  const geom = e.layer.toGeoJSON().geometry;
  setAoi(geom, false);
  $('aoiText').value = toWkt(geom);
  status(`AOI set from the map (${geom.type}, ${formatKm2(areaKm2(geom))}).`);
});

// ─── RULER ───────────────────────────────────────────────────────
// Leaflet.draw's polyline tool, which shows the length while drawing: click
// to add points, click the last point (or "Finish") to end, Esc to cancel.
// The measured line stays with its length until the next measure, a click
// on it, or Clear.
const RULER_STYLE = { color: '#facc15', weight: 2, dashArray: '6 4' };
const measureLayer = L.featureGroup().addTo(map);
const ruler = new L.Draw.Polyline(map, { shapeOptions: RULER_STYLE, metric: true, feet: false, showLength: true });
function showMeasure(line) {
  const pts = line.getLatLngs();
  const metres = pts.slice(1).reduce((a, p, i) => a + pts[i].distanceTo(p), 0);
  const text = metres >= 1000 ? `${(metres / 1000).toFixed(2)} km` : `${metres.toFixed(0)} m`;
  measureLayer.clearLayers();
  line.addTo(measureLayer)
    .bindTooltip(`${text} — click to remove`, { permanent: true, className: 'fp', direction: 'right' })
    .on('click', () => measureLayer.clearLayers());
  status(`Measured: ${text}.`);
}
L.Control.Ruler = L.Control.extend({
  options: { position: 'topleft' },
  onAdd() {
    const bar = L.DomUtil.create('div', 'leaflet-bar');
    const a = L.DomUtil.create('a', 'ruler-btn', bar);
    a.href = '#';
    a.title = 'Measure a distance';
    a.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16"><path fill="none" stroke="currentColor" stroke-width="1.8" '
      + 'd="M3 16.5 16.5 3 21 7.5 7.5 21zM7 12.5l2 2M9.5 10l1.5 1.5M12 7.5l2 2M14.5 5l1.5 1.5"/></svg>';
    L.DomEvent.on(a, 'click', ev => {
      L.DomEvent.preventDefault(ev);
      L.DomEvent.stopPropagation(ev);
      if (ruler.enabled()) { ruler.disable(); return; }
      measureLayer.clearLayers();
      ruler.enable();
    });
    return bar;
  },
});
map.addControl(new L.Control.Ruler());

// ─── STATUS ──────────────────────────────────────────────────────
function status(msg, kind = '') {
  const el = $('status');
  el.textContent = msg;
  el.className = kind;
  el.title = msg;
}
async function guard(fn) {
  try { await fn(); }
  catch (e) { status(e.message || String(e), 'err'); console.error(e); }
}
async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  const js = await res.json();
  if (!res.ok) throw new Error(js.error || `HTTP ${res.status}`);
  return js;
}

// ─── AOI ─────────────────────────────────────────────────────────
function setAoi(geom, fit = true) {
  aoi = geom;
  aoiLayer.clearLayers();
  aoiLayer.addData(geom);
  $('aoiArea').textContent = `Area: ${formatKm2(areaKm2(geom))}`;
  if (fit) map.fitBounds(aoiLayer.getBounds(), { padding: [30, 30] });
}
function useAoiText() {
  guard(async () => {
    const js = await api('/api/parse_aoi', { text: $('aoiText').value });
    setAoi(js.geometry);
    status(`AOI set from text (${js.type}, ${formatKm2(areaKm2(js.geometry))}).`);
  });
}
// Feature scripts register here what "Clear" must reset on their side
const clearHooks = [];
function clearAll() {
  aoi = null;
  aoiLayer.clearLayers();
  measureLayer.clearLayers();
  $('aoiText').value = '';
  $('aoiArea').textContent = '';
  clearHooks.forEach(fn => fn());
  status('Cleared.');
}
// WKT of a (Multi)Polygon, for the textarea after a draw
function toWkt(g) {
  const ring = r => '(' + r.map(c => `${c[0]} ${c[1]}`).join(', ') + ')';
  if (g.type === 'Polygon') return 'POLYGON (' + g.coordinates.map(ring).join(', ') + ')';
  if (g.type === 'MultiPolygon')
    return 'MULTIPOLYGON (' + g.coordinates.map(p => '(' + p.map(ring).join(', ') + ')').join(', ') + ')';
  return JSON.stringify(g);
}

// ─── TABS ────────────────────────────────────────────────────────
// Every element with data-tab="<name>" (the tab buttons, the panels) belongs
// to one tab; the AOI and the basemap stay above them, shared. Feature
// scripts register in tabHooks to show or hide their map layers.
const tabHooks = [];
let activeTab = null;
function setTab(name) {
  activeTab = name;
  document.querySelectorAll('.tab').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
  document.querySelectorAll('.tab-panel').forEach(el => { el.hidden = el.dataset.tab !== name; });
  tabHooks.forEach(fn => fn(name));
}

// gpt and rclone are found (or not) by the server at start-up
function warnMissingTools(tools) {
  const missing = [];
  if (!tools.gpt) missing.push("SNAP's gpt not found: pre/post runs are disabled (install SNAP or set SNAP_GPT, then restart)");
  if (!tools.rclone) missing.push('rclone not found: downloads are disabled (it ships with the rosarium env)');
  $('toolWarn').hidden = !missing.length;
  $('toolWarn').innerHTML = missing.map(m => `<div>${m}</div>`).join('');
}

// ─── LIVENESS ────────────────────────────────────────────────────
// The server stops on its own once every page is gone (unless --stay): ping
// while open, say goodbye when closing. sendBeacon is the one request a
// browser still delivers while unloading the page.
function startHeartbeat(intervalSeconds) {
  const ping = () => fetch('/api/ping', { method: 'POST' }).catch(() => {});
  ping();
  setInterval(ping, intervalSeconds * 1000);
  window.addEventListener('pagehide', () => navigator.sendBeacon('/api/bye'));
}

// ─── INIT ────────────────────────────────────────────────────────
// Feature scripts register here what they set up from the config
const initHooks = [];
window.addEventListener('DOMContentLoaded', () => guard(async () => {
  config = await api('/api/config');
  map.setView(config.center, config.zoom);
  startHeartbeat(config.ping_interval);
  warnMissingTools(config.tools);
  initHooks.forEach(fn => fn(config));
  setTab('pre_post');
}));
