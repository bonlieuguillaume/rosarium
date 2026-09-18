// rosarium webmap — the shared part: map, basemaps, AOI, status line, API calls.
// Loaded before the feature scripts, which use what is declared here.

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

// Leaflet.draw: polygon and rectangle only. showArea:false sidesteps a known
// Leaflet.draw 1.0.4 crash on rectangles with recent Leaflet versions.
map.addControl(new L.Control.Draw({
  position: 'topleft',
  draw: {
    polygon:   { shapeOptions: AOI_STYLE, showArea: false, allowIntersection: false },
    rectangle: { shapeOptions: AOI_STYLE, showArea: false },
    circle: false, circlemarker: false, marker: false, polyline: false,
  },
  edit: false,
}));
map.on(L.Draw.Event.CREATED, e => {
  const geom = e.layer.toGeoJSON().geometry;
  setAoi(geom, false);
  $('aoiText').value = toWkt(geom);
  status(`AOI set from the map (${geom.type}).`);
});

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
  if (fit) map.fitBounds(aoiLayer.getBounds(), { padding: [30, 30] });
}
function useAoiText() {
  guard(async () => {
    const js = await api('/api/parse_aoi', { text: $('aoiText').value });
    setAoi(js.geometry);
    status(`AOI set from text (${js.type}).`);
  });
}
// Feature scripts register here what "Clear" must reset on their side
const clearHooks = [];
function clearAll() {
  aoi = null;
  aoiLayer.clearLayers();
  $('aoiText').value = '';
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

// ─── INIT ────────────────────────────────────────────────────────
// Feature scripts register here what they set up from the config
const initHooks = [];
window.addEventListener('DOMContentLoaded', () => guard(async () => {
  config = await api('/api/config');
  map.setView(config.center, config.zoom);
  initHooks.forEach(fn => fn(config));
}));
