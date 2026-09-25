const $=selector=>document.querySelector(selector);
const key='visn.contributions.v1';
let step=0,clip=null,objectURL=null,saving=false,savedNow=false;
const form=$('#contribution-form');
const fields=[...form.querySelectorAll('[data-onboarding-step]')];
const video=$('#contribution-preview');
const error=$('#contribution-error');
function fail(message){error.textContent=message;error.hidden=false;}
function clearError(){error.hidden=true;error.textContent='';}
function readDrafts(){
  const raw=localStorage.getItem(key);
  if(!raw)return [];
  const value=JSON.parse(raw);
  if(!Array.isArray(value)||value.some(d=>!d||typeof d.id!=='string'||typeof d.camera!=='string'||typeof d.filename!=='string'))throw Error('Saved draft data could not be read. Your existing data has not been changed.');
  return value;
}
function reviewRows(){return [
  ['Camera',$('#contribution-camera').value.trim()],['Setting',$('#contribution-context').value],
  ['Area',$('#contribution-area').value.trim()||'Not provided'],['Recording',clip?.name||'No clip'],
  ['Segment',`${Number($('#segment-start').value).toFixed(1)}–${Number($('#segment-end').value).toFixed(1)} seconds`],
  ['Permission','You confirmed ownership or permission'],['Status',clip?.example?'Example draft · no footage':'Local draft · not uploaded'],['Rewards','Not active in this preview']
];}
function fillList(element,rows){
  element.replaceChildren();
  for(const [label,value] of rows){const row=document.createElement('div'),dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=label;dd.textContent=value;row.append(dt,dd);element.append(row);}
}
function showStep(next,focus=true){
  video.pause();step=next;clearError();
  fields.forEach((field,i)=>{field.hidden=i!==step;field.disabled=i!==step;});
  document.querySelectorAll('[data-step-label]').forEach((item,i)=>{if(i===step)item.setAttribute('aria-current','step');else item.removeAttribute('aria-current');item.classList.toggle('complete',i<step);});
  $('#step-counter').textContent=`0${step+1} / 04`;
  $('#contribution-back').hidden=step===0;
  $('#contribution-next').textContent=step===3?'Save my draft ↗':'Continue →';
  if(step===3)fillList($('#contribution-review'),reviewRows());
  if(focus){const heading=fields[step].querySelector('legend');heading.tabIndex=-1;heading.focus({preventScroll:true});$('.onboarding-form-wrap').scrollIntoView({block:'nearest',behavior:'auto'});}
}
function resetDraft(){
  form.reset();clip=null;step=0;
  if(objectURL)URL.revokeObjectURL(objectURL);objectURL=null;video.removeAttribute('src');video.hidden=true;
  $('#contribution-file-status').textContent='No clip selected.';$('#example-note').hidden=true;$('#example-preview').hidden=true;showStep(0,false);
}
function selectClip(value){
  clip=value;$('#segment-start').value='0';$('#segment-end').value=(Math.floor(Math.min(30,value.duration)*10)/10).toFixed(1);
  $('#segment-start').max=value.duration;$('#segment-end').max=value.duration;
  $('#contribution-reviewed').checked=false;$('#contribution-draft-consent').checked=false;
  $('#contribution-file-status').textContent=value.example?'Example recording · 30 seconds':`${value.name} · ${(value.bytes/1048576).toFixed(1)} MB · ${value.duration.toFixed(1)} seconds`;
  $('#example-note').hidden=!value.example;$('#example-preview').hidden=!value.example;video.hidden=value.example;
  $('#preview-segment').hidden=value.example;clearError();
}
$('#use-example').addEventListener('click',()=>{
  video.pause();if(objectURL)URL.revokeObjectURL(objectURL);objectURL=null;video.removeAttribute('src');$('#contribution-file').value='';
  selectClip({name:'Shop entrance · example',duration:30,bytes:0,example:true});
});
$('#contribution-file').addEventListener('change',event=>{
  const file=event.target.files[0];if(!file)return;
  clip=null;$('#example-note').hidden=true;$('#example-preview').hidden=true;video.hidden=true;video.pause();if(objectURL)URL.revokeObjectURL(objectURL);objectURL=null;clearError();
  if(!/\.(mp4|webm|mov)$/i.test(file.name)||file.size>500*1024*1024||file.size===0){video.removeAttribute('src');video.hidden=true;$('#contribution-file-status').textContent='No usable clip selected.';fail('Choose a non-empty MP4, WebM or MOV file up to 500 MB.');return;}
  $('#contribution-file-status').textContent='Reading the recording on your device…';
  objectURL=URL.createObjectURL(file);const pendingURL=objectURL;video.src=pendingURL;
  const finishMetadata=()=>{if(video.src!==pendingURL)return;if(Number.isFinite(video.duration)&&video.duration>.1){video.currentTime=0;selectClip({name:file.name,bytes:file.size,duration:video.duration,example:false});}else {clip=null;fail('This recording has no readable duration. Please choose another clip.');}};
  video.onloadedmetadata=()=>{
    if(video.src!==pendingURL)return;
    // Browser-recorded WebM may omit its duration; seeking to the end resolves it.
    if(video.duration===Infinity){video.addEventListener('seeked',finishMetadata,{once:true});video.currentTime=1e10;}
    else finishMetadata();
  };
  video.onerror=()=>{if(video.src!==pendingURL)return;clip=null;fail('This browser cannot preview that recording. Please try an MP4 or WebM with a supported codec.');};
});
function validSegment(){
  const start=Number($('#segment-start').value),end=Number($('#segment-end').value);
  if(!clip||!Number.isFinite(start)||!Number.isFinite(end)||start<0||end<=start||end>clip.duration+.001){fail('Choose an end time after the start, within the recording’s duration.');return false;}
  return true;
}
$('#preview-segment').addEventListener('click',async()=>{if(!validSegment()||!objectURL)return;video.currentTime=Number($('#segment-start').value);try{await video.play();}catch{fail('Playback could not start. Use the video controls to try again.');}});
video.addEventListener('timeupdate',()=>{if(!video.paused&&video.currentTime>=Number($('#segment-end').value))video.pause();});
for(const id of ['segment-start','segment-end'])$('#'+id).addEventListener('input',()=>{$('#contribution-reviewed').checked=false;});
$('#contribution-back').addEventListener('click',()=>showStep(Math.max(0,step-1)));
form.addEventListener('submit',event=>{
  event.preventDefault();if(saving)return;clearError();
  if(step===0){$('#contribution-camera').value=$('#contribution-camera').value.trim();if(!form.reportValidity())return;}
  if(step===1&&!clip){fail('Choose a recording, or use the labelled example to explore the flow.');return;}
  if(step===2&&(!validSegment()||!form.reportValidity()))return;
  if(step<3){showStep(step+1);return;}
  if(!form.reportValidity())return;
  saving=true;$('#contribution-next').disabled=true;
  try{
    const drafts=readDrafts();if(drafts.length>=100)throw Error('This browser already has 100 drafts. No existing drafts have been changed.');
    const draft={id:crypto.randomUUID(),camera:$('#contribution-camera').value.trim(),context:$('#contribution-context').value,area:$('#contribution-area').value.trim(),filename:clip.name,bytes:clip.bytes,duration:clip.duration,start:Number($('#segment-start').value),end:Number($('#segment-end').value),example:clip.example,created:new Date().toISOString(),status:'draft'};
    localStorage.setItem(key,JSON.stringify([draft,...drafts]));savedNow=true;resetDraft();location.hash='/home';
  }catch(e){fail(e.message?.includes('draft')?e.message:'Your browser could not save the draft. Enable local storage or free some space, then try again. Nothing was uploaded.');}
  finally{saving=false;$('#contribution-next').disabled=false;}
});
function renderHome(){
  let drafts=[];const notice=$('#contribution-saved');notice.hidden=!savedNow;
  notice.textContent='Your draft is saved on this device. Your video has not been uploaded.';
  try{drafts=readDrafts();}catch(e){notice.hidden=false;notice.textContent=e.message||'Saved drafts are unavailable in this browser.';}
  $('#contributor-draft-count').textContent=drafts.length;$('#contribution-empty').hidden=drafts.length>0;
  const list=$('#contribution-list');list.replaceChildren();
  for(const [i,draft] of drafts.entries()){
    const row=document.createElement('article');row.className='contribution-row';
    const mark=document.createElement('span');mark.className='row-mark';mark.textContent=String(i+1).padStart(2,'0');
    const copy=document.createElement('div'),title=document.createElement('h3'),sub=document.createElement('p');title.textContent=draft.camera;sub.textContent=`${draft.context} · ${draft.area||'Area not provided'}`;copy.append(title,sub);
    const badge=document.createElement('span');badge.className='draft-status';badge.textContent=draft.example?'EXAMPLE DRAFT':'LOCAL DRAFT';
    const details=document.createElement('details');details.className='draft-details';const summary=document.createElement('summary');summary.textContent='View details';details.append(summary);
    const dl=document.createElement('dl');dl.className='review-list';fillList(dl,[['Recording',draft.filename],['Segment',`${draft.start}–${draft.end} seconds`],['Saved',new Date(draft.created).toLocaleDateString()],['Next step',draft.example?'Example only. Start a new draft to use your own video.':'Video was not stored. Keep the original recording; submission is not available yet.']]);details.append(dl);
    row.append(mark,copy,badge,details);list.append(row);
  }
}
export function renderContributor(page){
  $('#contributor-shell').dataset.page=page;
  const onboarding=page==='contribute';$('#onboarding-page').hidden=!onboarding;$('#contributor-home').hidden=onboarding;
  if(onboarding)showStep(step,false);else {video.pause();renderHome();}
}
window.addEventListener('pagehide',()=>video.pause());
showStep(0,false);
