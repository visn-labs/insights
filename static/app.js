const state = {
  capabilities: null,
  models: [],
  jobs: [],
  sample: null,
  selectedJobId: null,
  upload: null,
  pollTimer: null,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

document.addEventListener('DOMContentLoaded', boot);

async function boot() {
  bindNavigation();
  bindForm();
  bindUploads();
  $('#refresh-button').addEventListener('click', refreshAll);
  $('#new-run-button').addEventListener('click', () => showView('workspace'));
  await Promise.all([loadCapabilities(), loadModels(), loadSample(), loadJobs()]);
  state.pollTimer = window.setInterval(loadJobs, 1500);
}

function bindNavigation() {
  $$('.nav-item').forEach((button) => {
    button.addEventListener('click', () => showView(button.dataset.view));
  });
}

function showView(name) {
  $$('.nav-item').forEach((item) => item.classList.toggle('active', item.dataset.view === name));
  $$('.view').forEach((view) => view.classList.toggle('active', view.id === `${name}-view`));
  const titles = { workspace: 'Pipeline workspace', runs: 'Run history', system: 'System readiness' };
  $('#page-title').textContent = titles[name] || 'Pipeline workspace';
}

function bindForm() {
  $$('input[name="source"]').forEach((radio) => {
    radio.addEventListener('change', () => {
      $$('.source-card').forEach((card) => card.classList.remove('active'));
      $(`#${radio.value}-source`).classList.add('active');
      if (radio.value === 'sample') $('#backend').value = 'simulator';
    });
  });

  $('#format-json').addEventListener('click', () => {
    try {
      formatEditor($('#observations-json'));
      formatEditor($('#policy-json'));
      hideError();
    } catch (error) {
      showError(error.message);
    }
  });
  $('#reset-sample').addEventListener('click', fillSampleEditors);
  $('#job-form').addEventListener('submit', submitJob);
}

function bindUploads() {
  const zone = $('#drop-zone');
  const input = $('#video-file');
  input.addEventListener('change', () => input.files[0] && uploadVideo(input.files[0]));
  ['dragenter', 'dragover'].forEach((eventName) => zone.addEventListener(eventName, (event) => {
    event.preventDefault();
    zone.classList.add('dragging');
  }));
  ['dragleave', 'drop'].forEach((eventName) => zone.addEventListener(eventName, (event) => {
    event.preventDefault();
    zone.classList.remove('dragging');
  }));
  zone.addEventListener('drop', (event) => {
    const file = event.dataTransfer.files[0];
    if (file) uploadVideo(file);
  });
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const type = response.headers.get('content-type') || '';
  const body = type.includes('application/json') ? await response.json() : await response.text();
  if (!response.ok) throw new Error(body.error || body || `${response.status} ${response.statusText}`);
  return body;
}

async function refreshAll() {
  $('#refresh-button').disabled = true;
  await Promise.all([loadCapabilities(), loadModels(), loadJobs()]);
  $('#refresh-button').disabled = false;
  toast('Runtime state refreshed');
}

async function loadCapabilities() {
  try {
    state.capabilities = await api('/api/v1/capabilities');
    $('#service-dot').className = 'status-dot ok';
    $('#service-status').textContent = 'Service ready';
    $('#service-version').textContent = `v${state.capabilities.service_version} · ${state.capabilities.kafka_enabled ? 'Kafka' : 'No-op sink'}`;
    renderCapabilities();
  } catch (error) {
    $('#service-dot').className = 'status-dot error';
    $('#service-status').textContent = 'Service unavailable';
  }
}

async function loadModels() {
  try {
    const response = await api('/api/v1/models');
    state.models = response.models || [];
    if (response.available && state.models.length) {
      $('#gemma-dot').className = 'status-dot ok';
      $('#gemma-status').textContent = 'Gemma reachable';
    } else {
      $('#gemma-dot').className = 'status-dot error';
      $('#gemma-status').textContent = 'Gemma fallback mode';
    }
    renderModels(response.error);
  } catch (error) {
    state.models = [];
    $('#gemma-dot').className = 'status-dot error';
    $('#gemma-status').textContent = 'Gemma fallback mode';
    renderModels(error.message);
  }
}

async function loadSample() {
  try {
    state.sample = await api('/api/v1/sample');
    fillSampleEditors();
  } catch (error) {
    showError(`Could not load sample: ${error.message}`);
  }
}

function fillSampleEditors() {
  if (!state.sample) return;
  $('#observations-json').value = JSON.stringify(state.sample.observations, null, 2);
  $('#policy-json').value = JSON.stringify(state.sample.policy, null, 2);
  hideError();
}

function formatEditor(editor) {
  editor.value = JSON.stringify(JSON.parse(editor.value || '{}'), null, 2);
}

async function uploadVideo(file) {
  state.upload = null;
  $('#upload-progress').classList.remove('hidden');
  $('#upload-help').textContent = `Uploading ${file.name} · ${formatBytes(file.size)}`;
  const form = new FormData();
  form.append('video', file);
  try {
    state.upload = await api('/api/v1/uploads', { method: 'POST', body: form });
    $('#upload-help').textContent = `${state.upload.original_name} · ${formatBytes(state.upload.size_bytes)} · ready`;
    toast('Video is ready for processing');
  } catch (error) {
    $('#upload-help').textContent = `Upload failed: ${error.message}`;
    showError(error.message);
  } finally {
    $('#upload-progress').classList.add('hidden');
  }
}

async function submitJob(event) {
  event.preventDefault();
  hideError();
  const button = $('#run-button');
  button.disabled = true;
  button.querySelector('span').textContent = 'Submitting…';
  try {
    const sourceKind = $('input[name="source"]:checked').value;
    let source;
    if (sourceKind === 'sample') {
      source = 'sample';
    } else if (sourceKind === 'upload') {
      if (!state.upload) throw new Error('Choose and finish uploading a video first.');
      source = { upload: { upload_id: state.upload.id } };
    } else {
      const uri = $('#rtsp-uri').value.trim();
      if (!uri) throw new Error('Enter an RTSP address.');
      source = { rtsp: { uri } };
    }

    const backend = $('#backend').value;
    const observationsText = $('#observations-json').value.trim();
    const policyText = $('#policy-json').value.trim();
    const observations = observationsText ? JSON.parse(observationsText) : [];
    const policy = policyText ? JSON.parse(policyText) : {};
    const request = {
      name: $('#job-name').value.trim(),
      source,
      backend,
      detector_fps: Number($('#detector-fps').value),
      gemma_enabled: $('#gemma-enabled').checked,
      observations: backend === 'simulator' ? observations : [],
      policy,
    };
    const job = await api('/api/v1/jobs', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(request),
    });
    state.selectedJobId = job.id;
    inspectJob(job);
    toast('Pipeline run submitted');
    await loadJobs();
  } catch (error) {
    showError(error.message);
  } finally {
    button.disabled = false;
    button.querySelector('span').textContent = 'Run pipeline';
  }
}

