const KNOWN_VLM_MODELS = [
  'prism-ml/bonsai-27b',
  'moondream2',
  'qwen/qwen3.6-35b-a3b',
  'google/gemma-4-26b-a4b-qat',
  'zai-org/glm-4.6v-flash',
];

const MEMORY_CAMERA_PRESETS = {
  'cluster-a': {
    clusterId: 'authorized-cluster-a',
    cameras: [
      { camera_id: 'cluster-a-cam-1', liveurl: 'http://47.181.86.62:8081/mjpg/video.mjpg', Country: '', 'Country code': '', Region: '', City: '', Latitude: null, Longitude: null, ZIP: null, Timezone: '', Manufacturer: '', description: 'Authorized MJPEG camera; scene metadata will be supplied by the backend.' },
      { camera_id: 'cluster-a-cam-2', liveurl: 'http://47.181.86.62:8082/mjpg/video.mjpg', Country: '', 'Country code': '', Region: '', City: '', Latitude: null, Longitude: null, ZIP: null, Timezone: '', Manufacturer: '', description: 'Authorized MJPEG camera in the same cluster; scene metadata will be supplied by the backend.' },
    ],
  },
  'cluster-b': {
    clusterId: 'authorized-cluster-b',
    cameras: [
      { camera_id: 'cluster-b-cam-1', liveurl: 'http://193.214.235.26:8001/mjpg/video.mjpg', Country: '', 'Country code': '', Region: '', City: '', Latitude: null, Longitude: null, ZIP: null, Timezone: '', Manufacturer: '', description: 'Authorized MJPEG camera; scene metadata will be supplied by the backend.' },
      { camera_id: 'cluster-b-cam-3', liveurl: 'http://193.214.235.26:8003/mjpg/video.mjpg', Country: '', 'Country code': '', Region: '', City: '', Latitude: null, Longitude: null, ZIP: null, Timezone: '', Manufacturer: '', description: 'Authorized MJPEG camera in the same cluster; scene metadata will be supplied by the backend.' },
      { camera_id: 'cluster-b-cam-4', liveurl: 'http://193.214.235.26:8004/mjpg/video.mjpg', Country: '', 'Country code': '', Region: '', City: '', Latitude: null, Longitude: null, ZIP: null, Timezone: '', Manufacturer: '', description: 'Authorized MJPEG camera in the same cluster; scene metadata will be supplied by the backend.' },
    ],
  },
  movement: {
    clusterId: 'authorized-high-movement',
    cameras: [
      { camera_id: 'movement-cam-1', liveurl: 'http://103.151.177.124:89/mjpg/video.mjpg', Country: '', 'Country code': '', Region: '', City: '', Latitude: null, Longitude: null, ZIP: null, Timezone: '', Manufacturer: '', description: 'Authorized high-movement MJPEG camera.' },
      { camera_id: 'movement-cam-2', liveurl: 'http://24.30.252.59/mjpg/video.mjpg', Country: '', 'Country code': '', Region: '', City: '', Latitude: null, Longitude: null, ZIP: null, Timezone: '', Manufacturer: '', description: 'Authorized high-movement MJPEG camera.' },
    ],
  },
};

const state = {
  capabilities: null,
  retrievalConfigured: null,
  visualIndexRetryJob: null,
  visualIndexRetryAt: 0,
  visualIndexFailures: 0,
  models: [],
  configuredVlms: [...KNOWN_VLM_MODELS],
  jobs: [],
  clusterJobs: [],
  memoryJobs: [],
  knownRevokedMemoryIds: new Set(),
  memoryAccessRevision: 0,
  evidenceStatistics: null,
  evidenceStatisticsBusy: false,
  sample: null,
  selectedJobId: null,
  selectedJobKind: null,
  selectedMemoryJobId: null,
  renderedMemoryJob: null,
  memoryJobOptions: null,
  searchAllRecordings: false,
  memoryQueryBusy: false,
  memoryRecordingActionBusy: false,
  pendingMemorySubmission: null,
  visualIndex: null,
  visualIndexBusy: false,
  visualIndexPolling: false,
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
  bindMemory();
  showView('memory');
  renderVlmOptions();
  $('#refresh-button').addEventListener('click', refreshAll);
  $('#new-run-button').addEventListener('click', () => showView('workspace'));
  await Promise.all([loadCapabilities(), loadRetrievalStatus(), loadModels(), loadSample(), refreshJobLists()]);
  state.pollTimer = window.setInterval(refreshJobLists, 1500);
}

function bindNavigation() {
  $$('.nav-item').forEach((button) => {
    button.addEventListener('click', () => showView(button.dataset.view));
  });
}

function showView(name) {
  $$('.nav-item').forEach((item) => item.classList.toggle('active', item.dataset.view === name));
  $$('.view').forEach((view) => view.classList.toggle('active', view.id === `${name}-view`));
  const titles = { workspace: 'Detector analytics', memory: 'Video memory · Experiment 1', runs: 'Run history', system: 'System readiness' };
  $('#page-title').textContent = titles[name] || 'Pipeline workspace';
}

function bindMemory() {
  $('#memory-recording-cancel').addEventListener('click', () => changeMemoryRecording('cancel'));
  $('#memory-recording-resume').addEventListener('click', () => changeMemoryRecording('resume'));
  $('#memory-metadata-show').addEventListener('click', () => loadEvidenceStatistics(false));
  $('#memory-metadata-rebuild').addEventListener('click', () => loadEvidenceStatistics(true));
  $('#memory-recording-revoke').addEventListener('click', () => {
    const job = state.memoryJobs.find(job => job.id === state.selectedMemoryJobId);
    if (job?.revocation || confirm('Revoke access to this recording? It will leave search and playback, and its derived caches will be cleared. Retained media follows the existing retention policy. Access cannot be restored.')) changeMemoryRecording('revoke');
  });
  $('#visual-index-start').addEventListener('click', () => changeVisualIndex());
  $('#visual-index-cancel').addEventListener('click', () => changeVisualIndex('cancel'));
  $('#visual-index-retry').addEventListener('click', () => changeVisualIndex('retry'));
  $$('.memory-preset').forEach((button) => button.addEventListener('click', () => loadMemoryPreset(button.dataset.preset)));
  $('#format-memory-json').addEventListener('click', () => {
    try {
      const value = JSON.parse($('#memory-cameras-json').value || '[]');
      $('#memory-cameras-json').value = JSON.stringify(value, null, 2);
      setInlineError('#memory-form-error', '');
    } catch (error) {
      setInlineError('#memory-form-error', error.message);
    }
  });
  $('#memory-job-form').addEventListener('submit', submitMemoryJob);
  $('#memory-build-links').addEventListener('click', async () => {
    const id = state.selectedMemoryJobId;
    state.buildingEntityMemory = true;
    $('#memory-build-links').disabled = true;
    $('#memory-job-select').disabled = true;
    $('#memory-links-status').textContent = 'Tracking retained footage and linking evidence…';
    try {
      const job = await api(`/api/v1/memory-jobs/${id}/long-memory`, {method:'POST'});
      state.renderedMemoryJob = null;
      await loadMemoryJobs();
      if (state.selectedMemoryJobId === id) inspectMemoryJob(job);
      $('#memory-links-status').textContent = `${job.result.camera_results.reduce((n,c)=>n+(c.long_memory?.entities.length || 0),0)} tracker histories saved. Identity remains uncertain.`;
      toast('Entity memory saved');
    } catch(error) { $('#memory-links-status').textContent = `Entity memory failed: ${error.message}`; }
    finally {state.buildingEntityMemory = false;$('#memory-job-select').disabled = false;updateMemorySearchScope();}
  });
  const syncMemoryModes = () => { $('#memory-query-vlm').disabled = $('#memory-query-long').checked || $('#memory-query-temporal').checked; };
  $('#memory-query-long').addEventListener('change', () => {syncMemoryModes();});
  $('#memory-query-temporal').addEventListener('change', () => {syncMemoryModes();});
  $('#memory-query-entity').addEventListener('change', () => { if ($('#memory-query-entity').value) {$('#memory-query-long').checked = true;syncMemoryModes();} });

  $('#memory-job-select').addEventListener('change', event => {
    const job = state.memoryJobs.find(job => job.id === event.target.value);
    if (job) { inspectMemoryJob(job); loadVisualIndex(); }
  });
  $('#memory-query-form').addEventListener('submit', submitMemoryQuery);
  $('#memory-query-form').addEventListener('input', clearMemoryAnswer);
  $('#memory-query-session').addEventListener('change', () => {
    $('#memory-query-entity').value = '';
    renderMemoryEntityOptions(state.memoryJobs.find(job => job.id === state.selectedMemoryJobId));
    clearMemoryAnswer();
  });
  for (const [sequence, caption] of [['#memory-temporal-enabled', '#memory-vlm-enabled']]) {
    $(sequence).addEventListener('change', () => { $(caption).disabled = $(sequence).checked; });
  }
  $('#memory-search-all').addEventListener('click', () => {
    state.searchAllRecordings = true;
    $('#memory-query-entity').value = '';
    $('#memory-query-cluster').value = '';
    $('#memory-query-cameras').value = '';
    $('#memory-query-session').value = '';
    renderMemoryEntityOptions(state.memoryJobs.find(job => job.id === state.selectedMemoryJobId));
    updateMemorySearchScope();
  });
  $('#memory-vlm-model').addEventListener('change', () => renderModels());
  $('#open-memory').addEventListener('click', () => {
    const source = $('input[name="source"]:checked').value;
    const stream = $(source === 'rtsp' ? '#rtsp-uri' : '#http-uri').value.trim();
    if (stream) $('#memory-stream-url').value = stream;
    showView('memory');
    $('#memory-stream-url').focus();
  });
  $('#search-indexed-memory').addEventListener('click', () => {
    const job = state.memoryJobs.find(job => job.id === state.selectedMemoryJobId);
    if (job) {
      state.searchAllRecordings = false;
      updateMemorySearchScope();
      $('#memory-query-cluster').value = job.request.cluster_id || '';
      $('#memory-query-cameras').value = job.request.cameras.map(camera => camera.camera_id).join(', ');
    }
    $('#memory-query-text').focus();
    $('#memory-query-form').scrollIntoView({behavior: 'smooth', block: 'center'});
  });
  $('#memory-query-semantic').addEventListener('change', () => {
    $('#memory-query-rerank').disabled = !$('#memory-query-semantic').checked;
  });
}

