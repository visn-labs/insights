#!/usr/bin/env python3
"""Original synthetic contributor samples. No source footage or real identities."""
from pathlib import Path
import math
import subprocess
from PIL import Image,ImageDraw,ImageFont
import imageio_ffmpeg
ROOT=Path(__file__).resolve().parents[1]

def main():
    font_path=Path('/System/Library/Fonts/Supplemental/Georgia.ttf')
    font=ImageFont.truetype(str(font_path),20) if font_path.exists() else ImageFont.load_default()
    small=ImageFont.truetype(str(font_path),13) if font_path.exists() else font
    for name,label in [('shop','CORNER SHOP'),('loading','LOADING BAY'),('forecourt','FORECOURT')]:
        path=ROOT/f'static/assets/sample-{name}.mp4'
        command=[imageio_ffmpeg.get_ffmpeg_exe(),'-y','-loglevel','error','-f','rawvideo','-pix_fmt','rgb24','-s','720x432','-r','15','-i','pipe:0','-an','-c:v','libx264','-threads','1','-pix_fmt','yuv420p','-crf','23','-movflags','+faststart',str(path)]
        process=subprocess.Popen(command,stdin=subprocess.PIPE)
        try:
            for frame in range(180):
                t=frame/15;image=Image.new('RGB',(720,432),'#e1d2b7');draw=ImageDraw.Draw(image)
                draw.polygon([(0,315),(720,236),(720,432),(0,432)],fill='#beac90')
                for y in range(295,440,24):draw.line([(0,y+45),(720,y-35)],fill='#a99578',width=1)
                for x in range(0,750,48):draw.line([(x,285),(x+90,432)],fill='#a99578',width=1)
                draw.polygon([(58,90),(510,50),(665,115),(203,170)],fill='#654b35')
                draw.polygon([(58,90),(510,50),(510,281),(58,331)],fill='#d5bd98')
                draw.polygon([(510,50),(665,115),(665,308),(510,281)],fill='#b09573')
                for y in range(110,320,22):draw.line([(58,y),(510,y-40)],fill='#c3a986')
                draw.line([(58,99),(510,59)],fill='#f5e6c6',width=8)
                draw.polygon([(85,153),(481,117),(481,157),(85,194)],fill='#493327')
                draw.text((122,149),label,fill='#fff0d1',font=font)
                for i in range(12):
                    x=85+i*33;y=195-i*3;draw.polygon([(x,y),(x+33,y-3),(x+59,y+28),(x+26,y+31)],fill='#6f543b' if i%2 else '#e9dbb8')
                draw.polygon([(100,234),(268,217),(268,307),(100,325)],fill='#6e8376',outline='#f4e5c5',width=5)
                draw.line([(182,225),(182,316)],fill='#ebdbb8',width=4);draw.line([(106,270),(262,255)],fill='#ebdbb8',width=4)
                draw.polygon([(338,214),(441,203),(441,290),(338,302)],fill='#4a574b',outline='#f2e1bb',width=5)
                draw.line([(390,210),(390,296)],fill='#baa17d',width=3)
                draw.polygon([(526,132),(560,148),(560,188),(526,173)],fill='#72897a')
                draw.polygon([(593,167),(630,183),(630,223),(593,207)],fill='#72897a')
                draw.polygon([(464,116),(493,113),(510,124),(481,129)],fill='#f8efdc',outline='#6d553d')
                draw.ellipse((498,121,508,133),fill='#35634d')
                draw.line([(481,130),(481,152),(465,156)],fill='#6d553d',width=4)
                draw.polygon([(270,344),(452,324),(502,349),(321,371)],outline='#35634d',width=3)
                if name=='loading':
                    draw.polygon([(535,270),(588,264),(588,310),(535,317)],fill='#9b7953',outline='#6f5238');draw.polygon([(546,249),(579,245),(579,267),(546,272)],fill='#c0a278',outline='#6f5238')
                if name=='forecourt':
                    draw.polygon([(540,300),(621,291),(672,317),(672,352),(547,367),(518,342),(518,311)],fill='#839a86',outline='#536755');draw.polygon([(551,296),(597,291),(627,314),(553,322)],fill='#e6deca');draw.ellipse((536,345,561,370),fill='#493b2d');draw.ellipse((638,334,663,359),fill='#493b2d')
                x=100+t*37.5;y=337-(x-100)*.10;stride=math.sin(t*math.pi*2.2)*10
                draw.ellipse((x-23,y+28,x+23,y+39),fill='#ab987b')
                draw.line([(x-6,y-3),(x-7-stride,y+29)],fill='#594638',width=8);draw.line([(x+6,y-3),(x+7+stride,y+29)],fill='#594638',width=8)
                draw.polygon([(x-11,y-44),(x+10,y-46),(x+16,y),(x-15,y)],fill='#35634d')
                draw.line([(x-11,y-37),(x-22+stride,y-11)],fill='#b19774',width=5);draw.line([(x+10,y-39),(x+22-stride,y-10)],fill='#b19774',width=5)
                draw.ellipse((x-12,y-72,x+12,y-48),fill='#c3a785');draw.arc((x-12,y-75,x+12,y-50),180,350,fill='#5b4430',width=6)
                if t>=9.5:
                    draw.polygon([(435,292),(563,278),(590,376),(461,390)],fill='#817766',outline='#5e5549',width=3);draw.text((493,322),'?',fill='#fff5df',font=font)
                draw.rectangle((0,400,720,432),fill='#493327');draw.text((20,408),'SYNTHETIC EXAMPLE  /  NOT CAMERA FOOTAGE',font=small,fill='#fff2d7');draw.text((625,408),f'00:{int(t):02d}',font=small,fill='#fff2d7')
                process.stdin.write(image.tobytes())
        finally:process.stdin.close()
        if process.wait()!=0:raise RuntimeError('Synthetic sample encoding failed')
        print(path.name,path.stat().st_size)

if __name__=='__main__':main()
