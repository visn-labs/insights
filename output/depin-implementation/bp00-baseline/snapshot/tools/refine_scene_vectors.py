"""Authored curved replacement figures and architectural props for the legacy vector asset library.
The active landing scene is Three.js; these remain reusable standalone SVGs.
"""
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1] / 'static/svgs'
DEFS = '''<defs>
 <linearGradient id="cloth" x1="0" x2="1"><stop stop-color="#342f2a"/><stop offset=".42" stop-color="#756450"/><stop offset=".7" stop-color="#5b4d3f"/><stop offset="1" stop-color="#302b27"/></linearGradient>
 <linearGradient id="skin" x1="0" x2="1" y2=".5"><stop stop-color="#a87552"/><stop offset=".5" stop-color="#e0ba91"/><stop offset="1" stop-color="#b3825e"/></linearGradient>
 <linearGradient id="leather" x2=".2" y2="1"><stop stop-color="#6c503c"/><stop offset="1" stop-color="#292420"/></linearGradient>
 <linearGradient id="silk" x1="0" x2="1"><stop stop-color="#594637"/><stop offset=".4" stop-color="#a18968"/><stop offset=".65" stop-color="#80684f"/><stop offset="1" stop-color="#49392d"/></linearGradient>
 <radialGradient id="shadow"><stop stop-color="#3c3028" stop-opacity=".23"/><stop offset="1" stop-color="#3c3028" stop-opacity="0"/></radialGradient>
 </defs>'''
FACE = '''<path d="M373 200 Q369 175 380 165 Q395 153 414 166 Q427 177 420 201 L415 216 Q405 230 393 220 L382 209Z" fill="url(#skin)"/>
 <path d="M377 180 Q377 157 397 158 Q420 157 423 179 L414 176 Q412 166 401 171 Q388 168 382 185Z" fill="#49382c"/>
 <path d="M384 186q7-4 13-1m7-1q7-3 12 1" stroke="#674b36" stroke-width="2" fill="none"/>
 <path d="M385 190q5-3 10 0m10-1q5-3 9 0" stroke="#49382e" stroke-width="1.5" fill="none"/>
 <path d="M401 187l-3 14 6 1m-12 9q9 3 16-2" stroke="#9d6b50" stroke-width="1.5" fill="none"/>
 <path d="M376 188q-8-7-6 6l7 7m44-13q6-6 4 5l-5 8" fill="url(#skin)"/>
 <path d="M388 215l-2 22 24 2-1-21" fill="url(#skin)"/>'''
