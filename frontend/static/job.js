// rosarium webmap — the job panel: follows the background job the server runs
// (a download, or a whole pre/post run), whatever the tab. Polls
// /api/job/status every second while it runs; a reloaded page picks it up.
// Uses map, api(), guard() and status() of map.js.

const BURST_STYLE = { color: '#94a3b8', weight: 1, dashArray: '3 3', fillOpacity: 0 };
const BURST_USED  = { color: '#facc15', weight: 1.5, fillOpacity: 0.12 };

let jobPolling = false;
let jobBurstsShown = false;   // bursts of the current job already on the map
const jobBurstLayer = L.geoJSON(EMPTY, {
  style: f => (f.properties.used ? BURST_USED : BURST_STYLE),
  onEachFeature: (f, layer) => layer.bindTooltip(
    `${f.properties.swath} · burst ${f.properties.burst}${f.properties.used ? ' · processed' : ''}`,
    { className: 'fp', sticky: true }),
}).addTo(map);

// Called by the tabs with the answer of the route that started a job
function followJob(snapshot) {
  jobBurstsShown = false;
  jobBurstLayer.clearLayers();
  renderJob(snapshot);
  if (!jobPolling) pollJob();
}

async function pollJob() {
  jobPolling = true;
  try {
    const js = await api('/api/job/status');
    renderJob(js);
    if (js.status === 'running') { setTimeout(pollJob, 1000); return; }
  } catch (e) {
    // The server is gone (stopped from the console): nothing left to follow
    status('Lost the server: ' + (e.message || e), 'err');
  }
  jobPolling = false;
}

function cancelJob() {
  if (!confirm('Cancel the running job? gpt / rclone are stopped; files already written stay.')) return;
  guard(async () => { await api('/api/job/cancel', {}); status('Cancelling…', 'busy'); });
}

function closeJob() {
  guard(async () => {
    await api('/api/job/clear', {});
    $('jobPanel').hidden = true;
    jobBurstLayer.clearLayers();
    jobBurstsShown = false;
  });
}

// ─── RENDERING ───────────────────────────────────────────────────
function hms(seconds) {
  if (seconds == null) return '';
  const s = Math.floor(seconds), h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60;
  return (h ? `${h}:${String(m).padStart(2, '0')}` : `${m}`) + ':' + String(s % 60).padStart(2, '0');
}
function gb(bytes) { return bytes == null ? '?' : (bytes / 1e9).toFixed(2) + ' GB'; }
function esc(s) { return String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

const STEP_ICON = { done: '✓', running: '▶', pending: '○', error: '✗', cancelled: '✗' };
const STATE_TEXT = { running: 'running', done: 'finished', error: 'failed', cancelled: 'cancelled' };

function renderJob(job) {
  const panel = $('jobPanel');
  document.body.classList.toggle('job-running', job.status === 'running');
  if (job.status === 'idle') { panel.hidden = true; return; }
  panel.hidden = false;

  const parts = [`
    <div class="job-head"><span class="job-title">${esc(job.title)}</span><span class="job-time">${hms(job.elapsed)}</span></div>
    <div class="job-state ${job.status}">${STATE_TEXT[job.status] || job.status}</div>`];

  // Steps: done ones with their duration, the running one with its percentage
  parts.push('<div class="job-block"><div class="label">Steps</div>');
  for (const s of job.steps) {
    const pct = s.status === 'running' && s.percent != null ? `${s.percent}%` : '';
    parts.push(`<div class="jstep ${s.status}"><span class="ico">${STEP_ICON[s.status] || ''}</span>
      <span class="name">${esc(s.name)}</span><span class="pct">${pct}</span><span class="dur">${hms(s.duration)}</span></div>`);
    if (s.status === 'running' && s.percent != null)
      parts.push(`<div class="bar"><div style="width:${s.percent}%"></div></div>`);
  }
  parts.push('</div>');

  // rclone statistics, while downloading and once done
  const dl = job.download;
  if (dl) {
    const eta = dl.eta == null ? '–' : hms(dl.eta);
    parts.push(`<div class="job-block"><div class="label">Download</div><div class="job-dl">
      <div><span>received</span> ${gb(dl.bytes)} / ${gb(dl.total_bytes)}</div>
      <div><span>speed</span> ${(dl.speed / 1e6).toFixed(1)} MB/s</div>
      <div><span>files</span> ${dl.transfers} / ${dl.total_transfers}</div>
      <div><span>eta</span> ${eta}</div>
      ${dl.errors ? `<div style="color:var(--err)"><span>errors</span> ${dl.errors}</div>` : ''}
    </div></div>`);
  }

  // Sub-swaths and bursts kept for the SNAP graphs, also drawn on the map
  const bursts = job.info && job.info.bursts;
  if (bursts) {
    const lines = bursts.swaths.map(s => s.first_burst === s.last_burst
      ? `${s.subswath}: burst ${s.first_burst}` : `${s.subswath}: bursts ${s.first_burst} – ${s.last_burst}`);
    parts.push(`<div class="job-block job-bursts"><div class="label">Sub-swaths &amp; bursts (yellow on the map)</div>
      ${lines.map(l => `<div>${esc(l)}</div>`).join('')}</div>`);
    if (!jobBurstsShown) { jobBurstLayer.clearLayers(); jobBurstLayer.addData(bursts.footprints); jobBurstsShown = true; }
  }

  if (job.status === 'done' && job.result) {
    const rows = Object.entries(job.result).map(([k, v]) => `<div><span style="color:var(--text-dim)">${esc(k)}</span> ${esc(v)}</div>`);
    parts.push(`<div class="job-block job-result">${rows.join('')}</div>`);
  }
  if (job.error) parts.push(`<div class="job-block job-result err">${esc(job.error)}</div>`);
  if (job.log_path) parts.push(`<div class="job-block job-result"><span style="color:var(--text-dim)">log</span> ${esc(job.log_path)}</div>`);

  // The log: kept open across refreshes if the user opened it
  const logOpen = panel.querySelector('details.job-log')?.open ? ' open' : '';
  parts.push(`<div class="job-block"><details class="job-log"${logOpen}><summary>Log (last lines)</summary>
    <pre>${esc(job.tail.join('\n'))}</pre></details></div>`);

  parts.push(job.status === 'running'
    ? '<div class="btn-row"><button class="btn" onclick="cancelJob()">Cancel</button></div>'
    : '<div class="btn-row"><button class="btn" onclick="closeJob()">Close</button></div>');

  const pre = panel.querySelector('.job-log pre');
  const atBottom = !pre || pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 4;
  panel.innerHTML = parts.join('');
  const newPre = panel.querySelector('.job-log pre');
  if (newPre && atBottom) newPre.scrollTop = newPre.scrollHeight;

  if (job.status !== 'running' && jobPolling) {
    const msg = { done: 'Job finished', error: 'Job failed: ' + job.error, cancelled: 'Job cancelled' }[job.status];
    status(msg, job.status === 'done' ? '' : 'err');
  }
}

// A job may already be running (page reloaded): pick it up
initHooks.push(() => guard(async () => {
  const js = await api('/api/job/status');
  renderJob(js);
  if (js.status === 'running' && !jobPolling) pollJob();
}));