async function loadJobs() {
  try {
    const jobs = await api('/api/v1/jobs');
    state.jobs = jobs;
    $('#run-count').textContent = jobs.length;
    renderRuns();
    if (state.selectedJobId) {
      const selected = jobs.find((job) => job.id === state.selectedJobId);
      if (selected) inspectJob(selected);
    }
  } catch (error) {
    console.warn('Could not refresh jobs', error);
  }
}

function inspectJob(job) {
  state.selectedJobId = job.id;
  $('#empty-inspector').classList.add('hidden');
  $('#job-inspector').classList.remove('hidden');
  $('#job-title').textContent = job.request.name;
  $('#job-kicker').textContent = `RUN / ${shortId(job.id)}`;
  const status = $('#job-status');
  status.textContent = job.status;
  status.className = `badge ${job.status}`;
  $('#processing-state').classList.toggle('hidden', !['queued', 'running'].includes(job.status));
  $('#job-error').classList.toggle('hidden', job.status !== 'failed');
  $('#job-error').textContent = job.error || '';

  const uploadId = job.request.source?.upload?.upload_id;
  const video = $('#video-preview');
  if (uploadId) {
    video.src = `/api/v1/uploads/${uploadId}/content`;
    video.classList.remove('hidden');
  } else {
    video.removeAttribute('src');
    video.classList.add('hidden');
  }

  if (!job.result) {
    $('#result-content').classList.add('hidden');
    return;
  }
  renderResult(job.result);
}