def save(name, title, body):
 (ROOT/name).write_text(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 800" width="1024" height="1024" role="img" aria-labelledby="title"><title id="title">{title}</title>{DEFS}<ellipse cx="401" cy="651" rx="113" ry="20" fill="url(#shadow)"/><g stroke-linejoin="round" stroke-linecap="round">{body}</g></svg>\n')
boots='''<path d="M369 590l-7 37q-28 10-25 20 28 8 54-2l5-54m24-1 1 41q-13 14-4 19 23 8 46-2 2-8-22-17l-2-41" fill="url(#leather)" stroke="#302921" stroke-width="2"/><path d="M340 645q27 8 48-3m30 4q23 6 43-1" fill="none" stroke="#b99a76" stroke-width="1"/>'''
coat='''<path d="M367 389q-6 74-4 129l-4 82 38 1 8-95 9 94 32-1 3-95-17-113Z" fill="url(#cloth)"/>
 <path d="M389 421l-7 164m44-160 7 156" fill="none" stroke="#a18a6d" stroke-opacity=".38" stroke-width="2"/>
 <path d="M382 233q-37 4-48 24l8 99-10 82q29 17 64-3l9-97 10 98q26 13 49-5l-18-89 2-88q-17-20-38-22Z" fill="url(#cloth)" stroke="#483c30" stroke-width="2"/>
 <path d="M385 233l17 59 10-59" fill="#e6d7bc"/><path d="M396 249l11 0-2 12 6 36-12-3 2-32Z" fill="#63513f"/>
 <path d="M375 239l-10 32 22 10-12 12 23 39-13-73m31-22 17 36-17 8 11 13-19 35" fill="#78654f" stroke="#ad9370" stroke-width="1.2"/>
 <path d="M344 361l29 2m51-2 23-2M349 411l29 1m44 0 23-3" stroke="#b49a78" stroke-width="2"/>
 <path d="M340 251q-20 21-19 61l1 62-3 41 19 4 13-77 7-52m81-36q23 22 24 57l6 62-1 43-18 2-12-76-6-52" fill="url(#cloth)" stroke="#483c30" stroke-width="2"/>
 <path d="M320 414l-2 17q-6 15 1 20l5-18 1 20q4 5 6-1l3-19q3 19 7 12l-4-27m115-2 2 21q-3 14 3 13l3-16 1 19q4 4 6-2l0-20 4 15q5 1 2-11l-7-20" fill="url(#skin)"/>
 <path d="M320 407l21 4m107-2 22-3" stroke="#e0c9a7" stroke-width="5"/>'''
buttons=''.join(f'<circle cx="403" cy="{y}" r="2.7" fill="#b69b6e" stroke="#6a5035"/>' for y in [315,340,365,390])
hat='''<path d="M364 164q35-15 69 2l-3 9q-36 10-70-2Z" fill="url(#leather)"/><path d="M378 160l-2-54q22-11 45 1l-3 54Z" fill="url(#cloth)" stroke="#483d31" stroke-width="2"/><path d="M377 147q20 7 42 1l-1 12q-21 6-40 0Z" fill="#302b27"/><ellipse cx="399" cy="106" rx="23" ry="5" fill="#9b896b"/>'''
save('person_gentleman.svg','Victorian gentleman with tailored wool coat, leather boots and top hat',boots+coat+buttons+FACE+hat)
skirt='''<path d="M375 342q-22 59-36 125l-28 161q81 33 172-1l-28-161q-14-67-37-125Z" fill="url(#silk)" stroke="#584332" stroke-width="2"/>
 <path d="M376 373q-19 131-30 248m42-248-9 258m25-258 8 257m3-259 36 253m-27-247 44 238" fill="none" stroke="#c2a884" stroke-opacity=".42" stroke-width="3"/>
 <path d="M318 613q79 29 158 1m-159 8q83 27 161-1" fill="none" stroke="#4f3d2e" stroke-width="3"/>
 <path d="M382 233q-26 4-39 21l9 70 23 32q23 10 45-1l24-35 5-64q-13-20-39-23Z" fill="url(#silk)" stroke="#584332" stroke-width="2"/>
 <path d="M385 233l14 35 14-35" fill="#ecddbf"/><path d="M397 271l1 70m-26-86 10 77m43-76-12 76" stroke="#ceb28b" stroke-width="1.5" fill="none"/>
 <path d="M373 342q25 7 50-1l2 12q-26 8-52 0Z" fill="#51402f"/>
 <path d="M346 251q-25 13-21 48l10 56-7 56 17 3 16-61-7-60m88-42q20 8 21 38l-4 61 14 55-16 6-22-59 0-60" fill="url(#silk)" stroke="#584332" stroke-width="2"/>
 <path d="M328 408l-3 23q-4 15 2 15l5-17 0 21q5 3 7-5l2-22 5 13q5 2 3-10l-5-14m112-5 3 23q-2 17 4 17l2-19 4 22q5 2 4-9l-1-17 6 12q5 0 0-13l-7-19" fill="url(#skin)"/>
 <path d="M326 405l21 5m107-6 21-4" stroke="#dfcdae" stroke-width="5"/>'''
bonnet='''<path d="M368 181q-10-37 11-50 23-15 48 4 20 17 7 49l-14-11q8-26-16-31-24-3-24 31Z" fill="url(#silk)" stroke="#bd9f79" stroke-width="3"/><path d="M371 175q-2 50 27 65m31-64q5 39-27 64" stroke="#c1a882" stroke-width="5" fill="none"/><path d="M399 238q-22-12-16 5l16-2q21 15 17-3Z" fill="#9b7d5b"/><path d="M426 141q12-19 15-1-2 9-15 8 17 5 13 13-10 9-16-10" fill="#dbbd8d"/>'''
save('person_lady.svg','Victorian woman with pleated dress, fitted bodice and ribbon bonnet',boots+skirt+buttons+FACE+bonnet)

# Architectural props retain vector paths and controlled shaded surfaces.
def building(name, shop=False, clock=False):
    body = '<path d="M218 267L500 267 598 216 598 591 500 648 218 625Z" fill="#8a7258"/>'
    body += '<path d="M218 267H500V648L218 625Z" fill="#c7af8a" stroke="#79634b" stroke-width="2"/><path d="M500 267l98-51v375l-98 57Z" fill="#907b61"/>'
    for y in range(285, 620, 17):
        body += f'<path d="M224 {y}H495" stroke="#8d795e" stroke-opacity=".28"/>'
    body += '<path d="M200 269L322 170 610 211 501 282Z" fill="#53483d" stroke="#3e352d" stroke-width="3"/>'
    for i in range(12):
        x=210+i*24
        body += f'<path d="M{x} 263l95-78" stroke="#b39b7a" stroke-opacity=".33"/>'
    body += '<path d="M215 278H500l107-58M214 287H498M216 630l283 24 104-60" fill="none" stroke="#dfc8a1" stroke-width="7"/>'
    for row,y in enumerate([318,434]):
        for x in [249,334,419]:
            body += f'<path d="M{x-5} {y+76}v-81q31-20 62 0v81Z" fill="#90795b"/>'
            body += f'<path d="M{x} {y+70}v-72q26-18 52 0v72Z" fill="#4b5a52" stroke="#e0cba5" stroke-width="5"/>'
            body += f'<path d="M{x+26} {y-8}v78m-24-37h48" stroke="#bdac8c" stroke-width="3"/><path d="M{x+4} {y+8}l18-12v24l-18 12Z" fill="#d2d8b9" opacity=".28"/>'
            body += f'<path d="M{x-8} {y+77}h68" stroke="#e1cba6" stroke-width="7"/>'
    if shop:
        body += '<path d="M241 545H479V623L241 606Z" fill="#433d31" stroke="#68513b" stroke-width="7"/><path d="M352 550v67m-100-31 216 14" stroke="#b59b73" stroke-width="4"/><path d="M225 530H490l19 40-290-12Z" fill="#6d7c64"/>'
        for x in range(233,490,32):
            body += f'<path d="M{x} 531h15l13 33-17-1Z" fill="#dbc6a0"/>'
        body += '<rect x="245" y="506" width="226" height="23" rx="3" fill="#554431"/><text x="358" y="522" text-anchor="middle" font-family="serif" font-size="13" letter-spacing="3" fill="#e6d4ac">CORNER PROVISIONS</text>'
    else:
        body += '<path d="M340 622V548q31-20 62 0v78" fill="#524636" stroke="#dfc8a1" stroke-width="5"/><path d="M371 547v76" stroke="#a08963" stroke-width="2"/><circle cx="383" cy="591" r="3" fill="#c0a15f"/>'
    if clock:
        body += '<path d="M330 189V96H422V200Z" fill="#bea37c" stroke="#786043" stroke-width="3"/><path d="M317 97l59-54 60 54Z" fill="#574c3e"/><circle cx="376" cy="139" r="33" fill="#e5d7b7" stroke="#755f43" stroke-width="5"/>'
        for i in range(12):
            import math
            a=i*math.pi/6
            body += f'<path d="M{376+math.sin(a)*25:.2f} {139-math.cos(a)*25:.2f}l{math.sin(a)*-4:.2f} {math.cos(a)*4:.2f}" stroke="#61523c" stroke-width="2"/>'
        body += '<path d="M376 119v20l15 9" stroke="#443a2e" stroke-width="3" fill="none"/>'
    save(name,'Detailed Victorian masonry building with glazed sash windows',body)
building('building1.svg');building('building_shop.svg',shop=True);building('building_clock.svg',clock=True)
save('street_lamp.svg','Cast iron Victorian street lamp with glazed lantern and decorative brackets',
    '<path d="M355 653q43 13 89 0l-7-17h-76Z" fill="url(#leather)"/><path d="M380 640l8-61 5-308h12l6 308 9 61Z" fill="url(#cloth)" stroke="#493b2c" stroke-width="2"/><path d="M390 570h19m-20-10h21m-18-249h14" stroke="#ab9370" stroke-width="4"/><path d="M396 286q-57-60-14-100m18 100q58-60 15-100" fill="none" stroke="#5e503e" stroke-width="6"/><path d="M361 169l12 87q24 12 51 0l14-87Z" fill="#c7c9a2" fill-opacity=".55" stroke="#504332" stroke-width="5"/><path d="M369 175l11 70m48-70-10 70m-20-74v83" stroke="#8c7858" stroke-width="3"/><path d="M349 167q49-38 99 0l-7 9h-85Z" fill="url(#leather)"/><path d="M365 262h65l-17 13h-33Z" fill="#69563f"/><path d="M398 132v-15m-6 3q6-15 12 0" fill="none" stroke="#62513d" stroke-width="4"/>')
# Organic trunk and individual overlapping foliage silhouettes replace box crowns.
leaves=''
import math
for i in range(65):
    angle=i*2.399963; radius=math.sqrt(i/65)*135
    x=400+math.cos(angle)*radius;y=290+math.sin(angle)*radius*.85
    leaves+=f'<ellipse cx="{x:.2f}" cy="{y:.2f}" rx="{31+i%13}" ry="{24+i%9}" fill="{["#53614b","#6c7959","#87916c","#657350","#9a9d74"][i%5]}"/>'
save('tree.svg','Mature street tree with branching trunk and layered foliage', '<path d="M363 650q20-81 24-167l-5-132-30-73 14-7 35 72 16-108 13 4-11 141 42-57 12 8-58 92q-8 125 17 227l-26-10-17 16Z" fill="url(#leather)"/>'+leaves+'<path d="M386 621q17-113 9-209m9 70 2-95" stroke="#ad9370" stroke-width="3" fill="none"/>')
save('fountain.svg','Carved stone fountain with tiered basins and flowing water', '<ellipse cx="400" cy="618" rx="180" ry="49" fill="#8c8068"/><path d="M222 599q0 70 178 72 178-2 178-72l-19-10q-159 53-318 0Z" fill="url(#silk)" stroke="#9c8a6c" stroke-width="3"/><ellipse cx="400" cy="590" rx="174" ry="49" fill="#c5b99a"/><ellipse cx="400" cy="588" rx="151" ry="34" fill="#718b80"/><path d="M376 580l12-152h24l13 152q-22 15-49 0Z" fill="url(#silk)"/><path d="M287 406q8 68 113 69 105-1 113-69" fill="url(#silk)" stroke="#a39172" stroke-width="3"/><ellipse cx="400" cy="404" rx="114" ry="30" fill="#bdae8b"/><ellipse cx="400" cy="402" rx="98" ry="21" fill="#79968a"/><path d="M390 398l-3-115h26l-3 115Z" fill="url(#silk)"/><path d="M337 276q7 44 63 45 56-1 63-45" fill="url(#silk)"/><ellipse cx="400" cy="274" rx="64" ry="18" fill="#b6ad8a"/><path d="M399 268q-20-100-48-9m51 9q19-99 48-9m-111 28q-22 61-19 112m139-112q22 60 20 113m-182 23q-26 77-25 154m231-154q23 80 21 153" fill="none" stroke="#a9c7b5" stroke-opacity=".7" stroke-width="4"/>')

save('market_stall.svg','Timber market stall with striped canvas awning, crates and produce', '<path d="M220 650V317h15v333m330 0V317h15v333" fill="url(#leather)"/><path d="M211 325l46-78h291l43 78Z" fill="#6c7b62" stroke="#64553d" stroke-width="3"/>'+''.join(f'<path d="M{x} 248h26l12 76h-32Z" fill="#e2cc9f"/>' for x in range(267,540,52))+'<path d="M211 326v20q18 18 36 0 18 18 36 0 18 18 36 0 18 18 36 0 18 18 36 0 18 18 36 0 18 18 36 0 18 18 36 0 18 18 36 0 18 18 36 0v-20Z" fill="#a0a17b"/><path d="M225 498h348v112H225Z" fill="url(#silk)" stroke="#64503a" stroke-width="4"/><path d="M210 492h376v18H210Z" fill="#aa8a60"/>'+''.join(f'<path d="M{x} 517v87" stroke="#6b543b" stroke-width="2"/>' for x in range(240,572,22))+''.join(f'<circle cx="{265+i%9*32}" cy="{477-i//9*23}" r="15" fill="{["#9d7750","#a99d59","#8e9562"][i%3]}" stroke="#6e6943" stroke-width="1.5"/>' for i in range(18))+'<path d="M241 611l-5 39h23l3-39m273 0 3 39h23l-7-39" fill="url(#leather)"/>')
# Curved coach body, glazed doors, sprung axles and spoke wheels.
coach='<path d="M225 297q110-33 242 4l-9 174q-110 37-225-8Z" fill="url(#cloth)" stroke="#48392b" stroke-width="4"/><path d="M213 302q14-40 54-45h149q41 2 65 48Z" fill="url(#leather)"/><path d="M249 315h78v92h-78Zm96 0h91v92h-91Z" fill="#738276" stroke="#b6a17d" stroke-width="6"/><path d="M260 325l52 0-52 65m97-65h65l-65 65" fill="#c8d0b0" opacity=".25"/><path d="M340 313v166m-109-56h224m-201 26h68m33 0h69" stroke="#b19a72" stroke-width="3"/><circle cx="349" cy="424" r="4" fill="#bca16c"/><path d="M215 482q37 52 81 0m90 0q36 48 79 0" fill="none" stroke="#42392d" stroke-width="6"/><path d="M207 503h274l90 24" fill="none" stroke="#63513b" stroke-width="9"/>'
for cx,r in [(263,67),(430,78)]:
    coach+=f'<circle cx="{cx}" cy="541" r="{r}" fill="#c3af87" stroke="#3e342a" stroke-width="7"/>'
    for i in range(14):
        a=i*math.pi/7
        coach+=f'<path d="M{cx} 541l{math.cos(a)*(r-5):.2f} {math.sin(a)*(r-5):.2f}" stroke="#6b563d" stroke-width="3"/>'
    coach+=f'<circle cx="{cx}" cy="541" r="12" fill="#8b7958" stroke="#483c2d" stroke-width="3"/>'
coach+='<path d="M488 364q28-34 47-8l12 49-15 42-15-3 6-41-22-18-10 50-16-2Z" fill="url(#cloth)"/><path d="M505 347q-11-20 2-35 21-12 26 10l-7 25Z" fill="url(#skin)"/><path d="M494 314q18-12 43 1l-3 6-39-1Z" fill="#504231"/><path d="M499 309v-19l29 0v22" fill="url(#cloth)"/><path d="M523 373l41 16" stroke="#a88b63" stroke-width="7"/>'
save('carriage.svg','Victorian glazed coach with curved body, sprung suspension and fourteen-spoke wheels',coach)

# Keep the legacy inline library in sync with its authored vector files.
import re
library = ROOT.parent / 'svgs.js'
source = library.read_text()
for key in ['person_gentleman', 'person_lady', 'building_shop', 'building_clock', 'street_lamp', 'tree', 'fountain', 'market_stall', 'carriage']:
    value = (ROOT / f'{key}.svg').read_text().strip()
    source = re.sub(rf'({key}: `).*?(`,)', lambda match: match[1] + value + match[2], source, flags=re.S)
library.write_text(source)