function loadMemoryPreset(name) {
  const preset = MEMORY_CAMERA_PRESETS[name];
  if (!preset) return;
  $('#memory-stream-url').value = '';
  $('#memory-cluster-id').value = preset.clusterId;
  $('#memory-cameras-json').value = JSON.stringify(preset.cameras, null, 2);
  setInlineError('#memory-form-error', '');
}

function bindForm() {
  $$('input[name="source"]').forEach((radio) => {
    radio.addEventListener('change', () => {
      $$('.source-card').forEach((card) => card.classList.remove('active'));
      $(`#${radio.value}-source`).classList.add('active');
      $('#backend').value = radio.value === 'sample' ? 'simulator' : 'yolo26_command';
    });
  });
  $('#cluster-mode').addEventListener('change', (event) => {
    $('#cluster-topology-details').open = event.target.value === 'topology';
  });
  $('#vlm-model').addEventListener('change', () => renderModels());

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
  state.visualIndexRetryAt = 0;
  $('#refresh-button').disabled = true;
  await Promise.all([loadCapabilities(), loadRetrievalStatus(), loadModels(), refreshJobLists()]);
  $('#refresh-button').disabled = false;
  toast('Runtime state refreshed');
}

async function loadRetrievalStatus() {
  try {
    const status = await api('/api/v1/retrieval-status');
    state.retrievalConfigured = status.configured;
    $('#retrieval-status').textContent = status.message;
    $('#retrieval-status').dataset.available = String(status.available);
  } catch (error) {
    $('#retrieval-status').textContent = `Unable to check visual search: ${error.message}`;
    $('#retrieval-status').dataset.available = 'false';
  }
}

async function loadCapabilities() {
  try {
    state.capabilities = await api('/api/v1/capabilities');
    $('#memory-capture-sessions').disabled = state.capabilities.media_policy !== 'sanitized-v1';
    const durationInput = $('#monitor-duration');
    durationInput.max = state.capabilities.max_analysis_secs;
    if (Number(durationInput.value) > state.capabilities.max_analysis_secs) {
      durationInput.value = state.capabilities.max_analysis_secs;
    }
    $('#service-dot').className = 'status-dot ok';
    $('#service-status').textContent = 'Service ready';
    $('#service-version').textContent = `v${state.capabilities.service_version} · ${state.capabilities.kafka_enabled ? 'Kafka' : 'No-op sink'}`;
    renderCapabilities();
    state.renderedMemoryJob = null;
  } catch (error) {
    $('#service-dot').className = 'status-dot error';
    $('#service-status').textContent = 'Service unavailable';
  }
}

async function loadModels() {
  try {
    const response = await api('/api/v1/models');
    state.models = response.models || [];
    state.configuredVlms = response.configured_vlms?.length ? response.configured_vlms : [...KNOWN_VLM_MODELS];
    if (response.available && state.models.length) {
      $('#gemma-dot').className = 'status-dot ok';
      $('#gemma-status').textContent = 'LM Studio reachable';
    } else {
      $('#gemma-dot').className = 'status-dot error';
      $('#gemma-status').textContent = 'Optional captions offline';
    }
    renderVlmOptions();
    renderModels(response.error);
  } catch (error) {
    state.models = [];
    $('#gemma-dot').className = 'status-dot error';
    $('#gemma-status').textContent = 'Optional captions offline';
    renderVlmOptions();
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
    if (sourceKind === 'cluster') {
      const job = await submitClusterJob();
      state.selectedJobId = job.id;
      state.selectedJobKind = 'cluster';
      inspectClusterJob(job);
      toast('Multi-camera cluster run submitted');
      await refreshJobLists();
      return;
    }
    let source;
    if (sourceKind === 'sample') {
      source = 'sample';
    } else if (sourceKind === 'upload') {
      if (!state.upload) throw new Error('Choose and finish uploading a video first.');
      source = { upload: { upload_id: state.upload.id } };
    } else if (sourceKind === 'rtsp') {
      const uri = $('#rtsp-uri').value.trim();
      if (!uri) throw new Error('Enter an RTSP address.');
      source = { rtsp: { uri } };
    } else {
      const uri = $('#http-uri').value.trim();
      if (!uri) throw new Error('Enter an HTTP or HTTPS stream address.');
      source = { http: { uri } };
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
      monitor_duration_secs: Number($('#monitor-duration').value),
      gemma_enabled: $('#gemma-enabled').checked,
      vlm_model: $('#memory-vlm-model').value,
      observations: backend === 'simulator' ? observations : [],
      policy,
    };
    const job = await api('/api/v1/jobs', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(request),
    });
    state.selectedJobId = job.id;
    state.selectedJobKind = 'single';
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

async function submitClusterJob() {
  const cameras = parseClusterCameras($('#cluster-cameras').value);
  if (cameras.length < 2) throw new Error('Enter at least two HTTP cameras for a cluster run.');
  const policyText = $('#policy-json').value.trim();
  const policy = policyText ? JSON.parse(policyText) : {};
  const mode = $('#cluster-mode').value;
  cameras.forEach((camera) => {
    camera.policy = policy;
    camera.overlap_group = mode === 'overlap' ? 'ui-overlap-group' : null;
  });
  const topologyText = $('#cluster-topology').value.trim();
  const topology = mode === 'topology' ? JSON.parse(topologyText || '[]') : [];
  if (mode === 'topology' && !Array.isArray(topology)) {
    throw new Error('Directed topology must be a JSON array.');
  }
  return api('/api/v1/cluster-jobs', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      name: $('#job-name').value.trim(),
      cluster_id: $('#cluster-id').value.trim(),
      cameras,
      topology,
      association: {},
      detector_fps: Number($('#detector-fps').value),
      monitor_duration_secs: Number($('#monitor-duration').value),
      gemma_enabled: $('#gemma-enabled').checked,
      vlm_model: $('#vlm-model').value,
    }),
  });
}

function parseClusterCameras(text) {
  const seen = new Set();
  return text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean).map((line, index) => {
    const parts = line.split('|').map((part) => part.trim());
    if (parts.length < 2) {
      throw new Error(`Camera line ${index + 1} must use: camera-id | label | http-url`);
    }
    const cameraId = parts[0];
    const uri = parts.length === 2 ? parts[1] : parts.slice(2).join('|');
    const cameraLabel = parts.length === 2 ? cameraId : parts[1];
    if (!cameraId || seen.has(cameraId)) throw new Error(`Camera ID "${cameraId}" is empty or duplicated.`);
    if (!/^https?:\/\//i.test(uri)) throw new Error(`Camera ${cameraId} must use an http:// or https:// URL.`);
    seen.add(cameraId);
    return { camera_id: cameraId, label: cameraLabel || cameraId, uri, clock_offset_ms: 0 };
  });
}

