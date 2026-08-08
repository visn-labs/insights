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
  models: [],
  configuredVlms: [...KNOWN_VLM_MODELS],
  jobs: [],
  clusterJobs: [],
  memoryJobs: [],
  sample: null,
  selectedJobId: null,
  selectedJobKind: null,
  selectedMemoryJobId: null,
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
  renderVlmOptions();
  $('#refresh-button').addEventListener('click', refreshAll);
  $('#new-run-button').addEventListener('click', () => showView('workspace'));
  await Promise.all([loadCapabilities(), loadModels(), loadSample(), refreshJobLists()]);
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
  const titles = { workspace: 'Pipeline workspace', memory: 'Retrieval-first memory', runs: 'Run history', system: 'System readiness' };
  $('#page-title').textContent = titles[name] || 'Pipeline workspace';
}

function bindMemory() {
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
  $('#memory-query-form').addEventListener('submit', submitMemoryQuery);
  $('#memory-vlm-model').addEventListener('change', () => renderModels());
  loadMemoryPreset('cluster-a');
}

function loadMemoryPreset(name) {
  const preset = MEMORY_CAMERA_PRESETS[name];
  if (!preset) return;
  $('#memory-cluster-id').value = preset.clusterId;
  $('#memory-query-cluster').value = preset.clusterId;
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
  $('#refresh-button').disabled = true;
  await Promise.all([loadCapabilities(), loadModels(), refreshJobLists()]);
  $('#refresh-button').disabled = false;
  toast('Runtime state refreshed');
}

async function loadCapabilities() {
  try {
    state.capabilities = await api('/api/v1/capabilities');
    const durationInput = $('#monitor-duration');
    durationInput.max = state.capabilities.max_analysis_secs;
    if (Number(durationInput.value) > state.capabilities.max_analysis_secs) {
      durationInput.value = state.capabilities.max_analysis_secs;
    }
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
    state.configuredVlms = response.configured_vlms?.length ? response.configured_vlms : [...KNOWN_VLM_MODELS];
    if (response.available && state.models.length) {
      $('#gemma-dot').className = 'status-dot ok';
      $('#gemma-status').textContent = 'LM Studio reachable';
    } else {
      $('#gemma-dot').className = 'status-dot error';
      $('#gemma-status').textContent = 'VLM fallback mode';
    }
    renderVlmOptions();
    renderModels(response.error);
  } catch (error) {
    state.models = [];
    $('#gemma-dot').className = 'status-dot error';
    $('#gemma-status').textContent = 'VLM fallback mode';
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
    let cameras = JSON.parse($('#memory-cameras-json').value || '[]');
    if (!Array.isArray(cameras)) cameras = [cameras];
    if (!cameras.length) throw new Error('Provide at least one backend camera payload.');
    const clusterId = $('#memory-cluster-id').value.trim();
    const request = {
      name: $('#memory-job-name').value.trim(),
      cluster_id: clusterId || null,
      cameras,
      monitor_duration_secs: Number($('#memory-duration').value),
      observer_fps: Number($('#memory-observer-fps').value),
      vlm_enabled: $('#memory-vlm-enabled').checked,
      vlm_model: $('#vlm-model').value,
    };
    const job = await api('/api/v1/memory-jobs', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(request),
    });
    state.selectedMemoryJobId = job.id;
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
    $('#memory-count').textContent = state.memoryJobs.length;
    if (state.selectedMemoryJobId) {
      const selected = state.memoryJobs.find((job) => job.id === state.selectedMemoryJobId);
      if (selected) inspectMemoryJob(selected);
    } else if (state.memoryJobs.length) {
      state.selectedMemoryJobId = state.memoryJobs[0].id;
      inspectMemoryJob(state.memoryJobs[0]);
    }
  } catch (error) {
    console.warn('Could not refresh memory jobs', error);
  }
}

function inspectMemoryJob(job) {
  state.selectedMemoryJobId = job.id;
  $('#memory-empty').classList.add('hidden');
  const badge = $('#memory-job-status');
  badge.textContent = job.status;
  badge.className = `badge ${job.status}`;
  $('#memory-processing').classList.toggle('hidden', !['queued', 'running'].includes(job.status));
  $('#memory-job-error').classList.toggle('hidden', job.status !== 'failed');
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
  $('#memory-camera-results').innerHTML = result.camera_results.map(renderMemoryCamera).join('') +
    (result.camera_failures || []).map((failure) => `<article class="camera-result-card error"><div class="camera-result-head"><strong>${escapeHtml(failure.camera_id)}</strong><small>failed</small></div><p>${escapeHtml(failure.error)}</p></article>`).join('');
  $('#memory-raw-json').textContent = JSON.stringify(result, null, 2);
}

