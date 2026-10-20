import {motion,settle} from './motion.js';
import {SAMPLES} from './sample-data.js';
const $=selector=>document.querySelector(selector),storageKey='visn.contributions.v1';
const form=$('#contribution-form'),video=$('#contribution-preview'),error=$('#contribution-error');
const fields=[...form.querySelectorAll('[data-onboarding-step]')];
let step=0,clip=null,objectURL=null,saving=false,savedNow=null,editingId=null,pendingSelection=null,mediaToken=0,sampleKey='shop',exampleChoice=false,newAfterSave=false,deleteId=null;
const hints=[['Start with a place you know.','A name and a little context. No exact address is needed.'],['Choose just one recording.','Your own file stays on your device. The synthetic sample is here if you want to explore first.'],['Keep the moment that matters.','Use either the handles or exact times. Play the interval before continuing.'],['Your choice, with the details in view.','Only metadata is saved. Submission, anonymisation and rewards are not active.']];
function fail(message,field){error.textContent=message;error.hidden=false;if(field){field.setAttribute('aria-invalid','true');field.focus();}else error.focus();}
function clearError(){error.hidden=true;error.textContent='';form.querySelectorAll('[aria-invalid]').forEach(field=>field.removeAttribute('aria-invalid'));}
function readDrafts(){
  const raw=localStorage.getItem(storageKey);if(!raw)return [];
  const value=JSON.parse(raw);
  if(!Array.isArray(value)||value.some(d=>!d||typeof d.id!=='string'||typeof d.camera!=='string'||typeof d.filename!=='string'||!Number.isFinite(d.start)||!Number.isFinite(d.end)))throw Error('Saved draft data could not be read. Existing data has not been changed.');
  return value;
}
function fillList(element,rows){element.replaceChildren();for(const [label,value] of rows){const row=document.createElement('div'),dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=label;dd.textContent=value;row.append(dt,dd);element.append(row);}}
function reviewRows(){return [['Camera',$('#contribution-camera').value.trim()],['Setting',$('#contribution-context').value],['Area',$('#contribution-area').value.trim()||'Not provided'],['Recording',clip?.name||'Not selected'],['Interval',`${Number($('#segment-start').value).toFixed(1)} to ${Number($('#segment-end').value).toFixed(1)} seconds`],['Permission','Confirmed by you, not independently verified'],['Status',clip?.example?'Synthetic sample draft':'Local draft, not uploaded'],['Protection','No redaction performed'],['Rewards','Not active']];}
function showStep(next,focus=true){
  video.pause();step=next;clearError();fields.forEach((field,i)=>{field.hidden=i!==step;field.disabled=i!==step;});
  document.querySelectorAll('[data-step-label]').forEach((item,i)=>{if(i===step)item.setAttribute('aria-current','step');else item.removeAttribute('aria-current');item.classList.toggle('complete',i<step);item.querySelector('button').disabled=i>step;});
  $('#step-counter').textContent=`0${step+1} / 04`;$('#onboarding-meter').value=step+1;
  $('#onboarding-hint-title').textContent=hints[step][0];$('#onboarding-hint-copy').textContent=hints[step][1];
  $('#contribution-back').hidden=step===0;$('#contribution-next').textContent=step===3?(editingId?'Save changes ↗':'Save my draft ↗'):'Continue →';
  if(step===3)fillList($('#contribution-review'),reviewRows());
  settle(fields[step]);
  if(focus){const heading=fields[step].querySelector('legend');heading.tabIndex=-1;heading.focus({preventScroll:true});$('.onboarding-form-wrap').scrollIntoView({block:'nearest',behavior:'instant'});}
}
document.querySelectorAll('[data-step-label]').forEach((item,i)=>{const label=item.querySelector('div'),button=document.createElement('button');button.type='button';button.className='step-jump';button.innerHTML=label.innerHTML;button.addEventListener('click',()=>showStep(i));label.replaceWith(button);});
function releaseMedia(){mediaToken++;video.pause();video.removeAttribute('src');video.load();if(objectURL)URL.revokeObjectURL(objectURL);objectURL=null;clip=null;video.hidden=true;}
function resetDraft(){
  form.reset();releaseMedia();editingId=null;pendingSelection=null;exampleChoice=false;sampleKey='shop';
  $('#contribution-file-status').textContent='No recording selected.';$('#example-note').hidden=true;$('#example-preview').hidden=true;$('#reselect-notice').hidden=true;$('#example-permission-note').hidden=true;
  $('#start-example').setAttribute('aria-pressed','false');$('#use-own-recording').setAttribute('aria-pressed','true');showStep(0,false);
}
function updateSegment(){
  if(!clip)return;
  const start=Number($('#segment-start').value),end=Number($('#segment-end').value);
  for(const name of ['start','end']){const range=$('#segment-'+name+'-range');range.max=Math.floor(clip.duration*10)/10;range.value=$('#segment-'+name).value;}
  const valid=Number.isFinite(start)&&Number.isFinite(end)&&start>=0&&end>start&&end<=clip.duration+.001;
  $('#segment-duration').textContent=valid?`${(end-start).toFixed(1)} seconds selected`:'Adjust the interval';
  $('#segment-track').style.setProperty('--segment-start',`${Math.max(0,Math.min(100,start/clip.duration*100))}%`);$('#segment-track').style.setProperty('--segment-end',`${Math.max(0,Math.min(100,end/clip.duration*100))}%`);
}
function selectClip(value){
  clip=value;let start=0,end=Math.floor(Math.min(30,value.duration)*10)/10;
  if(pendingSelection&&pendingSelection.filename===value.name&&pendingSelection.end<=value.duration+.001){start=pendingSelection.start;end=pendingSelection.end;}
  else if(pendingSelection){$('#reselect-notice').hidden=false;$('#reselect-notice').textContent='The recording or duration changed. Review the new interval before saving.';}
  pendingSelection=null;
  $('#segment-start').value=start;$('#segment-end').value=end;$('#segment-start').max=value.duration;$('#segment-end').max=value.duration;
  $('#contribution-reviewed').checked=false;$('#contribution-draft-consent').checked=false;
  $('#contribution-file-status').textContent=`${value.name} · ${value.duration.toFixed(1)} seconds${value.example?' · synthetic sample':` · ${(value.bytes/1048576).toFixed(1)} MB`}`;
  $('#example-note').hidden=!value.example;$('#example-preview').hidden=!value.example;video.hidden=false;clearError();updateSegment();
}
function loadMedia(source,value){
  video.pause();clip=null;const token=++mediaToken;
  $('#contribution-file-status').textContent=value.example?'Loading the synthetic sample…':'Reading your recording on this device…';
  video.onloadedmetadata=()=>{
    const finish=()=>{if(token!==mediaToken)return;if(Number.isFinite(video.duration)&&video.duration>.1){video.currentTime=0;selectClip({...value,duration:video.duration});}else fail('This recording has no readable duration. Choose another file.',$('#contribution-file'));};
    if(token!==mediaToken)return;
    if(video.duration===Infinity){video.addEventListener('seeked',finish,{once:true});video.currentTime=1e10;}else finish();
  };
  video.onerror=()=>{if(token!==mediaToken)return;clip=null;$('#contribution-file-status').textContent='This recording could not be previewed.';fail('This browser cannot preview that recording. Try a supported MP4 or WebM. Nothing was uploaded.',$('#contribution-file'));};
  video.src=source;video.load();
}
function chooseExample(){
  exampleChoice=true;$('#start-example').setAttribute('aria-pressed','true');$('#use-own-recording').setAttribute('aria-pressed','false');$('#example-permission-note').hidden=false;
  const sample=SAMPLES[sampleKey];$('#contribution-camera').value=sample.camera;$('#contribution-context').value=sample.context;
  if(step===0)$('#contribution-authority').checked=false;$('#contribution-file').value='';
  if(objectURL)URL.revokeObjectURL(objectURL);objectURL=null;
  loadMedia(sample.url,{name:sample.camera+' · synthetic sample',example:true,sampleKey,bytes:0});
}
$('#start-example').addEventListener('click',chooseExample);$('#use-example').addEventListener('click',chooseExample);
$('#use-own-recording').addEventListener('click',()=>{
  const wasExample=exampleChoice;exampleChoice=false;$('#start-example').setAttribute('aria-pressed','false');$('#use-own-recording').setAttribute('aria-pressed','true');$('#example-permission-note').hidden=true;
  if(wasExample){releaseMedia();$('#contribution-camera').value='';$('#contribution-context').value='';$('#contribution-authority').checked=false;$('#example-note').hidden=true;$('#example-preview').hidden=true;$('#contribution-file-status').textContent='No recording selected.';}
});
$('#contribution-file').addEventListener('change',event=>{
  const file=event.target.files[0];if(!file)return;if(exampleChoice)$('#contribution-authority').checked=false;releaseMedia();exampleChoice=false;$('#start-example').setAttribute('aria-pressed','false');$('#use-own-recording').setAttribute('aria-pressed','true');$('#example-permission-note').hidden=true;clearError();$('#example-note').hidden=true;$('#example-preview').hidden=true;
  if(!/\.(mp4|webm|mov)$/i.test(file.name)||!file.size||file.size>500*1024*1024){$('#contribution-file-status').textContent='No usable recording selected.';fail('Choose a non-empty MP4, WebM or MOV up to 500 MB.',$('#contribution-file'));return;}
  objectURL=URL.createObjectURL(file);loadMedia(objectURL,{name:file.name,bytes:file.size,example:false});
});
function validSegment(){
  const start=Number($('#segment-start').value),end=Number($('#segment-end').value);
  if(!clip||!$('#segment-start').value.trim()||!$('#segment-end').value.trim()||!Number.isFinite(start)||!Number.isFinite(end)||start<0||end<=start||end>clip.duration+.001){fail('Choose an end after the start, within the recording’s duration.',$('#segment-end'));return false;}return true;
}
$('#preview-segment').addEventListener('click',async()=>{if(!validSegment())return;video.currentTime=Number($('#segment-start').value);try{await video.play();}catch{fail('Playback could not start. Try the video controls.');}});
video.addEventListener('timeupdate',()=>{if(!video.paused&&video.currentTime>=Number($('#segment-end').value))video.pause();});
for(const name of ['start','end']){
  $('#segment-'+name).addEventListener('input',()=>{clearError();$('#contribution-reviewed').checked=false;video.pause();updateSegment();});
  $('#segment-'+name+'-range').addEventListener('input',event=>{
    const other=Number($('#segment-'+(name==='start'?'end':'start')).value);const value=name==='start'?Math.min(Number(event.target.value),other-.1):Math.max(Number(event.target.value),other+.1);
    $('#segment-'+name).value=Math.max(0,Math.min(Math.floor(clip.duration*10)/10,value)).toFixed(1);$('#contribution-reviewed').checked=false;video.pause();clearError();updateSegment();
  });
}
$('#contribution-back').addEventListener('click',()=>showStep(Math.max(0,step-1)));
form.addEventListener('submit',event=>{
  event.preventDefault();if(saving)return;clearError();
  if(step===0){$('#contribution-camera').value=$('#contribution-camera').value.trim();if(!form.reportValidity())return;}
  if(step===1&&!clip){fail('Choose a readable recording, or wait for the synthetic sample to load.',$('#contribution-file'));return;}
  if(step===2&&(!validSegment()||!form.reportValidity()))return;
  if(step<3){showStep(step+1);return;}
  if(!form.reportValidity())return;
  if(!$('#contribution-authority').checked){showStep(0);fail('Confirm permission before saving.',$('#contribution-authority'));return;}
  if(!clip||!$('#contribution-reviewed').checked){showStep(2);fail('Reselect and review the recording interval before saving.');return;}
  if(!validSegment())return;
  saving=true;let committed=false;$('#contribution-next').disabled=true;
  try{
    const drafts=readDrafts(),existing=editingId?drafts.find(d=>d.id===editingId):null;
    if(editingId&&!existing)throw Error('This draft no longer exists. Your other drafts have not been changed.');
    if(!editingId&&drafts.length>=100)throw Error('This browser already has 100 drafts. Delete a draft before creating another.');
    const draft={id:existing?.id||crypto.randomUUID(),camera:$('#contribution-camera').value.trim(),context:$('#contribution-context').value,area:$('#contribution-area').value.trim(),filename:clip.name,bytes:clip.bytes,duration:clip.duration,start:Number($('#segment-start').value),end:Number($('#segment-end').value),example:clip.example,sampleKey:clip.sampleKey,created:existing?.created||new Date().toISOString(),updated:new Date().toISOString(),status:'draft'};
    const next=existing?drafts.map(d=>d.id===draft.id?draft:d):[draft,...drafts];
    localStorage.setItem(storageKey,JSON.stringify(next));committed=true;savedNow=draft.id;newAfterSave=true;
    window.dispatchEvent(new CustomEvent('visn:navigate',{detail:{path:'/home',transition:true}}));
  }catch(e){fail(e.message?.includes('draft')?e.message:'This browser could not save the draft. Free some space or enable local storage and try again. Nothing was uploaded.');}
  finally{if(!committed){saving=false;$('#contribution-next').disabled=false;}}
});
function notice(message){const node=$('#contribution-saved');node.hidden=false;node.textContent=message;}
function renderHome(){
  let drafts=[];const saved=$('#contribution-saved');saved.hidden=!savedNow;if(savedNow)saved.textContent='Draft saved on this device. No video was uploaded or redacted, and no earnings were created.';
  try{drafts=readDrafts();}catch(e){notice(e.message||'Saved drafts are unavailable. Existing data has not been changed.');}
  $('#contributor-draft-count').textContent=drafts.length;$('#contribution-empty').hidden=drafts.length>0;
  const list=$('#contribution-list');list.replaceChildren();
  for(const [i,draft] of drafts.entries()){
    const row=document.createElement('article');row.className='contribution-row';row.dataset.draftId=draft.id;
    const mark=document.createElement('span');mark.className='row-mark';mark.textContent=String(i+1).padStart(2,'0');mark.setAttribute('aria-hidden','true');
    const copy=document.createElement('div');copy.className='draft-copy';if(draft.id===savedNow)copy.classList.add('receipt-transition');
    const title=document.createElement('h3'),sub=document.createElement('p'),interval=document.createElement('p');title.textContent=draft.camera;sub.textContent=`${draft.context||'Setting not provided'} · ${draft.area||'Area not provided'}`;interval.textContent=`${draft.start.toFixed(1)} to ${draft.end.toFixed(1)} seconds · ${draft.filename}`;
    const badge=document.createElement('span');badge.className='draft-status';badge.textContent=draft.example?'SYNTHETIC SAMPLE DRAFT':'LOCAL DRAFT · NOT UPLOADED';copy.append(title,sub,interval,badge);
    const actions=document.createElement('div');actions.className='draft-actions';
    for(const action of ['Edit','Delete']){const button=document.createElement('button');button.type='button';button.textContent=action;button.setAttribute('aria-label',`${action} ${draft.camera} draft`);button.addEventListener('click',()=>{
      if(action==='Edit')location.hash='/contribute?draft='+encodeURIComponent(draft.id);
      else{deleteId=draft.id;$('#delete-copy').textContent=`Only the draft details for “${draft.camera}” will be removed from this browser.`;$('#delete-dialog').showModal();$('#delete-cancel').focus();}
    });actions.append(button);}
    const details=document.createElement('details');details.className='draft-details';const summary=document.createElement('summary');summary.textContent='View draft details';const dl=document.createElement('dl');dl.className='review-list';
    fillList(dl,[['Recording',draft.filename],['Interval',`${draft.start.toFixed(1)} to ${draft.end.toFixed(1)} seconds`],['Saved',new Date(draft.updated||draft.created).toLocaleDateString()],['Next step',draft.example?'A synthetic example, not a submitted recording.':'Reselect the original recording when editing. The video was not stored.'],['Protection','No upload or redaction performed'],['Rewards','Not active']]);details.append(summary,dl);row.append(mark,copy,actions,details);list.append(row);
  }
}
$('#delete-cancel').addEventListener('click',()=>$('#delete-dialog').close());
$('#delete-confirm').addEventListener('click',()=>{
  try{const drafts=readDrafts();localStorage.setItem(storageKey,JSON.stringify(drafts.filter(d=>d.id!==deleteId)));savedNow=null;$('#delete-dialog').close();renderHome();notice('Local draft details deleted. Your original recording is unchanged.');$('#contributor-home-title').focus();}
  catch{ $('#delete-dialog').close();notice('The draft could not be deleted. Existing data has not been changed.');}
});
function editDraft(id){
  resetDraft();editingId=id;
  try{
    const draft=readDrafts().find(d=>d.id===id);if(!draft)throw Error('This draft no longer exists. Return to My drafts.');
    $('#contribution-camera').value=draft.camera;$('#contribution-context').value=draft.context||'';$('#contribution-area').value=draft.area||'';pendingSelection=draft;
    $('#reselect-notice').hidden=false;$('#reselect-notice').textContent=`Editing ${draft.camera}. Reselect “${draft.filename}” to preview or change its interval. No video was stored.`;
    if(draft.example){sampleKey=SAMPLES[draft.sampleKey]?draft.sampleKey:'shop';chooseExample();$('#contribution-camera').value=draft.camera;}
  }catch(e){return e.message||'The draft could not be opened. Existing data is unchanged.';}
}
let renderedEdit=null;
export function renderContributor(page,params=new URLSearchParams()){
  $('#contributor-shell').dataset.page=page;
  for(const [id,name] of [['onboarding-page','contribute'],['contributor-home','home'],['rewards-page','rewards'],['not-found-page','not-found']])$('#'+id).hidden=page!==name;
  document.querySelectorAll('.contributor-header nav a').forEach(link=>{if(link.hash==='#/'+page)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');});
  if(page==='contribute'){
    saving=false;$('#contribution-next').disabled=false;
    const id=params.get('draft');let editError;
    if(id&&(id!==renderedEdit||newAfterSave)){editError=editDraft(id);renderedEdit=id;newAfterSave=false;}
    else if(!id&&(newAfterSave||renderedEdit)){resetDraft();renderedEdit=null;newAfterSave=false;}
    if(!id&&params.has('sample')&&!exampleChoice){sampleKey=SAMPLES[params.get('view')]?params.get('view'):'shop';const start=Number(params.get('start')),end=Number(params.get('end'));if(start>=0&&end>start&&end<=12)pendingSelection={filename:SAMPLES[sampleKey].camera+' · synthetic sample',start,end};chooseExample();}
    showStep(step,false);if(editError)fail(editError);
  }else{video.pause();if(page==='home')renderHome();}
}
window.addEventListener('storage',event=>{if(event.key===storageKey&&!$('#contributor-home').hidden){savedNow=null;renderHome();}});
window.addEventListener('pagehide',()=>video.pause());
showStep(0,false);