async function submitMemoryJob(event) {
  event.preventDefault();
  setInlineError('#memory-form-error', '');
  const button = $('#memory-run-button');
  button.disabled = true;
  button.querySelector('span').textContent = 'Submitting…';
  try {
    const stream = $('#memory-stream-url').value.trim();
    let cameras = stream ? [{live_url: stream}] : JSON.parse($('#memory-cameras-json').value || '[]');
    if (!Array.isArray(cameras)) cameras = [cameras];
    if (!cameras.length) throw new Error('Provide at least one backend camera payload.');
    const clusterId = $('#memory-cluster-id').value.trim();
    const request = {
      name: $('#memory-job-name').value.trim(),
      cluster_id: clusterId || null,
      cameras,
      monitor_duration_secs: Number($('#memory-duration').value),
      observer_fps: Number($('#memory-observer-fps').value),
      vlm_enabled: $('#memory-vlm-enabled').checked && !$('#memory-temporal-enabled').checked,
      long_memory_enabled: $('#memory-long-enabled').checked,
      capture_sessions: $('#memory-capture-sessions').checked ? true : undefined,
      temporal_enabled: $('#memory-temporal-enabled').checked,
      vlm_model: $('#memory-vlm-model').value,
    };
    // Keep only a digest and operation key across an uncertain acknowledgement.
    // The camera URL/credentials are never persisted in browser storage here.
    const intentJson = JSON.stringify(request);
    const digest = globalThis.crypto?.subtle
      ? Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(intentJson))), b => b.toString(16).padStart(2, '0')).join('')
      : null;
    let pending = state.pendingMemorySubmission;
    if (digest) {
      try { pending = JSON.parse(sessionStorage.getItem('visn.pending-recording.v1')) || pending; } catch (_) {}
    }
    if (!pending || pending.fingerprint !== (digest || intentJson)) {
      pending = {fingerprint: digest || intentJson, key: Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, '0')).join('')};
    }
    state.pendingMemorySubmission = pending;
    if (digest) { try { sessionStorage.setItem('visn.pending-recording.v1', JSON.stringify(pending)); } catch (_) {} }
    request.idempotency_key = pending.key;
    const job = await api('/api/v1/memory-jobs', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(request),
    });
    state.pendingMemorySubmission = null;
    try { sessionStorage.removeItem('visn.pending-recording.v1'); } catch (_) {}
    state.selectedMemoryJobId = job.id;
    $('#memory-query-cluster').value = clusterId;
    $('#memory-query-cameras').value = job.request.cameras.map(camera => camera.camera_id).join(', ');
    inspectMemoryJob(job);
    toast('Camera-memory indexing submitted');
    await loadMemoryJobs();
  } catch (error) {
    setInlineError('#memory-form-error', error.message);
  } finally {
    button.disabled = false;
    button.querySelector('span').textContent = 'Record and index';
  }
}

async function loadMemoryJobs() {
  try {
    state.memoryJobs = await api('/api/v1/memory-jobs');
    noticeRevocations(state.memoryJobs);
    $('#memory-count').textContent = state.memoryJobs.length;
    const options = state.memoryJobs.map(job => `<option value="${job.id}">${escapeHtml(job.request.name)} · ${escapeHtml(job.status)} · ${new Date(job.created_at_ms).toLocaleString()}</option>`).join('');
    if (state.memoryJobOptions !== options) {
      state.memoryJobOptions = options;
      $('#memory-job-select').innerHTML = options || '<option value="">No recordings yet</option>';
    }
    if (state.selectedMemoryJobId) {
      $('#memory-job-select').value = state.selectedMemoryJobId;
      const selected = state.memoryJobs.find((job) => job.id === state.selectedMemoryJobId);
      if (selected) inspectMemoryJob(selected);
    } else if (state.memoryJobs.length) {
      const initial = state.memoryJobs.find(job => job.status === 'completed' && job.result?.events_indexed > 0) || state.memoryJobs[0];
      inspectMemoryJob(initial);
    }
    updateMemorySearchScope();
    loadVisualIndex();
  } catch (error) {
    console.warn('Could not refresh memory jobs', error);
  }
}

async function changeMemoryRecording(action) {
  const id = state.selectedMemoryJobId;
  if (!id || state.memoryRecordingActionBusy) return;
  state.memoryRecordingActionBusy = true;
  $('#memory-recording-cancel').disabled = $('#memory-recording-resume').disabled = true;
  $('#memory-recording-revoke').disabled = true;
  const status = $('#memory-recording-action-status');
  status.textContent = action === 'revoke' ? 'Revoking recording access…' : action === 'cancel' ? 'Stopping recording…' : 'Checking saved evidence…';
  try {
    const job = await api(`/api/v1/memory-jobs/${id}/${action}`, {method: 'POST'});
    noticeRevocations([job]);
    if (id === state.selectedMemoryJobId) {
      state.renderedMemoryJob = null;
      inspectMemoryJob(job);
      status.textContent = action === 'revoke' ? revocationMessage(job) : action === 'cancel' ? 'Recording stopped. Already committed evidence remains available for recovery.' : 'Resuming saved evidence only. No new live interval is being opened.';
      $('#memory-job-select').focus();
    }
  } catch (error) {
    status.textContent = `Recording action unavailable: ${error.message}`;
  } finally {
    state.memoryRecordingActionBusy = false;
    await loadMemoryJobs();
  }
}

function revocationMessage(job) {
  const cleanup = job.revocation?.cleanup_state;
  const local = job.revocation?.local_cleanup_state;
  return `Access revoked. ${cleanup === 'completed' && local === 'completed' ? 'Derived cache cleanup completed.' : cleanup === 'blocked' || local === 'blocked' ? 'Cache cleanup is blocked; check storage and worker configuration, then retry.' : 'Cache cleanup is saved and will resume when storage and the worker are available.'} Retained media follows the existing retention policy.`;
}

async function loadEvidenceStatistics(rebuild) {
  const id = state.selectedMemoryJobId;
  if (!id || state.evidenceStatisticsBusy) return;
  const accessRevision = state.memoryAccessRevision;
  state.evidenceStatisticsBusy = true;
  state.evidenceStatistics = null;
  renderEvidenceStatistics(state.memoryJobs.find(job => job.id === id));
  try {
    const report = await api(`/api/v1/memory-jobs/${id}/${rebuild ? 'metadata/rebuild' : 'analytics'}`, {method:'POST', headers:{'content-type':'application/json'}, body:'{}'});
    if (accessRevision === state.memoryAccessRevision && id === state.selectedMemoryJobId && !rebuild) state.evidenceStatistics = {id, report};
  } catch (error) {
    if (accessRevision === state.memoryAccessRevision && id === state.selectedMemoryJobId) state.evidenceStatistics = {id, error:error.message};
  } finally {
    state.evidenceStatisticsBusy = false;
    await loadMemoryJobs();
  }
}

function renderEvidenceStatistics(job) {
  const metadata = job?.metadata_projection;
  const available = job?.status === 'completed' && !job.revocation;
  $('#memory-metadata-show').disabled = state.evidenceStatisticsBusy || !available || metadata?.state !== 'completed';
  $('#memory-metadata-rebuild').disabled = state.evidenceStatisticsBusy || !available || metadata?.state === 'pending';
  const saved = state.evidenceStatistics?.id === job?.id ? state.evidenceStatistics : null;
  const status = $('#memory-metadata-status');
  if (job?.revocation) { status.textContent = 'Access revoked. Evidence statistics are unavailable.'; return; }
  if (state.evidenceStatisticsBusy) { status.textContent = 'Checking saved evidence metadata…'; return; }
  if (saved?.error) { status.textContent = `Evidence statistics unavailable: ${saved.error}`; return; }
  if (saved?.report && saved.report.revision === metadata?.revision) {
    status.textContent = saved.report.cameras.map(camera => `${camera.camera_id}${camera.source_session_id ? ` · source ${camera.source_session_id.slice(0,8)}` : ''}: ${camera.indexed_events} indexed intervals, ${camera.indexed_interval_ms} ms of interval coverage; ${camera.observed_tracker_entities === null ? 'tracker counts unavailable' : `${camera.observed_tracker_entities} observed tracker histories`}.`).join(' ') + (saved.report.failed_cameras.length ? ` ${saved.report.failed_cameras.length} cameras have no committed result.` : '');
    return;
  }
  status.textContent = !available ? 'Select a completed recording.' : !metadata ? 'No saved metadata projection yet. Rebuild it from retained evidence.' : metadata.state === 'completed' ? `Saved ${metadata.version} projection. Statistics are verified when requested.` : metadata.state === 'blocked' ? `Metadata preparation blocked: ${metadata.last_error || 'check retained evidence, then rebuild'}` : 'Evidence metadata is queued. It survives a service restart.';
}

function clearMemoryAnswer() {
  $('#memory-query-result').querySelectorAll('video').forEach(video => { video.pause(); video.removeAttribute('src'); video.load(); });
  $('#memory-query-result').classList.add('hidden');
  for (const id of ['memory-query-json','memory-query-title','memory-query-summary','memory-query-mode','memory-query-notes','memory-query-journeys','memory-query-matches']) $(`#${id}`).textContent = '';
}

