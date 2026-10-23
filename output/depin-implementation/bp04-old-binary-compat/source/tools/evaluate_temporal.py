#!/usr/bin/env python3
"""Controlled synthetic direction probes; save real model outputs for human review.
These schematic fixtures are diagnostic, not a CCTV accuracy benchmark.
"""
import argparse,json,math,subprocess,urllib.request
from pathlib import Path
from PIL import Image,ImageDraw
import imageio_ffmpeg

def scene(kind,t):
    im=Image.new('RGB',(384,384),'#ddd4c8');d=ImageDraw.Draw(im)
    if kind=='door':
        d.rectangle((105,40,285,340),fill='#49403a',outline='#39302a',width=8)
        right=280-145*t;drop=40*t
        d.polygon([(110,45),(right,45+drop),(right,335-drop),(110,335)],fill='#916039',outline='#35271e')
        d.ellipse((right-20,185,right-10,195),fill='#ddbe67')
    elif kind=='object':
        d.rectangle((30,230,354,250),fill='#815c38');d.rectangle((50,250,65,380),fill='#60432b');d.rectangle((320,250,335,380),fill='#60432b')
        y=40+150*min(t*1.5,1);d.rectangle((165,y,215,y+40),fill='#b63428',outline='#752017',width=3)
        if t<.8:
            d.line((190,0,190,y),fill='#d7a27c',width=24);d.ellipse((175,y-15,205,y+5),fill='#d7a27c')
    else:
        d.rectangle((20,20,364,364),fill='#5a5b5b');d.rectangle((145,150,240,345),outline='white',width=4)
        y=-105+310*t
        d.rounded_rectangle((160,y,225,y+115),radius=12,fill='#b63a2d',outline='#342b29',width=3)
        d.rectangle((166,y+20,219,y+45),fill='#9cb4bc');d.rectangle((166,y+80,219,y+98),fill='#9cb4bc')
    return im

def main():
    p=argparse.ArgumentParser();p.add_argument('--still-only',action='store_true');p.add_argument('--url',default='http://127.0.0.1:8198');p.add_argument('--data-dir',default='data/highway-validation/temporal-fixtures');p.add_argument('--output',default='output/experiment2/direction-probes.json');args=p.parse_args()
    root=Path(args.data_dir);root.mkdir(parents=True,exist_ok=True);out=Path(args.output);out.parent.mkdir(parents=True,exist_ok=True)
    results=[]
    for kind,query,pair in [('door','Is the door opening or closing?',('opening','closing')),('object','Is the red object being placed on or removed from the table?',('placing','removing')),('car','Is the car arriving in or leaving the outlined parking space?',('arriving','leaving'))]:
        for reverse in ((False,) if args.still_only else (False,True)):
            path=root/f'{kind}-{"still" if args.still_only else int(reverse)}.mp4'
            frames=[scene(kind,i/47) for i in range(48)]
            if reverse: frames.reverse()
            if args.still_only: frames=[scene(kind,.5)]*48
            proc=subprocess.Popen([imageio_ffmpeg.get_ffmpeg_exe(),'-y','-loglevel','error','-f','rawvideo','-pix_fmt','rgb24','-s','384x384','-r','8','-i','-','-an','-c:v','libx264','-pix_fmt','yuv420p',str(path)],stdin=subprocess.PIPE)
            for frame in frames: proc.stdin.write(frame.tobytes())
            proc.stdin.close();assert proc.wait()==0
            body=dict(query=query,events=[dict(event_id=f'{kind}-{int(reverse)}',source_path=str(path.resolve()),start_ms=0,end_ms=6000,metadata={'scene':'controlled schematic fixture'})])
            print(f'Running {kind}, expected {pair[int(reverse)]}',flush=True)
            try:
                response=json.load(urllib.request.urlopen(urllib.request.Request(args.url+'/interpret',data=json.dumps(body).encode(),headers={'Content-Type':'application/json'}),timeout=600))
                value=response['interpretations'][0];value.pop('frames',None)
            except Exception as error: value={'error':str(error)}
            results.append(dict(fixture=kind,expected='uncertain' if args.still_only else pair[int(reverse)],result=value));out.write_text(json.dumps(results,indent=2));print(json.dumps(value),flush=True)
if __name__=='__main__': main()
