// Voter Roll OCR — frontend client.
// Handles drag/drop upload, job list rendering, and live progress via WebSocket.

(function () {
  const API = location.origin;
  const dropzone = document.getElementById('dropzone');
  const fileInput = document.getElementById('fileInput');
  const uploadStatus = document.getElementById('uploadStatus');
  const jobList = document.getElementById('jobList');
  const refreshBtn = document.getElementById('refreshBtn');

  const stats = {
    voters: document.getElementById('statVoters'),
    booths: document.getElementById('statBooths'),
    ok: document.getElementById('statOk'),
    warn: document.getElementById('statWarn'),
    mismatch: document.getElementById('statMismatch'),
    cost: document.getElementById('statCost'),
  };

  const VALIDATION_BADGE = {
    OK:              { icon: '✅', label: 'Match',         cls: 'val-ok' },
    WARN:            { icon: '⚠️', label: 'Warning',       cls: 'val-warn' },
    MINOR_MISMATCH:  { icon: '⚠️', label: 'Minor (≤2%)',   cls: 'val-warn' },
    MAJOR_MISMATCH:  { icon: '❌', label: 'Major (≤10%)',  cls: 'val-mismatch' },
    MISMATCH:        { icon: '❌', label: 'Mismatch',      cls: 'val-mismatch' },
    FAILED:          { icon: '❌', label: 'Failed (>10%)', cls: 'val-mismatch' },
    PENDING:         { icon: '…',  label: 'Pending',       cls: 'val-pending' },
    UNKNOWN:         { icon: '?',  label: 'Unknown',       cls: 'val-pending' },
  };

  // Map of job_id → { card element, websocket, latest snapshot }
  const tracked = new Map();

  // ---------- helpers ----------
  function fmt(n) {
    if (n === null || n === undefined) return '—';
    return Number(n).toLocaleString('en-IN');
  }
  function pct(part, whole) {
    if (!whole) return 0;
    return Math.round((part / whole) * 100);
  }
  function escapeHtml(s) {
    return String(s ?? '')
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  // ---------- upload ----------
  dropzone.addEventListener('click', () => fileInput.click());
  ['dragenter', 'dragover'].forEach(ev =>
    dropzone.addEventListener(ev, e => {
      e.preventDefault(); e.stopPropagation();
      dropzone.classList.add('drag-over');
    })
  );
  ['dragleave', 'drop'].forEach(ev =>
    dropzone.addEventListener(ev, e => {
      e.preventDefault(); e.stopPropagation();
      dropzone.classList.remove('drag-over');
    })
  );
  dropzone.addEventListener('drop', e => {
    const files = Array.from(e.dataTransfer.files || []).filter(f =>
      f.type === 'application/pdf' || f.name.toLowerCase().endsWith('.pdf')
    );
    if (files.length) uploadFiles(files);
  });
  fileInput.addEventListener('change', () => {
    const files = Array.from(fileInput.files || []);
    if (files.length) uploadFiles(files);
    fileInput.value = '';
  });

  async function uploadFiles(files) {
    uploadStatus.className = 'upload-status';
    uploadStatus.textContent = `Uploading ${files.length} file${files.length > 1 ? 's' : ''}…`;
    const fd = new FormData();
    files.forEach(f => fd.append('files', f));
    try {
      const resp = await fetch(`${API}/api/upload`, { method: 'POST', body: fd });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || `Upload failed (${resp.status})`);
      }
      const data = await resp.json();
      uploadStatus.className = 'upload-status success';
      uploadStatus.textContent = `Queued ${data.count} job${data.count > 1 ? 's' : ''}.`;
      await refreshJobs();
      data.jobs.forEach(j => connectJob(j.job_id));
    } catch (e) {
      uploadStatus.className = 'upload-status error';
      uploadStatus.textContent = e.message;
    }
  }

  // ---------- jobs ----------
  refreshBtn.addEventListener('click', refreshJobs);

  async function refreshJobs() {
    try {
      const r = await fetch(`${API}/api/jobs`);
      const data = await r.json();
      renderJobs(data.jobs || []);
      // Subscribe to any in-flight jobs
      (data.jobs || []).forEach(j => {
        if (j.status === 'processing' || j.status === 'queued') connectJob(j.job_id);
      });
      await refreshStats();
    } catch (e) {
      console.error(e);
    }
  }

  function renderJobs(jobs) {
    if (!jobs.length) {
      jobList.innerHTML = '<div class="empty">No jobs yet — upload a PDF to get started.</div>';
      return;
    }
    jobList.innerHTML = '';
    jobs.forEach(j => {
      const card = document.createElement('div');
      card.className = 'job-card';
      card.dataset.jobId = j.job_id;
      jobList.appendChild(card);
      paintJobCard(card, j);
      tracked.set(j.job_id, { ...(tracked.get(j.job_id) || {}), card, snapshot: j });
    });
  }

  function paintJobCard(card, j) {
    const status = j.status || 'queued';
    const total = j.total_voters || 0;
    const expected = j.expected_voter_count || 0;
    const valStatus = j.validation_status || 'PENDING';
    const valBadge = VALIDATION_BADGE[valStatus] || VALIDATION_BADGE.PENDING;
    const diff = total - expected;
    const diffLabel = expected ? (diff === 0 ? 'exact match' : `${diff > 0 ? '+' : ''}${diff}`) : '—';

    const showProgress = status === 'queued' || status === 'processing';
    const showValidation = status === 'completed';
    const showResult = status === 'completed' && j.has_output;

    card.innerHTML = `
      <div class="job-head">
        <div>
          <div class="job-name">${escapeHtml(j.pdf_name)}</div>
          <div class="job-meta">
            ${j.processed_pages || 0} / ${j.total_pages || 0} pages
            · ${fmt(total)} voters
            · ₹${(j.cost_estimate || 0).toFixed(2)}
          </div>
        </div>
        <span class="status-badge status-${status}">${status}</span>
      </div>

      ${showProgress || status !== 'queued' ? `
        <div>
          <div class="progress ${status}">
            <div class="progress-bar" style="width: ${(j.progress_pct || 0)}%"></div>
          </div>
          <div class="job-meta" style="margin-top:4px;">${(j.progress_pct || 0).toFixed(1)}% complete</div>
        </div>` : ''}

      ${showValidation ? `
        <div class="validation-row ${valBadge.cls}">
          <div class="validation-counts">
            <span><strong>Expected:</strong> ${fmt(expected)}</span>
            <span><strong>Extracted:</strong> ${fmt(total)}</span>
            <span class="validation-diff">${diffLabel}</span>
          </div>
          <div class="validation-badge ${valBadge.cls}">${valBadge.icon} ${valBadge.label}</div>
        </div>` : ''}

      ${j.error ? `<div class="job-error">⚠ ${escapeHtml(j.error)}</div>` : ''}

      <div class="job-actions">
        ${showResult ? `
          <a class="btn-primary" href="${API}/api/jobs/${j.job_id}/result?format=json">Download JSON</a>
          <a class="btn-secondary" href="${API}/api/jobs/${j.job_id}/result?format=xlsx">Download Excel</a>
          <a class="btn-secondary" href="${API}/api/jobs/${j.job_id}/result?format=summary">Summary</a>
        ` : ''}
        ${status === 'processing' || status === 'queued' ?
          `<button class="btn-secondary" data-act="cancel" data-id="${j.job_id}">Cancel</button>` : ''}
        <button class="btn-danger" data-act="delete" data-id="${j.job_id}">Delete</button>
      </div>
    `;

    card.querySelectorAll('button[data-act]').forEach(btn => {
      btn.addEventListener('click', () => onJobAction(btn.dataset.act, btn.dataset.id));
    });
  }

  async function onJobAction(action, jobId) {
    try {
      if (action === 'cancel') {
        await fetch(`${API}/api/jobs/${jobId}/cancel`, { method: 'POST' });
      } else if (action === 'delete') {
        if (!confirm('Delete this job and its outputs?')) return;
        await fetch(`${API}/api/jobs/${jobId}`, { method: 'DELETE' });
        const t = tracked.get(jobId);
        if (t && t.ws) try { t.ws.close(); } catch (_) {}
        tracked.delete(jobId);
      }
      await refreshJobs();
    } catch (e) {
      console.error(e);
    }
  }

  // ---------- WebSocket per-job ----------
  function connectJob(jobId) {
    const t = tracked.get(jobId);
    if (t && t.ws && (t.ws.readyState === 0 || t.ws.readyState === 1)) return;
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = new WebSocket(`${proto}://${location.host}/ws/jobs/${jobId}`);
    tracked.set(jobId, { ...(t || {}), ws });

    ws.addEventListener('message', e => {
      try {
        const payload = JSON.parse(e.data);
        if (payload.heartbeat) return;
        if (payload.error) return;
        const cur = tracked.get(jobId) || {};
        cur.snapshot = { ...(cur.snapshot || {}), ...payload };
        tracked.set(jobId, cur);
        const card = cur.card || document.querySelector(`.job-card[data-job-id="${jobId}"]`);
        if (card) {
          paintJobCard(card, cur.snapshot);
        } else {
          // Card not yet on screen → refresh full list
          refreshJobs();
        }
        if (['completed', 'failed', 'cancelled'].includes(payload.status)) {
          refreshStats();
        }
      } catch (err) {
        console.error('ws parse', err);
      }
    });

    ws.addEventListener('close', () => {
      const cur = tracked.get(jobId);
      if (!cur) return;
      cur.ws = null;
      tracked.set(jobId, cur);
      const status = cur.snapshot && cur.snapshot.status;
      // Reconnect for in-flight jobs
      if (status === 'processing' || status === 'queued') {
        setTimeout(() => connectJob(jobId), 2000);
      }
    });

    ws.addEventListener('error', () => {
      try { ws.close(); } catch (_) {}
    });
  }

  // ---------- aggregate stats ----------
  async function refreshStats() {
    try {
      const r = await fetch(`${API}/api/stats`);
      const d = await r.json();
      stats.voters.textContent   = fmt(d.total_voters || 0);
      stats.booths.textContent   = fmt(d.completed_jobs || 0);
      stats.ok.textContent       = fmt(d.validation_ok || 0);
      stats.warn.textContent     = fmt(d.validation_warn || 0);
      stats.mismatch.textContent = fmt(d.validation_mismatch || 0);
      stats.cost.textContent     = `₹${(d.total_cost || 0).toFixed(2)}`;
    } catch (e) {
      console.error('stats', e);
    }
  }

  // ---------- bootstrap ----------
  refreshJobs();
  refreshStats();
  setInterval(refreshStats, 5000);
})();