function noticeRevocations(jobs) {
  const newlyRevoked = jobs.filter(job => job.revocation && !state.knownRevokedMemoryIds.has(job.id));
  if (!newlyRevoked.length) return;
  newlyRevoked.forEach(job => state.knownRevokedMemoryIds.add(job.id));
  state.memoryAccessRevision += 1;
  state.evidenceStatistics = null;
  clearMemoryAnswer();
}

function renderMemoryEntityOptions(job) {
  const select = $('#memory-query-entity');
  const previous = select.value;
  const session = $('#memory-query-session').value;
  const entities = (job?.result?.camera_results || []).map(c => c.long_memory)
    .filter(g => g && (!state.capabilities?.entity_memory_version || g.version === state.capabilities.entity_memory_version))
    .flatMap(g => g.entities).filter(entity => !session || entity.source_session_id === session);
  select.innerHTML = '<option value="">Find candidates from question</option>' + entities.map(entity => `<option value="${entity.entity_id}">${escapeHtml(entity.class_name)} · track ${escapeHtml(entity.track_id)}${entity.source_session_id ? ` · source ${escapeHtml(entity.source_session_id.slice(0,8))}` : ''} · ${escapeHtml(entity.camera_id)}</option>`).join('');
  if (entities.some(entity => entity.entity_id === previous)) select.value = previous;
}

function inspectMemoryJob(job) {
  const renderKey = `${job.id}:${job.status}:${job.completed_at_ms}:${Object.keys(job.camera_checkpoints || {}).length}:${job.recovery_attempts || 0}:${JSON.stringify(job.result?.camera_results.map(c => [c.long_memory?.version, c.long_memory?.source_sha256, c.long_memory?.entities.length]))}`;
  const active = ['queued', 'running'].includes(job.status);
  const recoverable = !job.revocation && ['failed', 'cancelled'].includes(job.status) && state.capabilities?.media_policy === 'sanitized-v1';
  $('#memory-recording-cancel').classList.toggle('hidden', !active);
  $('#memory-recording-resume').classList.toggle('hidden', !recoverable);
  $('#memory-recording-cancel').disabled = $('#memory-recording-resume').disabled = state.memoryRecordingActionBusy;
  $('#memory-recording-revoke').disabled = state.memoryRecordingActionBusy;
  $('#memory-recording-revoke').classList.toggle('hidden', !!job.revocation && job.revocation.cleanup_state !== 'blocked' && job.revocation.local_cleanup_state !== 'blocked');
  $('#memory-recording-revoke').textContent = job.revocation ? 'Retry revocation cleanup' : 'Revoke recording access';
  if (job.revocation) {
    $('#memory-query-session').innerHTML = '<option value="">All source sessions</option>';
    $('#memory-query-session').disabled = true;
    $('#memory-recording-action-status').textContent = revocationMessage(job);
    state.visualIndex = null;
    $('#memory-query-result').classList.add('hidden');
    $('#memory-camera-results').querySelectorAll('video').forEach(video => { video.pause(); video.removeAttribute('src'); video.load(); });
    $('#memory-camera-results').textContent = '';
    $('#memory-raw-json').textContent = '';
  }
  renderEvidenceStatistics(job);
  if (state.renderedMemoryJob === renderKey) { renderVisualIndex(); return; }
  if (state.selectedMemoryJobId !== job.id) { state.visualIndex = null; setInlineError('#visual-index-error', ''); }
  state.renderedMemoryJob = renderKey;
  if (!state.memoryRecordingActionBusy && !job.revocation) {
    $('#memory-recording-action-status').textContent = `${Object.keys(job.camera_checkpoints || {}).length}/${job.request.cameras.length} camera checkpoints saved. Recovery uses saved evidence only; a new live interval requires a new recording.`;
  }
  state.selectedMemoryJobId = job.id;
  state.searchAllRecordings = false;
  $('#memory-query-cluster').value = job.request.cluster_id || '';
  $('#memory-query-cameras').value = [...new Set((job.result?.camera_results || []).map(result => result.camera.camera_id))].join(', ');
  $('#memory-query-result').classList.add('hidden');
  const memories = (job.result?.camera_results || []).map(c => c.long_memory).filter(Boolean);
  const sourceSessions = (job.result?.camera_results || []).filter(c => c.source_session_id);
  const selectedSourceSession = $('#memory-query-session').value;
  $('#memory-query-session').innerHTML = '<option value="">All source sessions</option>' + sourceSessions.map((camera,index) => `<option value="${escapeHtml(camera.source_session_id)}">Source ${index+1} · ${escapeHtml(camera.source_session_id.slice(0,8))} · ${escapeHtml(camera.camera.camera_id)}</option>`).join('');
  $('#memory-query-session').disabled = !!job.revocation || !sourceSessions.length;
  if (sourceSessions.some(c => c.source_session_id === selectedSourceSession)) $('#memory-query-session').value = selectedSourceSession;
  const outdated = memories.some(g => state.capabilities?.entity_memory_version && g.version !== state.capabilities.entity_memory_version);
  const entities = memories.filter(g => !state.capabilities?.entity_memory_version || g.version === state.capabilities.entity_memory_version).flatMap(g => g.entities);
  renderMemoryEntityOptions(job);
  if (!state.buildingEntityMemory) $('#memory-links-status').textContent = outdated ? 'Saved tracker memory uses an older sampling version. Rebuild entity memory for this recording.' : entities.length ? `${entities.length} tracker histories saved. Same-track links are not verified identities.` : (job.result?.camera_results.some(c => c.long_memory?.status === 'ready') ? 'Entity memory built; no confirmed tracks found.' : 'No linked entity memory for this recording yet.');
  updateMemorySearchScope(job);
  renderVisualIndex();
  $('#memory-job-select').value = job.id;
  $('#memory-empty').classList.add('hidden');
  const badge = $('#memory-job-status');
  badge.textContent = job.status;
  badge.className = `badge ${job.status}`;
  $('#memory-processing').classList.toggle('hidden', !['queued', 'running'].includes(job.status));
  $('#memory-job-error').classList.toggle('hidden', !['failed', 'cancelled'].includes(job.status));
  $('#memory-job-error').textContent = job.error || '';
  if (!job.result) {
    $('#memory-result').classList.add('hidden');
    return;
  }
  const result = job.result;
  $('#memory-result').classList.remove('hidden');
  $('#memory-metric-cameras').textContent = `${result.cameras_processed}/${result.cameras_requested}`;
  $('#memory-metric-events').textContent = result.events_indexed;
  $('#memory-metric-frames').textContent = result.observer_frames_decoded;
  $('#memory-metric-duration').textContent = formatDuration(result.source_duration_ms);
  $('#memory-metric-duration-label').textContent = job.request.capture_sessions ? 'Longest session' : 'Duration';
  $('#memory-camera-results').innerHTML = result.camera_results.map(renderMemoryCamera).join('') +
    (result.camera_failures || []).map((failure) => `<article class="camera-result-card error"><div class="camera-result-head"><strong>${escapeHtml(failure.camera_id)}</strong><small>failed</small></div><p>${escapeHtml(failure.error)}</p></article>`).join('');
  $('#memory-raw-json').textContent = JSON.stringify(result, null, 2);
}

async function loadVisualIndex() {
  const id = state.selectedMemoryJobId;
  if (state.memoryJobs.find(job => job.id === id)?.revocation) {
    state.visualIndex = null;
    renderVisualIndex();
    $('#memory-query-queue').textContent = 'Recording access is revoked. It is excluded from search and playback.';
    return;
  }
  if (!id || state.visualIndexPolling || state.visualIndexBusy) return;
  if (state.visualIndexRetryJob !== id) {
    state.visualIndexRetryJob = id;
    state.visualIndexRetryAt = 0;
    state.visualIndexFailures = 0;
  }
  if (Date.now() < state.visualIndexRetryAt) return;
  state.visualIndexPolling = true;
  try {
    const result = await api(`/api/v1/memory-jobs/${id}/visual-index`);
    if (id !== state.selectedMemoryJobId) return;
    state.visualIndexFailures = 0;
    state.visualIndexRetryAt = 0;
    state.visualIndex = result.jobs.find(job => job.profile_current === true) || result.jobs[0] || null;
    const queue = result.scheduler || {};
    $('#memory-query-queue').textContent = queue.active || queue.interactive_waiting
      ? `${queue.active === 'background' ? 'Background preparation is using the model; questions take priority after the current interval.' : queue.active ? 'The model is serving a question.' : 'Model available.'} ${queue.interactive_waiting || 0} question requests waiting.`
      : 'Model slot available. Questions and background preparation share one inference slot.';
    const resources = result.resources;
    if (resources?.enabled && resources.external_recovery_required) {
      $('#memory-query-queue').textContent = 'Visual search is waiting for model-service recovery. Each search will report the retrieval mode used.';
    } else if (resources?.enabled && (resources.this_process_waiting || (resources.owner && resources.owner.role !== 'retrieval'))) {
      $('#memory-query-queue').textContent = 'Shared resources are busy. Visual preparation and questions will continue when a slot is available.';
    }
    renderVisualIndex();
  } catch (error) {
    if (id === state.selectedMemoryJobId) {
      state.visualIndexFailures += 1;
      const retryMs = Math.min(60000, 5000 * 2 ** Math.min(state.visualIndexFailures - 1, 4));
      state.visualIndexRetryAt = Date.now() + retryMs;
      const recording = state.memoryJobs.find(job => job.id === id);
      $('#visual-index-status').textContent = `${visualProjectionMessage(recording) || 'Visual preparation unavailable.'} Worker status unavailable: ${error.message}`;
      $('#memory-query-queue').textContent = `Queue status unavailable. Retrying in ${retryMs / 1000}s. Each search reports the retrieval mode actually used.`;
    }
  } finally { state.visualIndexPolling = false; }
}

