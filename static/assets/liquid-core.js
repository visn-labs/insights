import * as T from '../vendor/three.module.min.js';
// An implicit surface: droplets merge into one continuous skin as they approach.
// This is geometry in the shader, not a blurred DOM filter or a fade between shapes.
export function makeLiquidCore(){
 const material=new T.ShaderMaterial({
  uniforms:{uCamera:{value:new T.Vector3()},uTime:{value:0},uEnergy:{value:0},uPull:{value:new T.Vector3()}},
  vertexShader:`varying vec3 vPosition;void main(){vPosition=position;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.);}`,
  fragmentShader:`precision highp float;
  varying vec3 vPosition;uniform vec3 uCamera;uniform vec3 uPull;uniform float uTime;uniform float uEnergy;
  float merge(float a,float b,float k){float h=clamp(.5+.5*(b-a)/k,0.,1.);return mix(b,a,h)-k*h*(1.-h);}
  float field(vec3 p){
   float d=length(p)-.49;
   for(int i=0;i<5;i++){
    float a=float(i)*1.256637;float wave=sin(uTime*1.7+a)*uEnergy*.12;
    vec3 c=vec3(cos(a)*(.57+wave),sin(a)*(.57+wave),sin(a*2.+uTime*.5)*.18*uEnergy);
    c+=uPull*.22*(.5+.5*cos(a));
    d=merge(d,length(p-c)-.23,.27);
   }
   return d;
  }
  void main(){
   vec3 ray=normalize(vPosition-uCamera),p=vPosition;float travel=0.;bool hit=false;
   for(int i=0;i<48;i++){float d=field(p);if(d<.002){hit=true;break;}travel+=d;if(travel>4.)break;p+=ray*d*.8;}
   if(!hit)discard;
   vec2 e=vec2(.003,0.);vec3 n=normalize(vec3(field(p+e.xyy)-field(p-e.xyy),field(p+e.yxy)-field(p-e.yxy),field(p+e.yyx)-field(p-e.yyx)));
   vec3 l=normalize(vec3(-.6,1.,1.5)),v=-ray,h=normalize(l+v);float diffuse=max(dot(n,l),0.);float spec=pow(max(dot(n,h),0.),75.);float fresnel=pow(1.-max(dot(n,v),0.),3.);
   vec3 color=mix(vec3(.025,.16,.095),vec3(.16,.49,.29),diffuse);color+=vec3(1.,.89,.66)*spec*.9+vec3(.52,.72,.51)*fresnel*.48;
   gl_FragColor=vec4(color,1.);
  }`
 });
 const mesh=new T.Mesh(new T.BoxGeometry(2,2,2),material);mesh.renderOrder=1;
 return {mesh,update(camera,time,energy,pull){mesh.updateWorldMatrix(true,false);material.uniforms.uCamera.value.copy(camera.position);mesh.worldToLocal(material.uniforms.uCamera.value);material.uniforms.uTime.value=time;material.uniforms.uEnergy.value=energy;material.uniforms.uPull.value.copy(pull);}};
}
