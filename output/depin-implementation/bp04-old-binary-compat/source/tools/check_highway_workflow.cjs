// Real browser + real models + a bounded recording. Requires explicit STREAM_URL.
const { chromium } = require('playwright');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const base = process.env.VISN_TEST_URL || 'http://127.0.0.1:8197';
const stream = process.env.STREAM_URL;
if (!stream && !process.env.REUSE_JOB_ID) throw Error('Set STREAM_URL to a camera you want to record for 30 seconds.');
const output = 'output/playwright/highway';
fs.mkdirSync(output, { recursive: true });
const log = message => console.log(`${new Date().toISOString()} ${message}`);
(async () => {
  const browser = await chromium.launch({executablePath: process.env.CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless: true});
  try {
    const page = await browser.newPage({viewport: {width:1440, height:1000}});
    const errors = []; page.on('pageerror', e => errors.push(e.message));
    await page.goto(`${base}/#/workspace`);
    await page.locator('#memory-view.active').waitFor();
    await page.waitForFunction(() => document.querySelector('#retrieval-status').dataset.available === 'true');
    let jobId = process.env.REUSE_JOB_ID;
    if (jobId) {
      await page.locator('#memory-job-select').selectOption(jobId);
    } else {
    await page.locator('#memory-stream-url').fill(stream);
    await page.locator('#memory-cluster-id').fill(`highway-${Date.now()}`);
    await page.locator('#memory-job-name').fill('Highway signal · experiment 1 validation');
    await page.locator('#memory-duration').fill('30');
    await page.locator('#memory-vlm-model').selectOption('moondream2');
    assert.equal(await page.locator('#memory-vlm-enabled').isChecked(), false);
    const submit = page.waitForResponse(r => r.url().endsWith('/api/v1/memory-jobs') && r.request().method() === 'POST');
    await page.locator('#memory-run-button').click();
    const submitted = await submit; assert.equal(submitted.status(), 202);
    assert.equal(submitted.request().postDataJSON().vlm_model, 'moondream2');
    jobId = (await submitted.json()).id;
    }
    log(`${process.env.REUSE_JOB_ID ? 'Using persisted recording' : 'Recording started'}: ${jobId}`);
    let job;
    for (let i=0; i<120; i++) {
      job=await (await page.request.get(`${base}/api/v1/memory-jobs/${jobId}`)).json();
      if (['completed','failed'].includes(job.status)) break;
      await new Promise(r=>setTimeout(r,1000));
    }
    fs.writeFileSync(`${output}/job-${jobId}.json`,JSON.stringify(job,null,2));
    assert.equal(job.status,'completed',job.error);
    assert.ok(job.result.events_indexed>0);
    assert.ok(job.result.source_duration_ms>=25000 && job.result.source_duration_ms<=32000, 'Recorded interval must match wall-clock duration');
    log(`Indexed ${job.result.events_indexed} intervals over ${job.result.source_duration_ms}ms`);
    await page.locator('#memory-job-status.completed').waitFor();
    if (!process.env.REUSE_JOB_ID) assert.equal(await page.locator('#memory-query-cluster').inputValue(),job.request.cluster_id);
    // Indexed cards must have no bogus NaN scores and must survive polling during playback.
    assert.ok(!(await page.locator('#memory-camera-results').innerText()).includes('NaN'));
    const video=page.locator('#memory-camera-results video').first();
    await video.evaluate(async v => {v.muted=true;await v.play();});
    const before=await video.evaluate(v=>{v.dataset.playbackCheck='retained';return v.currentTime;});
    await new Promise(r=>setTimeout(r,3500));
    assert.equal(await video.getAttribute('data-playback-check'),'retained');
    assert.ok(await video.evaluate(v=>v.currentTime)>before+1);
    await video.evaluate(v=>v.pause());
    log('Browser evidence playback survives polling');
    await page.locator('#search-indexed-memory').click();
    assert.equal(await page.locator('#memory-query-cameras').inputValue(),job.request.cameras.map(camera=>camera.camera_id).join(', '));
    await page.locator('#memory-query-text').fill('Vehicles and trucks on a road at an intersection');
    const search = async () => {
      const pending=page.waitForResponse(r=>r.url().endsWith('/api/v1/memory-query')&&r.request().method()==='POST',{timeout:600000});
      await page.locator('#memory-query-button').click();
      const reply=await pending;assert.equal(reply.status(),200);return reply.json();
    };
    log('Searching with real Qwen embeddings and reranking');
    const result=await search();
    fs.writeFileSync(`${output}/query.json`,JSON.stringify(result,null,2));
    assert.equal(result.retrieval_mode,'hybrid_qwen_reranker');
    assert.ok(result.matches.length>0);
    assert.ok(result.matches.every(m=>m.event.job_id===jobId));
    assert.ok(result.retrieval_provenance.reranker_snapshot);
    log(`Visual search complete: ${result.matches.length} candidates; ${result.retrieval_notes.join(' ')}`);
    const cached=await search();assert.ok(cached.retrieval_notes.some(n=>n.includes(`${job.result.events_indexed} cached embeddings`)));
    fs.writeFileSync(`${output}/cached-query.json`,JSON.stringify(cached,null,2));
    const scoped=await (await page.request.post(`${base}/api/v1/memory-query`,{data:{query:'vehicles',cluster_id:job.request.cluster_id,camera_ids:['nonexistent'],semantic_enabled:true}})).json();
    assert.equal(scoped.events_considered,0);assert.equal(scoped.matches.length,0);
    await page.locator('#memory-query-result').scrollIntoViewIfNeeded();
    await page.screenshot({path:`${output}/desktop.png`});
    for (const width of [320,768,1024,1440]) {
      await page.setViewportSize({width,height:900});
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`overflow at ${width}`);
    }
    await page.setViewportSize({width:390,height:844});
    await page.locator('#memory-query-form').scrollIntoViewIfNeeded();
    await page.screenshot({path:`${output}/mobile.png`});
    assert.deepEqual(errors,[]);
    log(`PASS: ${process.env.REUSE_JOB_ID ? 'persisted recording recovery' : 'recording, model selector, indexing'}, playback, filters, retrieval, reranking, cache, responsive UI`);
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