function visualProjectionMessage(recording) {
  if (recording?.revocation) return revocationMessage(recording);
  const projection = recording?.visual_projection;
  if (!projection) return '';
  if (projection.state === 'pending') return state.retrievalConfigured === false
    ? 'Visual preparation intent is saved. Configure the visual worker to continue.'
    : 'Visual preparation intent is saved and will be retried automatically. Closing this page does not discard it.';
  if (projection.state === 'submitted') return 'Visual preparation was accepted by the worker. Waiting for verified completion.';
  if (projection.state === 'blocked') return 'Saved visual preparation is blocked. Check the source and worker configuration, then prepare again.';
  if (projection.state === 'completed') return 'The saved worker receipt reports all requested intervals prepared. Check the current worker profile before relying on readiness.';
  return '';
}

function renderVisualIndex() {
  const recording = state.memoryJobs.find(job => job.id === state.selectedMemoryJobId);
  const task = state.visualIndex?.scope_id === state.selectedMemoryJobId ? state.visualIndex : null;
  const active = task && ['queued','running'].includes(task.status);
  $('#visual-index-start').disabled = state.visualIndexBusy || active || recording?.status !== 'completed' || !recording?.result?.events_indexed;
  $('#visual-index-start').textContent = task?.status === 'completed' ? 'Check preparation again' : 'Prepare this recording';
  $('#visual-index-cancel').classList.toggle('hidden', !active);
  $('#visual-index-retry').classList.toggle('hidden', !task || !['failed','cancelled'].includes(task.status));
  $('#visual-index-cancel').disabled = $('#visual-index-retry').disabled = state.visualIndexBusy;
  const progress = $('#visual-index-progress');
  progress.classList.toggle('hidden', !task);
  progress.max = task?.total || 1;
  progress.value = task?.completed || 0;
  $('#visual-index-status').textContent = !task
    ? visualProjectionMessage(recording) || 'No visual preparation task for this recording. You can prepare it now or search directly.'
    : `${label(task.status)} · ${task.completed}/${task.total} intervals prepared${task.failed ? ` · ${task.failed} failed` : ''}. ${task.status === 'completed' ? 'All requested intervals prepared; this does not certify scene or action accuracy.' : task.status === 'cancelled' ? 'Completed artifacts are retained. An interval already in progress may finish.' : task.status === 'failed' ? 'Coverage is incomplete. Retry, or prepare again if the source or model changed.' : 'Questions may run between intervals. Closing this page does not stop the task.'}`;
  if (task?.status === 'completed' && task.profile_current !== true) {
    $('#visual-index-status').textContent += task.profile_current === false
      ? ' This task used a different model profile. Prepare again for the current worker.'
      : ' The current worker profile has not been resolved yet. Check preparation again to confirm readiness.';
  }
}

async function changeVisualIndex(action = '') {
  const id = state.selectedMemoryJobId;
  const task = state.visualIndex;
  if (!id || (action && (!task || task.scope_id !== id))) return;
  state.visualIndexBusy = true;
  setInlineError('#visual-index-error', '');
  renderVisualIndex();
  try {
    const suffix = action ? `/${task.id}/${action}${task.deployment_generation ? `?deployment_generation=${encodeURIComponent(task.deployment_generation)}` : ''}` : '';
    const result = await api(`/api/v1/memory-jobs/${id}/visual-index${suffix}`, {method:'POST'});
    if (id === state.selectedMemoryJobId) state.visualIndex = result;
  } catch (error) {
    if (id === state.selectedMemoryJobId) setInlineError('#visual-index-error', error.message);
  } finally {
    state.visualIndexBusy = false;
    state.visualIndexRetryAt = 0;
    renderVisualIndex();
    loadVisualIndex();
  }
}

function renderCaptureSessions(events) {
  const source = events.find(event => event.privacy?.source?.capture)?.privacy.source;
  const capture = source?.capture;
  const sessions = capture?.association_sessions || [];
  if (!sessions.length) return '<section class="capture-timeline"><h3>Observation timeline</h3><p>Session boundaries are unavailable for this recording. Continuous capture has not been verified.</p></section>';
  const seconds = ms => `${(ms / 1000).toFixed(3)}s`;
  const sourceClock = source.clock_kind === 'source_pts';
  return `<section class="capture-timeline"><h3>Observation timeline</h3>
    <p>${sourceClock ? 'Times come from source presentation timestamps, relative to the first frame.' : 'Times are observed decoder arrival times, not verified camera capture times.'} These ranges show observed frames; they do not certify continuous coverage or real-world identity.</p>
    <details><summary>${sessions.length} observation session${sessions.length === 1 ? '' : 's'} · ${sessions.length - 1} timing boundar${sessions.length === 2 ? 'y' : 'ies'}</summary>
      <ol>${sessions.map((session, index) => `<li>
        ${index ? `<p class="capture-boundary">${seconds(session.first_observed_ms - sessions[index - 1].last_observed_ms)} between observed frames. Variable frame rate and missing observations cannot be distinguished; tracker association restarts here.</p>` : ''}
        <strong>Session ${index + 1} · ${seconds(session.first_observed_ms)}–${seconds(session.last_observed_ms)}</strong>
        <p>${session.last_frame_index - session.first_frame_index + 1} consumed frames · ${index ? 'Starts after an observation timing boundary' : 'Starts with the first observed frame'}</p>
        <small>Session ID: ${escapeHtml(session.session_id)}</small>
      </li>`).join('')}</ol>
    </details>
    <p>Capture ended: ${escapeHtml(label(capture.termination))}. Recording and transport times do not establish when the scene occurred.</p>
  </section>`;
}

function renderProducerProfile(events) {
  const source = events.find(event => event.privacy?.source)?.privacy.source;
  if (!source) return '';
  if (!source.sanitizer_code_sha256 || !source.models || !source.runtime) {
    return '<section class="capture-timeline"><h3>Processing details unavailable</h3><p>This saved record does not contain a complete producer recipe. Its processing profile cannot be inferred from the current server settings.</p></section>';
  }
  const runtime = source.runtime;
  const fields = [
    ['Sanitizer', `${label(source.sanitizer || 'unavailable')} · audio ${source.audio || 'unknown'}`],
    ['Detection threshold', source.detector_threshold],
    ...['yunet', 'plate'].map(kind => [kind === 'yunet' ? 'Face model SHA-256' : 'Plate model SHA-256', source.models[kind]?.sha256 || 'Unavailable']),
    ['Sanitizer code SHA-256', source.sanitizer_code_sha256],
    ['Redaction tracker code SHA-256', source.tracker_code_sha256 || 'Unavailable'],
    ['Runtime', `OpenCV ${runtime.opencv || '?'} · ONNX Runtime ${runtime.onnxruntime || '?'} · PyAV ${runtime.pyav || '?'} · NumPy ${runtime.numpy || '?'}`],
    ['Inference', `${runtime.precision || 'Unknown precision'} · ${(Array.isArray(runtime.providers) ? runtime.providers : []).join(', ') || 'Unknown provider'} · ${runtime.threads ?? '?'} thread(s)`],
  ];
  return `<details class="capture-timeline"><summary>Processing details · sanitized evidence</summary>
    <p>This recipe belongs to the saved footage. It is an integration candidate; general privacy detection accuracy has not been established.</p>
    <dl>${fields.map(([key, value]) => `<dt>${escapeHtml(key)}</dt><dd>${escapeHtml(String(value ?? 'Unavailable'))}</dd>`).join('')}</dl>
  </details>`;
}

