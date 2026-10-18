const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const crypto=require('node:crypto');
const source=fs.readFileSync(path.join(__dirname,'../../browser-extension/content.js'),'utf8');

function environment(){
  let now=0,id=0,listener;
  const timers=new Map(),sent=[],windowEvents=new Map();
  const schedule=(fn,delay,repeat)=>{timers.set(++id,{fn,at:now+delay,repeat});return id;};
  class Video extends EventTarget{
    constructor(){super();this._time=20;this.duration=1000;this.paused=false;this.seeking=false;this.readyState=4;this.playbackRate=1;this.currentSrc='blob:fixed-video';this.audioTracks=[];this.textTracks=[];this.isConnected=true;}
    get currentTime(){return this._time;}
    set currentTime(value){this.target=value;this.seeking=true;}
    getBoundingClientRect(){return {x:0,y:0,width:800,height:450};}
    querySelectorAll(){return [];}
    pause(){this.paused=true;}
    async play(){this.paused=false;}
  }
  const video=new Video();
  const document={querySelectorAll:selector=>selector==='video'?[video]:[]};
  const window={addEventListener:(name,fn)=>windowEvents.set(name,fn),postMessage(data){windowEvents.get('message')?.({data,source:window});}};
  vm.runInNewContext(source,{document,window,top:window,parent:window,location:{href:'https://video.test/episode'},innerWidth:800,innerHeight:450,
    performance:{now:()=>now},crypto,console,
    chrome:{runtime:{sendMessage:async m=>{sent.push(m);},onMessage:{addListener:fn=>{listener=fn;}}}},
    setInterval:(fn,delay)=>schedule(fn,delay,delay),setTimeout:(fn,delay)=>schedule(fn,delay,0),
    clearInterval:key=>timers.delete(key),clearTimeout:key=>timers.delete(key)});
  function advance(ms){
    const end=now+ms;
    while(true){const next=[...timers].sort((a,b)=>a[1].at-b[1].at)[0];if(!next||next[1].at>end)break;
      now=next[1].at;if(next[1].repeat)next[1].at+=next[1].repeat;else timers.delete(next[0]);next[1].fn();}
    now=end;
  }
  advance(100);
  return {video,sent,advance,command(action,time){
    const videoId=sent.find(m=>m.type==='PlaybackSnapshot').videoId;
    return new Promise(resolve=>listener({type:'PlayerCommand',identity:{videoId},action,time},{},resolve));
  }};
}

test('seek acknowledges a completed clock change even without seeked',async()=>{
  const env=environment(),reply=env.command('seek',100);
  env.video._time=100;env.video.seeking=false;env.advance(50);
  assert.equal((await reply).ok,true);
});
test('seeked arriving before seeking clears does not cause a false timeout',async()=>{
  const env=environment(),reply=env.command('seek',100);
  env.video._time=100;env.video.dispatchEvent(new Event('seeked'));
  env.video.seeking=false;env.advance(50);
  assert.equal((await reply).ok,true);
});
test('an ignored seek fails with observed position and clock publishing continues',async()=>{
  const env=environment(),reply=env.command('seek',100);
  env.video.seeking=false;env.advance(10000);
  const result=await reply;
  assert.equal(result.ok,false);assert.match(result.error,/requested 100.00s, player at 20.00s/);
  env.video._time=25;env.advance(100);
  assert.equal(env.sent.filter(m=>m.type==='PlaybackSnapshot').at(-1).currentTime,25);
});
test('duration updates preserve media identity, while a replaced resource invalidates it',()=>{
  const env=environment();const initial=env.sent.find(m=>m.type==='PlaybackSnapshot').mediaKey;
  env.video.duration=1000.4;env.advance(100);
  assert.equal(env.sent.filter(m=>m.type==='PlaybackSnapshot').at(-1).mediaKey,initial);
  env.video.dispatchEvent(new Event('emptied'));
  assert.notEqual(env.sent.filter(m=>m.type==='PlaybackSnapshot').at(-1).mediaKey,initial);
});