function renderResult(result) {
  $('#result-content').classList.remove('hidden');
  $('#metric-observations').textContent = result.observations_processed;
  $('#metric-tracks').textContent = result.tracks.length;
  $('#metric-events').textContent = result.events.length;
  $('#metric-duration').textContent = formatDuration(result.duration_ms);
  $('#report-headline').textContent = result.report.headline;
  $('#report-summary').textContent = result.report.summary;
  $('#report-model').textContent = result.gemma.used ? result.gemma.model : 'Deterministic fallback';
  const notes = [...(result.report.data_quality_notes || [])];
  if (result.gemma.fallback_reason) notes.push(`Gemma fallback: ${result.gemma.fallback_reason}`);
  $('#report-notes').innerHTML = notes.map((note) => `<span>${escapeHtml(note)}</span>`).join('');

  $('#event-total').textContent = `${result.events.length} event${result.events.length === 1 ? '' : 's'}`;
  $('#event-list').innerHTML = result.events.length ? result.events.map((event) => `
    <article class="event-row">
      <span class="event-time">${formatDuration(event.event_time_ms)}</span>
      <span class="event-marker"></span>
      <div class="event-body"><strong>${escapeHtml(label(event.event_type))}</strong><small>${escapeHtml(event.description)}</small></div>
      <span class="event-confidence">${Math.round(event.confidence * 100)}%</span>
    </article>`).join('') : '<div class="empty-row">No policy events were emitted.</div>';

  $('#track-list').innerHTML = result.tracks.length ? result.tracks.map((track) => `
    <tr><td>${escapeHtml(track.track_id)}</td><td>${escapeHtml(track.class_name)}</td><td>${formatDuration(track.duration_ms)}</td><td>${Math.round(track.maximum_confidence * 100)}%</td><td>${escapeHtml(track.zones_visited.join(', ') || '—')}</td></tr>`).join('') : '<tr><td colspan="5">No confirmed tracks</td></tr>';
  $('#raw-json').textContent = JSON.stringify(result, null, 2);
}

function renderRuns() {
  const container = $('#runs-list');
  if (!state.jobs.length) {
    container.innerHTML = '<div class="empty-row">No runs yet. Start with the built-in sample.</div>';
    return;
  }
  container.innerHTML = state.jobs.map((job) => `
    <article class="run-row" data-id="${job.id}">
      <div class="run-name"><strong>${escapeHtml(job.request.name)}</strong><small>${shortId(job.id)} · ${formatDate(job.created_at_ms)}</small></div>
      <div class="run-cell"><strong>${label(job.request.backend)}</strong><small>backend</small></div>
      <div class="run-cell"><strong>${job.result?.tracks?.length ?? '—'}</strong><small>tracks</small></div>
      <div class="run-cell"><strong>${job.result?.events?.length ?? '—'}</strong><small>events</small></div>
      <span class="badge ${job.status}">${job.status}</span>
    </article>`).join('');
  $$('.run-row').forEach((row) => row.addEventListener('click', () => {
    const job = state.jobs.find((candidate) => candidate.id === row.dataset.id);
    if (job) {
      inspectJob(job);
      showView('workspace');
    }
  }));
}

function renderCapabilities() {
  if (!state.capabilities) return;
  const labels = {
    service_version: 'Service version', local_state: 'State mode', simulator: 'Simulator',
    yolo26_command: 'YOLO26 adapter', gemma_endpoint: 'Gemma endpoint',
    kafka_compiled: 'Kafka compiled', kafka_enabled: 'Kafka enabled',
  };
  $('#capability-list').innerHTML = Object.entries(labels).map(([key, title]) => `<dt>${title}</dt><dd>${escapeHtml(String(state.capabilities[key]))}</dd>`).join('');
}

function renderModels(error) {
  if (!state.models.length) {
    $('#models-list').innerHTML = `<div class="empty-row">${escapeHtml(error || 'No loaded models were returned.')}</div>`;
    return;
  }
  $('#models-list').innerHTML = state.models.map((model) => {
    const preferred = /gemma-4.*26b.*a4b/i.test(model.id);
    return `<div class="model-row ${preferred ? 'preferred' : ''}">${escapeHtml(model.id)}${preferred ? ' · preferred' : ''}</div>`;
  }).join('');
}

function showError(message) {
  $('#form-error').textContent = message;
  $('#form-error').classList.remove('hidden');
}

function hideError() {
  $('#form-error').classList.add('hidden');
}

function toast(message) {
  const element = $('#toast');
  element.textContent = message;
  element.classList.add('show');
  window.setTimeout(() => element.classList.remove('show'), 2400);
}

function formatDuration(milliseconds) {
  if (milliseconds < 1000) return `${milliseconds}ms`;
  return `${(milliseconds / 1000).toFixed(milliseconds % 1000 ? 1 : 0)}s`;
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
}

function formatDate(milliseconds) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(milliseconds));
}

function label(value) {
  return String(value).replaceAll('_', ' ').replace(/\b\w/g, (character) => character.toUpperCase());
}

function shortId(value) {
  return value ? value.slice(0, 8).toUpperCase() : '—';
}

function escapeHtml(value) {
  const node = document.createElement('span');
  node.textContent = String(value);
  return node.innerHTML;
}