function renderMemoryCamera(cameraResult) {
  const camera = cameraResult.camera;
  const location = [camera.city, camera.region, camera.country].filter(Boolean).join(', ');
  const events = cameraResult.events || [];
  return `<section class="memory-camera-block">
    <div class="memory-camera-heading">
      <div><strong>${escapeHtml(camera.camera_id)}</strong><small>${escapeHtml(location || camera.manufacturer || 'Metadata pending')}</small></div>
      <span>${events.length} event${events.length === 1 ? '' : 's'} · ${cameraResult.frames_decoded} source frames</span>
    </div>
    ${camera.description ? `<p class="memory-camera-description">${escapeHtml(camera.description)}</p>` : ''}
    ${cameraResult.capture_extent === 'retained_prefix' ? '<aside class="capture-timeline"><strong>Recovered footage only</strong><p>The original capture was interrupted. Recovery indexed only committed sessions; missing footage remains unavailable and no new live interval was recorded.</p></aside>' : ''}
    ${renderCaptureSessions(events)}
    ${renderProducerProfile(events)}
    <div class="memory-event-grid">${events.map(event => renderMemoryEvent(event)).join('')}</div>
    ${(cameraResult.data_quality_notes || []).length ? `<div class="report-notes">${cameraResult.data_quality_notes.map((note) => `<span>${escapeHtml(note)}</span>`).join('')}</div>` : ''}
  </section>`;
}

function renderJourneys(answer) {
  if (!answer) return '';
  const evidence = new Map(answer.supporting_events.map(e => [e.event_id, e]));
  return `<section class="linked-memory"><h3>Linked evidence · ${escapeHtml(label(answer.status))}</h3>
    <p>${escapeHtml(answer.summary)}</p>
    <div class="report-notes">${answer.notes.map(n => `<span>${escapeHtml(n)}</span>`).join('')}</div>
    ${answer.entities.map(entity => `<details class="entity-history"><summary>${escapeHtml(entity.class_name)} · ${escapeHtml(entity.camera_id)} · track ${escapeHtml(entity.track_id)} · ${entity.observations.length} observations</summary>
      <p>${escapeHtml(entity.uncertainty)}</p><ol>${entity.observations.map(o => {const event=evidence.get(o.event_id);return `<li><b>${(o.timestamp_ms/1000).toFixed(2)}s · ${escapeHtml(label(o.kind))}</b><p>${escapeHtml(o.kind === 'detector_observation' ? `${entity.class_name} detected at this time. Review the clip to confirm.` : o.description)}</p>${o.kind === 'detector_observation' ? `<details><summary>Detection details</summary><small>${escapeHtml(o.description)}</small></details>` : ''}${event ? `<a href="${event.evidence_url}#t=${Math.max(0,(o.timestamp_ms-event.start_ms)/1000).toFixed(3)}" target="_blank" rel="noopener">Review source clip · ${(event.start_ms/1000).toFixed(2)}–${(event.end_ms/1000).toFixed(2)}s ↗</a>` : '<span>Evidence unavailable</span>'}</li>`;}).join('')}</ol>
      <small>Recording ${escapeHtml(entity.job_id)} · entity ${escapeHtml(entity.entity_id)}</small>
    </details>`).join('')}
    <details><summary>Review supporting clips (${answer.supporting_events.length})</summary><div class="memory-event-grid">${answer.supporting_events.map(e=>renderMemoryEvent(e)).join('')}</div></details>
  </section>`;
}

function renderInterpretation(value) {
  if (!value) return '';
  const cropSizes = value.provenance.crop_evidence?.crop_sizes || [];
  return `<section class="sequence-evidence" aria-label="Ordered visual evidence">
    <strong>Experiment 2 · ${value.status === 'supported' ? 'Unverified model interpretation' : value.status === 'not_observed' ? 'Target not observed in sampled frames' : 'Uncertain interpretation'}</strong>
    <p>${escapeHtml(value.answer)}</p>
    <small>Action-direction errors were observed in testing. Review the frames before relying on this interpretation.</small>
    <small>Source window ${(value.start_ms / 1000).toFixed(2)}–${(value.end_ms / 1000).toFixed(2)}s · ${escapeHtml(value.provenance.model || '')}${value.cached ? ' · cached' : ''}</small>
    ${value.provenance.state_verification ? `<small>Separate visual-state check: ${escapeHtml(value.provenance.state_verification.status)} · ${escapeHtml(value.provenance.state_verification.reason)}${value.provenance.state_verification.before ? ` · ${escapeHtml(value.provenance.state_verification.before)} → ${escapeHtml(value.provenance.state_verification.after)}` : ''}</small>` : ''}
    <details><summary>Review ordered frames and observations</summary>
      <div class="sequence-frames">${(value.frames || []).map((frame, index) => `<figure><img src="${frame.image}" loading="lazy" alt="Sequence frame ${index + 1} near ${(frame.timestamp_ms / 1000).toFixed(2)} seconds">${frame.target_crop ? `<img src="${frame.target_crop}" loading="lazy" alt="Selected tracker crop for frame ${index + 1}">` : ''}<figcaption title="Source seek target; sparse video can repeat an earlier valid image">seek ≈${(frame.timestamp_ms / 1000).toFixed(2)}s${Number.isFinite(frame.decoded_timestamp_ms) ? ` · decoded ${(frame.decoded_timestamp_ms / 1000).toFixed(2)}s` : ''}${frame.target_crop && Array.isArray(cropSizes[index]) ? `<small>${value.provenance.crop_evidence?.mode === 'source' ? 'Source crop' : 'Preview crop'} · ${escapeHtml(cropSizes[index].join(' × '))} px</small>` : ''}</figcaption></figure>`).join('')}</div>
      <ol>${value.observations.map(item => `<li><b>${(item.timestamp_ms / 1000).toFixed(2)}s</b> ${escapeHtml(item.target_visibility || 'unclassified')} · ${escapeHtml(item.description)}</li>`).join('')}</ol>
      ${value.limitations.map(note => `<p><small>${escapeHtml(note)}</small></p>`).join('')}
    </details></section>`;
}

function renderMemoryEvent(event, match = null) {
  const description = event.description || {};
  const tags = [...(description.visible_objects || []), ...(description.apparent_actions || []), ...(description.visible_text || [])].slice(0, 6);
  const score = match ? `<span class="memory-score">relevance ${Number(match.score).toFixed(3)}</span>` : '';
  return `<article class="memory-event-card">
    <img src="${event.thumbnail_url}" alt="Representative evidence frame from ${escapeHtml(event.camera_id)}" loading="lazy">
    <div class="memory-event-body">
      <div class="memory-event-meta"><span>${formatDuration(event.start_ms)}–${formatDuration(event.end_ms)}</span><span>activity ${Number(event.activity_peak).toFixed(2)}</span>${score}</div>
      <strong>${escapeHtml(description.generated_by_model ? description.scene_type : 'Recorded evidence')}</strong>
      <p>${escapeHtml(description.generated_by_model ? description.summary : 'No generated caption. Visual retrieval examines the recorded clip directly.')}</p>
      ${description.fallback_reason ? `<details><summary>Caption status</summary><small>${escapeHtml(description.fallback_reason)}</small></details>` : ''}
      ${tags.length ? `<div class="memory-tags">${tags.map((tag) => `<span>${escapeHtml(tag)}</span>`).join('')}</div>` : ''}
      ${match?.matched_terms?.length ? `<small class="matched-terms">Matched: ${escapeHtml(match.matched_terms.join(', '))}</small>` : ''}
      ${renderInterpretation(event.interpretation)}
      <video controls preload="none" poster="${event.thumbnail_url}" src="${event.evidence_url}"></video>
    </div>
  </article>`;
}

function updateMemorySearchScope(selected = null) {
  const job = selected || state.memoryJobs.find(job => job.id === state.selectedMemoryJobId);
  const ready = state.searchAllRecordings
    ? state.memoryJobs.some(job => job.result?.events_indexed > 0)
    : job?.status === 'completed' && job.result?.events_indexed > 0;
  $('#memory-search-scope').textContent = state.searchAllRecordings
    ? 'Searching all indexed recordings, subject to the filters below.'
    : ready ? `Searching selected recording: ${job.request.name} · ${job.result.events_indexed} indexed clips.`
    : job?.revocation ? 'Selected recording access is revoked. Search another recording or all permitted recordings.'
    : `Selected recording is ${job?.status || 'not available'}. Choose a completed recording with indexed clips, or search all recordings.`;
  $('#memory-query-button').disabled = state.memoryQueryBusy || !ready;
  $('#memory-build-links').disabled = state.buildingEntityMemory || job?.status !== 'completed' || !job?.result?.events_indexed;
}

