#!/usr/bin/env python3
"""Build a blind local review packet from fresh tracker recordings; no VLM calls."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
from datetime import datetime, timezone

from semantic_memory_server import Retrieval
from temporal_interpretation import track_crops, evidence_crops, SOURCE_CROP_MAX_EDGE
from state_verification import STATES


def digest(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def check_annotations(packet_path, annotations_path):
    packet=json.loads(Path(packet_path).read_text());review=json.loads(Path(annotations_path).read_text())
    if review.get('packet_sha256')!=digest(packet_path) or review.get('review_method')!='human' or not isinstance(review.get('reviewer'),str) or not review['reviewer'].strip() or not review.get('reviewed_utc'):
        raise ValueError('A named human reviewer, review timestamp and matching packet hash are required')
    if len(review.get('cases',[]))!=len(packet['cases']):raise ValueError('All cases must be reviewed')
    for expected,actual in zip(packet['cases'],review['cases']):
        scope=actual.get('annotation_scope')
        if (packet.get('version')=='human-state-review-v2' or scope is not None) and scope not in ('target','scene'):
            raise ValueError('Explicit selected-target versus whole-scene review scope is required')
        if actual.get('id')!=expected['id'] or actual.get('identity') not in ('same','different','unclear') or type(actual.get('reference_visible')) is not bool:
            raise ValueError('Missing identity or physical-reference annotation')
        if not isinstance(actual.get('object'),str) or not actual['object'].strip():raise ValueError('Missing physical target description')
        if len(actual.get('frames',[]))!=len(expected['frames']):raise ValueError('Missing per-frame states')
        if digest(expected['source_path'])!=expected['source_sha256']:raise ValueError('Source evidence changed')
        if digest(Path(packet_path).parent/expected['video'])!=expected['video_sha256']:raise ValueError('Context video changed')
        for saved,label in zip(expected['frames'],actual['frames']):
            if label.get('timestamp_ms')!=saved['timestamp_ms'] or label.get('visibility') not in ('visible','absent','unclear') or label.get('state') not in STATES['camera_distance']:
                raise ValueError('Invalid frame state or citation')
            for key in ('image','crop'):
                if digest(Path(packet_path).parent/saved[key])!=saved[key+'_sha256']:raise ValueError('Packet images changed')
    return review


def build(data_dir,output,development=False):
    import imageio_ffmpeg
    from select_clear_tracks import candidate_windows
    root=Path(output)
    if root.exists() and any(root.iterdir()):raise ValueError('Use an empty output directory')
    root.mkdir(parents=True,exist_ok=True)
    service=Retrieval(SimpleNamespace(data_dir=str(Path(data_dir).resolve()),frames=8,frame_size=384))
    cases=[];inputs=[]
    for manifest in sorted((Path(data_dir)/'memory/manifests').glob('*.json')):
        job=json.loads(manifest.read_text())
        if job['status']!='completed':continue
        if not development and (job['request'].get('temporal_enabled') or job['request'].get('vlm_enabled')):raise ValueError('Fresh review recordings must not contain generated action interpretations')
        for camera in job['result']['camera_results']:
            graph=camera.get('long_memory')
            if not graph:continue
            source=manifest.parent.parent/job['id']/camera['camera']['camera_id']/'source.mkv'
            probe=dict(source_path=str(source.resolve()),start_ms=0,end_ms=min(20000,camera['duration_ms']))
            detail_size=service.frames(probe,count=1,size=SOURCE_CROP_MAX_EDGE,no_upscale=True)[0].size
            candidates=candidate_windows(graph,detail_size,camera['duration_ms'])
            for candidate in candidates[:2]:
                entity=candidate['entity']
                obs=candidate['observations']
                start=min(o['timestamp_ms'] for o in obs) if obs else 0;end=min(start+20000,camera['duration_ms'])
                obs=[o for o in obs if start<=o['timestamp_ms']<end]
                points=sorted({o['timestamp_ms'] for o in obs})
                if entity and len(points)<2:continue
                count=min(8,len(points)) if entity else 8
                times=[points[round(i*(len(points)-1)/(count-1))] for i in range(count)] if entity else [round(start+(i+.5)/count*(end-start)) for i in range(count)]
                camera_id=camera['camera']['camera_id'];case_id=camera_id+'-'+(entity['entity_id'][-8:] if entity else 'untracked')
                source=manifest.parent.parent/job['id']/camera_id/'source.mkv'
                event=dict(event_id=case_id,source_path=str(source.resolve()),start_ms=start,end_ms=end,grounding=dict(entity_id=entity['entity_id'],source_sha256=graph['source_sha256'],class_name=entity['class_name'],observations=obs) if entity else None)
                frames,pts=service.frames(event,count=count,with_timestamps=True,sample_timestamps=times)
                crops,crop_provenance=evidence_crops(service,event,frames,pts,count,times,digest(source),(start,end),'source')
                folder=root/case_id;folder.mkdir()
                saved=[]
                for i,(frame,t,crop) in enumerate(zip(frames,pts,crops)):
                    if crop is None:continue
                    image_path=folder/f'{i}-full.jpg';crop_path=folder/f'{i}-crop.jpg'
                    frame.save(image_path,quality=90);crop['image'].save(crop_path,quality=90)
                    saved.append(dict(timestamp_ms=t,image=str(image_path.relative_to(root)),crop=str(crop_path.relative_to(root)),image_sha256=digest(image_path),crop_sha256=digest(crop_path),crop_size=list(crop['image'].size),bbox=crop.get('bbox')))
                if len(saved)<2:continue
                video=folder/'context.mp4'
                subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-y','-loglevel','error','-ss',str(start/1000),'-i',str(source),'-t',str((end-start)/1000),'-an','-vf','scale=960:-2','-c:v','libx264','-preset','fast','-crf','23','-vsync','vfr',str(video)],check=True,capture_output=True,timeout=90)
                cases.append(dict(id=case_id,camera_id=camera_id,detector_class=entity['class_name'] if entity else 'No vehicle track available; review the full context',entity_id=entity['entity_id'] if entity else None,comparison='camera_distance',reference='camera',crop_evidence=crop_provenance,source_path=str(source.resolve()),source_sha256=digest(source),start_ms=start,end_ms=end,video=str(video.relative_to(root)),video_sha256=digest(video),frames=saved))
                inputs.append(dict(query=f'Is the selected {entity['class_name'] if entity else 'vehicle'} moving toward or away from the camera?',events=[event]))
    service.db.close()
    if not cases:raise ValueError('No aligned multi-frame vehicle histories available')
    packet=dict(version='human-state-review-v2',created_utc=datetime.now(timezone.utc).isoformat(),action_inference_performed=development,annotation_status='pending',selection='source crops >=48px short edge, >=1s span and >=1.25 detector area ratio; geometry is not verified identity or motion',cases=cases)
    packet_path=root/'packet.json';packet_path.write_text(json.dumps(packet,indent=2))
    (root/'evaluation-inputs.json').write_text(json.dumps(inputs,indent=2))
    embedded=json.dumps(dict(packet=packet,packet_sha256=digest(packet_path))).replace('</','<\\/')
    (root/'review.html').write_text(HTML.replace('__PACKET__',embedded))
    print(json.dumps(dict(packet=str(packet_path),review=str(root/'review.html'),cases=len(cases),frames=sum(len(c['frames']) for c in cases),action_inference_performed=development)))


HTML='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Visn · Footage review</title>
<style>body{font:16px/1.6 system-ui;background:#f5eddd;color:#473225;margin:0}main{max-width:1100px;margin:auto;padding:32px}h1{font-size:36px}section{border-top:1px solid #b9a88f;padding:28px 0}.frames{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:18px}.context{position:relative}.target-box{position:absolute;box-sizing:border-box;border:3px solid #e65d21;pointer-events:none}figure{margin:0;background:#fff9ed;padding:12px}img{max-width:100%;display:block}.crop{height:140px;object-fit:contain;margin:12px auto}video{max-width:100%;max-height:400px}label{display:block;margin:10px 0}select,input,textarea,button{font:inherit;padding:9px;border:1px solid #a28b70;border-radius:6px;background:#fffaf1;max-width:100%;box-sizing:border-box}button{background:#305e44;color:white;cursor:pointer}small{display:block}#status{font-weight:600}textarea{width:100%}</style>
<main><p>VISN / HUMAN REVIEW</p><h1>What does the footage show?</h1><p>This packet shows footage without model action answers. Review the context video, then the frames and crops in source order. Detector IDs and class labels can be wrong. Judge physical identity yourself. Review the selected crop, not the street or camera view. Orange boxes mark the selected target when available.</p><p>For each image, mark visibility and apparent distance from the camera: far, middle, near, or unclear. Use the same scale across one case. Choose unclear when the perspective, resolution, identity or state is ambiguous. Still images and tracker IDs alone do not prove movement.</p><label>Your reviewer name <input id="reviewer" required autocomplete="name"></label><div id="cases"></div><button id="save">Save completed annotations</button><p id="status" role="status"></p></main>
<script type="application/json" id="packet">__PACKET__</script><script>
const data=JSON.parse(document.querySelector('#packet').textContent),packet=data.packet;
if(packet.action_inference_performed){const note=document.createElement('p');note.textContent='Development evidence: these recordings have already been used in model evaluation. No model predictions are shown here.';document.querySelector('h1').after(note)}
function el(tag,text){const n=document.createElement(tag);if(text)n.textContent=text;return n}
function select(label,options,parent){const row=el('label',label+' '),s=el('select');s.required=true;for(const [value,text] of [['','Choose…'],...options.map(x=>[x,x])]){const o=el('option',text);o.value=value;s.append(o)}row.append(s);parent.append(row);return s}
const controls=packet.cases.map((c,i)=>{const section=el('section');section.append(el('h2',`${i+1}. ${c.camera_id} · local track ${c.entity_id ? c.entity_id.slice(-8) : 'untracked context'}`),el('small',`Detector suggestion: ${c.detector_class}. Recording interval ${(c.start_ms/1000).toFixed(2)}–${(c.end_ms/1000).toFixed(2)}s. This is not a verified object identity.`));const video=el('video');video.controls=true;video.preload='metadata';video.src=c.video;section.append(video);const scope=select('What did you review?',['target','scene'],section);const scopeHint=el('small','target = the selected vehicle crop; scene = the entire camera view. Scene reviews cannot validate a vehicle action.');section.append(scopeHint);const label=el('label','Physical target description (the cropped vehicle, not the location) '),object=el('input');object.required=true;label.append(object);section.append(label);const identity=select('Same physical target across these images?',['same','different','unclear'],section),reference=select('Is the camera/scene reference sufficient to compare distance?',['yes','no'],section);const grid=el('div');grid.className='frames';const frames=c.frames.map(f=>{const fig=el('figure'),image=el('img'),crop=el('img');image.src=f.image;image.alt='Full context';crop.src=f.crop;crop.alt='Selected tracker crop';crop.className='crop';const context=el('div');context.className='context';context.append(image);if(f.bbox){const box=el('span');box.className='target-box';const [x,y,w,h]=f.bbox;Object.assign(box.style,{left:(x*100)+'%',top:(y*100)+'%',width:(w*100)+'%',height:(h*100)+'%'});context.append(box)}fig.append(el('figcaption',`Source time ${(f.timestamp_ms/1000).toFixed(2)}s`),context,crop,el('small',`Crop ${f.crop_size[0]} × ${f.crop_size[1]} px`));const visibility=select('Selected target visibility',['visible','absent','unclear'],fig),state=select('Selected target distance',['far','middle','near','unclear'],fig);grid.append(fig);return {visibility,state}});section.append(grid);const notes=el('textarea');notes.placeholder='Optional: identity switch, occlusion, uncertain perspective or other observations';notes.setAttribute('aria-label','Review notes');section.append(notes);document.querySelector('#cases').append(section);return {scope,object,identity,reference,frames,notes}});
document.querySelector('#save').onclick=()=>{const reviewer=document.querySelector('#reviewer');if(!reviewer.value.trim()){reviewer.focus();document.querySelector('#status').textContent='Enter your reviewer name.';return}const cases=[];for(let i=0;i<controls.length;i++){const c=controls[i],p=packet.cases[i],inputs=[c.scope,c.object,c.identity,c.reference,...c.frames.flatMap(f=>[f.visibility,f.state])];const missing=inputs.find(e=>!e.value.trim());if(missing){missing.focus();document.querySelector('#status').textContent=`Complete all fields in case ${i+1}; use unclear when needed.`;return}cases.push({id:p.id,annotation_scope:c.scope.value,object:c.object.value.trim(),identity:c.identity.value,reference_visible:c.reference.value==='yes',notes:c.notes.value,frames:c.frames.map((f,j)=>({timestamp_ms:p.frames[j].timestamp_ms,visibility:f.visibility.value,state:f.state.value}))})}const result={packet_sha256:data.packet_sha256,reviewer:reviewer.value.trim(),review_method:'human',reviewed_utc:new Date().toISOString(),cases};const a=el('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:'application/json'}));a.download='visn-state-annotations.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);document.querySelector('#status').textContent='Annotations downloaded. Keep the packet unchanged and provide this JSON for evaluation.'};
</script></html>'''

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--data-dir');p.add_argument('--development',action='store_true',help='Mark reused/previously inferred evidence as development material');p.add_argument('--output');p.add_argument('--packet');p.add_argument('--check-annotations');args=p.parse_args()
    if args.check_annotations:
        check_annotations(args.packet,args.check_annotations);print('Human annotations and evidence hashes validated; no inference performed.')
    elif args.data_dir and args.output:build(args.data_dir,args.output,args.development)
    else:p.error('Use --data-dir/--output or --packet/--check-annotations')