function renderMemoryCamera(cameraResult) {
  const camera = cameraResult.camera;
  const location = [camera.city, camera.region, camera.country].filter(Boolean).join(', ');
  const events = cameraResult.events || [];
  return `<section class="memory-camera-block">
    <div class="memory-camera-heading">
      <div><strong>${escapeHtml(camera.camera_id)}</strong><small>${escapeHtml(location || camera.manufacturer || 'Metadata pending')}</small></div>
      <span>${events.length} event${events.length === 1 ? '' : 's'} · ${cameraResult.frames_decoded} sparse frames</span>
    </div>
    ${camera.description ? `<p class="memory-camera-description">${escapeHtml(camera.description)}</p>` : ''}
    <div class="memory-event-grid">${events.map(renderMemoryEvent).join('')}</div>
    ${(cameraResult.data_quality_notes || []).length ? `<div class="report-notes">${cameraResult.data_quality_notes.map((note) => `<span>${escapeHtml(note)}</span>`).join('')}</div>` : ''}
  </section>`;
}

function renderMemoryEvent(event, match = null) {
  const description = event.description || {};
  const tags = [...(description.visible_objects || []), ...(description.apparent_actions || []), ...(description.visible_text || [])].slice(0, 6);
  const score = match ? `<span class="memory-score">score ${Number(match.score).toFixed(3)}</span>` : '';
  return `<article class="memory-event-card">
    <img src="${event.thumbnail_url}" alt="Representative evidence frame from ${escapeHtml(event.camera_id)}" loading="lazy">
    <div class="memory-event-body">
      <div class="memory-event-meta"><span>${formatDuration(event.start_ms)}–${formatDuration(event.end_ms)}</span><span>activity ${Number(event.activity_peak).toFixed(2)}</span>${score}</div>
      <strong>${escapeHtml(description.scene_type || 'Indexed interval')}</strong>
      <p>${escapeHtml(description.summary || 'Evidence interval indexed without a visual description.')}</p>
      ${tags.length ? `<div class="memory-tags">${tags.map((tag) => `<span>${escapeHtml(tag)}</span>`).join('')}</div>` : ''}
      ${match?.matched_terms?.length ? `<small class="matched-terms">Matched: ${escapeHtml(match.matched_terms.join(', '))}</small>` : ''}
      <video controls preload="none" poster="${event.thumbnail_url}" src="${event.evidence_url}"></video>
    </div>
  </article>`;
}

async function submitMemoryQuery(event) {
  event.preventDefault();
  setInlineError('#memory-query-error', '');
  const button = $('#memory-query-button');
  button.disabled = true;
  button.querySelector('span').textContent = 'Searching…';
  try {
    const clusterId = $('#memory-query-cluster').value.trim();
    const response = await api('/api/v1/memory-query', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        query: $('#memory-query-text').value.trim(),
        cluster_id: clusterId || null,
        camera_ids: [],
        limit: 12,
        vlm_enabled: $('#memory-query-vlm').checked,
        vlm_model: $('#memory-vlm-model').value,
      }),
    });
    renderMemoryQuery(response);
  } catch (error) {
    setInlineError('#memory-query-error', error.message);
  } finally {
    button.disabled = false;
    button.querySelector('span').textContent = 'Search memory';
  }
}

function renderMemoryQuery(result) {
  $('#memory-query-result').classList.remove('hidden');
  $('#memory-query-title').textContent = result.matches.length ? `${result.matches.length} evidence candidates` : 'No matching evidence';
  $('#memory-query-summary').textContent = result.summary;
  $('#memory-query-mode').textContent = result.model || label(result.retrieval_mode);
  const notes = [`${result.events_considered} indexed events considered`];
  if (result.fallback_reason) notes.push(`VLM fallback: ${result.fallback_reason}`);
  $('#memory-query-notes').innerHTML = notes.map((note) => `<span>${escapeHtml(note)}</span>`).join('');
  $('#memory-query-matches').innerHTML = result.matches.length
    ? result.matches.map((match) => renderMemoryEvent(match.event, match)).join('')
    : '<div class="empty-row">No events matched the current query and filters.</div>';
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