function memoryQueryRequest() {
  return {
    query: $('#memory-query-text').value.trim(),
    job_id: state.searchAllRecordings ? null : state.selectedMemoryJobId,
    semantic_enabled: $('#memory-query-semantic').checked,
    rerank_enabled: $('#memory-query-rerank').checked,
    cluster_id: $('#memory-query-cluster').value.trim() || null,
    camera_ids: $('#memory-query-cameras').value.split(',').map(id => id.trim()).filter(Boolean),
    source_session_ids: $('#memory-query-session').value ? [$('#memory-query-session').value] : [],
    limit: 12,
    vlm_enabled: $('#memory-query-vlm').checked && !$('#memory-query-temporal').checked && !$('#memory-query-long').checked,
    temporal_enabled: $('#memory-query-temporal').checked,
    long_memory_enabled: $('#memory-query-long').checked,
    entity_id: ($('#memory-query-long').checked || $('#memory-query-temporal').checked) ? ($('#memory-query-entity').value || null) : null,
    vlm_model: $('#memory-vlm-model').value,
  };
}

async function submitMemoryQuery(event) {
  event.preventDefault();
  setInlineError('#memory-query-error', '');
  const button = $('#memory-query-button');
  button.disabled = true;
  state.memoryQueryBusy = true;
  button.querySelector('span').textContent = $('#memory-query-temporal').checked ? 'Retrieving and reading frames…' : 'Searching…';
  try {
    const accessRevision = state.memoryAccessRevision;
    const submittedRequest = JSON.stringify(memoryQueryRequest());
    const response = await api('/api/v1/memory-query', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: submittedRequest,
    });
    if (accessRevision !== state.memoryAccessRevision) throw new Error('Recording access changed while searching. Search again using permitted recordings.');
    if (submittedRequest !== JSON.stringify(memoryQueryRequest())) throw new Error('Search inputs changed while searching. Search again using the current filters.');
    renderMemoryQuery(response);
    loadRetrievalStatus();
  } catch (error) {
    setInlineError('#memory-query-error', error.message);
  } finally {
    button.disabled = false;
    state.memoryQueryBusy = false;
    button.querySelector('span').textContent = 'Search memory';
    updateMemorySearchScope();
  }
}

function renderMemoryQuery(result) {
  $('#memory-query-result').classList.remove('hidden');
  $('#memory-query-json').textContent = JSON.stringify(result, null, 2);
  $('#memory-query-title').textContent = result.long_memory ? (result.long_memory.entities.length ? `${result.long_memory.entities.length} linked tracker ${result.long_memory.entities.length === 1 ? 'history' : 'histories'}` : 'No linked entity evidence') : result.matches.length ? `${result.matches.length} evidence candidates` : result.events_considered === 0 ? 'No clips were searched' : 'No matching evidence';
  $('#memory-query-summary').textContent = result.summary;
  $('#memory-query-mode').textContent = (result.long_memory ? 'Linked memory · ' : '') + ({hybrid_qwen_reranker: 'Visual + keyword · Qwen reranked', hybrid_rrf: 'Visual + keyword · rank fusion', local_lexical_metadata: 'Keyword only', not_run_empty_index: 'Not searched · no indexed clips', not_run_filtered_empty: 'Not searched · filters excluded all clips'}[result.retrieval_mode] || label(result.retrieval_mode)) + (result.model ? ` · Summary: ${result.model}` : '');
  const notes = [`${result.events_considered} filtered events considered`, ...(result.retrieval_notes || []), ...(result.interpretation_notes || [])];
  if (result.fallback_reason) notes.push(`VLM fallback: ${result.fallback_reason}`);
  $('#memory-query-notes').innerHTML = notes.map((note) => `<span>${escapeHtml(note)}</span>`).join('');
  $('#memory-query-journeys').innerHTML = renderJourneys(result.long_memory);
  $('#memory-query-matches').innerHTML = result.matches.length
    ? result.matches.map((match) => renderMemoryEvent({...match.event, interpretation: (result.interpretations || []).find(item => item.event_id === match.event.event_id) || match.event.interpretation}, match)).join('')
    : `<div class="empty-row">${result.long_memory?.entities.length ? 'No additional retrieval candidates. Linked evidence is shown above.' : 'No events matched the current query and filters.'}</div>`;
}

function setInlineError(selector, message) {
  const element = $(selector);
  element.textContent = message || '';
  element.classList.toggle('hidden', !message);
}

async function loadJobs() {
  try {
    const jobs = await api('/api/v1/jobs');
    state.jobs = jobs;
    updateRunState();
    renderRuns();
    if (state.selectedJobId && state.selectedJobKind === 'single') {
      const selected = jobs.find((job) => job.id === state.selectedJobId);
      if (selected) inspectJob(selected);
    }
  } catch (error) {
    console.warn('Could not refresh jobs', error);
  }
}

async function loadClusterJobs() {
  try {
    state.clusterJobs = await api('/api/v1/cluster-jobs');
    updateRunState();
    renderRuns();
    if (state.selectedJobId && state.selectedJobKind === 'cluster') {
      const selected = state.clusterJobs.find((job) => job.id === state.selectedJobId);
      if (selected) inspectClusterJob(selected);
    }
  } catch (error) {
    console.warn('Could not refresh cluster jobs', error);
  }
}

async function refreshJobLists() {
  await Promise.all([loadJobs(), loadClusterJobs(), loadMemoryJobs()]);
}

function updateRunState() {
  $('#run-count').textContent = state.jobs.length + state.clusterJobs.length;
}

function inspectJob(job) {
  state.selectedJobId = job.id;
  state.selectedJobKind = 'single';
  $('#empty-inspector').classList.add('hidden');
  $('#job-inspector').classList.remove('hidden');
  $('#job-title').textContent = job.request.name;
  $('#job-kicker').textContent = `RUN / ${shortId(job.id)}`;
  const status = $('#job-status');
  status.textContent = job.status;
  status.className = `badge ${job.status}`;
  $('#processing-state').classList.toggle('hidden', !['queued', 'running'].includes(job.status));
  $('#processing-help').textContent = ['http', 'rtsp'].some((kind) => job.request.source?.[kind])
    ? `Monitoring the stream for up to ${job.request.monitor_duration_secs} seconds, then generating insights.`
    : 'Results update automatically.';
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

function inspectClusterJob(job) {
  state.selectedJobId = job.id;
  state.selectedJobKind = 'cluster';
  $('#empty-inspector').classList.add('hidden');
  $('#job-inspector').classList.remove('hidden');
  $('#job-title').textContent = job.request.name;
  $('#job-kicker').textContent = `CLUSTER / ${job.request.cluster_id} / ${shortId(job.id)}`;
  const status = $('#job-status');
  status.textContent = job.status;
  status.className = `badge ${job.status}`;
  $('#processing-state').classList.toggle('hidden', !['queued', 'running'].includes(job.status));
  $('#processing-help').textContent = `Monitoring ${job.request.cameras.length} cameras for up to ${job.request.monitor_duration_secs} seconds, then associating identities and generating cluster insights.`;
  $('#job-error').classList.toggle('hidden', job.status !== 'failed');
  $('#job-error').textContent = job.error || '';
  const video = $('#video-preview');
  video.removeAttribute('src');
  video.classList.add('hidden');
  if (!job.result) {
    $('#result-content').classList.add('hidden');
    return;
  }
  renderClusterResult(job.result);
}

function renderResult(result) {
  $('#result-content').classList.remove('hidden');
  $('#cluster-camera-section').classList.add('hidden');
  $('#event-section-title').textContent = 'Event timeline';
  $('#track-section-title').textContent = 'Confirmed tracks';
  $('#metric-observations').textContent = result.observations_processed;
  $('#metric-tracks').textContent = result.tracks.length;
  $('#metric-events').textContent = result.events.length;
  $('#metric-duration').textContent = formatDuration(result.duration_ms);
  $('#report-headline').textContent = result.report.headline;
  $('#report-summary').textContent = result.report.summary;
  $('#report-model').textContent = result.gemma.used ? result.gemma.model : 'Deterministic fallback';
  renderViewDescription(result.view_description);
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

function renderClusterResult(result) {
  $('#result-content').classList.remove('hidden');
  $('#cluster-camera-section').classList.remove('hidden');
  $('#event-section-title').textContent = 'Cross-camera associations';
  $('#track-section-title').textContent = 'Cluster identity records';
  $('#metric-observations').textContent = result.observations_processed;
  $('#metric-tracks').textContent = result.global_tracks.length;
  $('#metric-events').textContent = result.events;
  $('#metric-duration').textContent = formatDuration(result.duration_ms);
  $('#report-headline').textContent = result.report.headline;
  $('#report-summary').textContent = result.report.summary;
  $('#report-model').textContent = result.gemma.used ? result.gemma.model : 'Deterministic cluster report';
  renderViewDescription(result.view_description);
  const notes = [...(result.report.data_quality_notes || [])];
  if (result.gemma.fallback_reason) notes.push(`Gemma fallback: ${result.gemma.fallback_reason}`);
  $('#report-notes').innerHTML = notes.map((note) => `<span>${escapeHtml(note)}</span>`).join('');

  $('#event-total').textContent = `${result.associations.length} decision${result.associations.length === 1 ? '' : 's'}`;
  $('#event-list').innerHTML = result.associations.length ? result.associations.map((decision) => `
    <article class="event-row">
      <span class="event-time">${Math.round(decision.score * 100)}%</span>
      <span class="event-marker"></span>
      <div class="event-body"><strong>${escapeHtml(label(decision.state))}</strong><small>${escapeHtml(decision.explanation)}</small></div>
      <span class="event-confidence">${escapeHtml(label(decision.edge_type))}</span>
    </article>`).join('') : '<div class="empty-row">No cross-camera identities were merged. Configure an overlap group or directed topology when the views are related.</div>';

  $('#track-list').innerHTML = result.global_tracks.length ? result.global_tracks.map((track) => {
    const start = Math.min(...track.segments.map((segment) => segment.started_at_ms));
    const end = Math.max(...track.segments.map((segment) => segment.ended_at_ms));
    return `<tr><td>${shortId(track.global_id)}</td><td>person</td><td>${formatDuration(Math.max(0, end - start))}</td><td>${Math.round(track.identity_confidence * 100)}%</td><td>${escapeHtml(track.camera_ids.join(' → '))}</td></tr>`;
  }).join('') : '<tr><td colspan="5">No confirmed person tracks</td></tr>';

  const cameraCards = result.camera_results.map((camera) => {
    const view = camera.pipeline.view_description || { scene_type: 'undetermined', description: 'No view description is available for this run.' };
    return `
      <article class="camera-result-card">
        <div class="camera-result-head"><strong>${escapeHtml(camera.label)}</strong><small>${escapeHtml(camera.camera_id)}</small></div>
        <div class="camera-result-metrics"><span>${camera.pipeline.observations_processed} observations</span><span>${camera.pipeline.tracks.length} tracks</span><span>${camera.pipeline.events.length} events</span></div>
        <div class="camera-view-description"><small>VIEW · ${escapeHtml(view.scene_type)}</small><p>${escapeHtml(view.description)}</p></div>
        <p><strong>${escapeHtml(camera.pipeline.report.headline)}</strong><br>${escapeHtml(camera.pipeline.report.summary)}</p>
      </article>`;
  });
  const failureCards = result.camera_failures.map((failure) => `
    <article class="camera-result-card error">
      <div class="camera-result-head"><strong>${escapeHtml(failure.label)}</strong><small>${escapeHtml(failure.camera_id)} · failed</small></div>
      <p>${escapeHtml(failure.error)}</p>
    </article>`);
  $('#cluster-camera-total').textContent = `${result.cameras_processed}/${result.cameras_requested} cameras`;
  $('#cluster-camera-results').innerHTML = [...cameraCards, ...failureCards].join('');
  $('#raw-json').textContent = JSON.stringify(result, null, 2);
}

function renderViewDescription(view) {
  const description = view || {
    description: 'No view description is available for this older run.',
    scene_type: 'undetermined',
    visible_areas: [],
    notable_static_elements: [],
    visibility_conditions: 'Not assessed',
    confidence: 0,
    generated_by_model: false,
    fallback_reason: 'Run the pipeline again to capture a representative frame.',
  };
  $('#view-scene-type').textContent = label(description.scene_type);
  $('#view-description-text').textContent = description.description;
  $('#view-description-source').textContent = description.generated_by_model
    ? `${description.model || 'Gemma vision'} · ${Math.round(description.confidence * 100)}%`
    : 'Detector fallback';
  const details = [];
  if (description.visible_areas?.length) details.push(`Areas: ${description.visible_areas.join(', ')}`);
  if (description.notable_static_elements?.length) details.push(`Static elements: ${description.notable_static_elements.join(', ')}`);
  if (description.visibility_conditions) details.push(`Visibility: ${description.visibility_conditions}`);
  if (description.fallback_reason) details.push(`Fallback: ${description.fallback_reason}`);
  $('#view-description-details').innerHTML = details.map((detail) => `<span>${escapeHtml(detail)}</span>`).join('');
}

function renderRuns() {
  const container = $('#runs-list');
  const runs = [
    ...state.jobs.map((job) => ({ kind: 'single', job })),
    ...state.clusterJobs.map((job) => ({ kind: 'cluster', job })),
  ].sort((left, right) => right.job.created_at_ms - left.job.created_at_ms);
  if (!runs.length) {
    container.innerHTML = '<div class="empty-row">No runs yet. Start with the built-in sample.</div>';
    return;
  }
  container.innerHTML = runs.map(({ kind, job }) => `
    <article class="run-row" data-id="${job.id}" data-kind="${kind}">
      <div class="run-name"><strong>${escapeHtml(job.request.name)}</strong><small>${shortId(job.id)} · ${formatDate(job.created_at_ms)}</small></div>
      <div class="run-cell"><strong>${kind === 'cluster' ? `${job.request.cameras.length} cameras` : label(job.request.backend)}</strong><small>${kind === 'cluster' ? 'cluster' : 'backend'}</small></div>
      <div class="run-cell"><strong>${kind === 'cluster' ? (job.result?.global_tracks?.length ?? '—') : (job.result?.tracks?.length ?? '—')}</strong><small>${kind === 'cluster' ? 'global IDs' : 'tracks'}</small></div>
      <div class="run-cell"><strong>${kind === 'cluster' ? (job.result?.events ?? '—') : (job.result?.events?.length ?? '—')}</strong><small>events</small></div>
      <span class="badge ${job.status}">${job.status}</span>
    </article>`).join('');
  $$('.run-row').forEach((row) => row.addEventListener('click', () => {
    const collection = row.dataset.kind === 'cluster' ? state.clusterJobs : state.jobs;
    const job = collection.find((candidate) => candidate.id === row.dataset.id);
    if (job) {
      if (row.dataset.kind === 'cluster') inspectClusterJob(job); else inspectJob(job);
      showView('workspace');
    }
  }));
}

function renderCapabilities() {
  if (!state.capabilities) return;
  const labels = {
    service_version: 'Service version', local_state: 'State mode', simulator: 'Simulator',
    yolo26_command: 'YOLO26 adapter', multi_camera_clusters: 'Multi-camera clusters',
    max_cluster_cameras: 'Maximum cameras per cluster', stream_protocols: 'Stream protocols',
    max_analysis_secs: 'Maximum monitor duration (sec)', gemma_endpoint: 'OpenAI-compatible endpoint',
    lmstudio_api_endpoint: 'LM Studio native API',
    kafka_compiled: 'Kafka compiled', kafka_enabled: 'Kafka enabled',
  };
  $('#capability-list').innerHTML = Object.entries(labels).map(([key, title]) => `<dt>${title}</dt><dd>${escapeHtml(String(state.capabilities[key]))}</dd>`).join('');
}

function renderVlmOptions() {
  const detected = detectedModelMap();
  const ordered = [...new Set([...state.configuredVlms, ...KNOWN_VLM_MODELS])];
  ['#vlm-model', '#memory-vlm-model'].forEach((selector) => {
    const select = $(selector);
    if (!select) return;
    const current = select.value || 'google/gemma-4-26b-a4b-qat';
    select.innerHTML = ordered.map((model) => {
      const info = detected.get(model);
      const stateLabel = info?.state ? ` · ${info.state}` : info ? ' · detected' : '';
      return `<option value="${escapeHtml(model)}"${model === current ? ' selected' : ''}>${escapeHtml(model + stateLabel)}</option>`;
    }).join('');
    if (!ordered.includes(current)) {
      select.value = ordered.includes('google/gemma-4-26b-a4b-qat') ? 'google/gemma-4-26b-a4b-qat' : ordered[0];
    }
  });
}

function renderModels(error) {
  if (!state.models.length) {
    $('#models-list').innerHTML = [
      ...state.configuredVlms.map((model) => `<div class="model-row">${escapeHtml(model)} · configured</div>`),
      `<div class="empty-row">${escapeHtml(error || 'LM Studio returned no models yet.')}</div>`,
    ].join('');
    return;
  }
  const configured = new Set(state.configuredVlms);
  $('#models-list').innerHTML = state.models.map((model) => {
    const preferred = [$('#vlm-model')?.value, $('#memory-vlm-model')?.value].includes(model.id);
    const tags = [
      configured.has(model.id) ? 'configured' : 'detected',
      model.model_type,
      model.state,
    ].filter(Boolean).join(' · ');
    return `<div class="model-row ${preferred ? 'preferred' : ''}">${escapeHtml(model.id)}${tags ? ` · ${escapeHtml(tags)}` : ''}</div>`;
  }).join('');
}

function detectedModelMap() {
  return new Map(state.models.map((model) => [model.id, model]));
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
