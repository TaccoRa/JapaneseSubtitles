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
  const documentEvents=new Map();
  const document={querySelectorAll:selector=>selector==='video'?[video]:[],addEventListener:(name,fn)=>documentEvents.set(name,fn),removeEventListener:name=>documentEvents.delete(name)};
  const window={addEventListener:(name,fn)=>windowEvents.set(name,fn),removeEventListener:name=>windowEvents.delete(name),postMessage(data){windowEvents.get('message')?.({data,source:window});}};
  const runtime={id:'test-extension',sendMessage:async m=>{sent.push(m);},onMessage:{addListener:fn=>{listener=fn;},removeListener:()=>{listener=null;}}};
  const scope=vm.createContext({document,window,top:window,parent:window,location:{href:'https://video.test/episode'},innerWidth:800,innerHeight:450,
    performance:{now:()=>now},crypto,console,
    chrome:{runtime},
    setInterval:(fn,delay)=>schedule(fn,delay,delay),setTimeout:(fn,delay)=>schedule(fn,delay,0),
    clearInterval:key=>timers.delete(key),clearTimeout:key=>timers.delete(key)});
  vm.runInContext(source,scope);
  function advance(ms){
    const end=now+ms;
    while(true){const next=[...timers].sort((a,b)=>a[1].at-b[1].at)[0];if(!next||next[1].at>end)break;
      now=next[1].at;if(next[1].repeat)next[1].at+=next[1].repeat;else timers.delete(next[0]);next[1].fn();}
    now=end;
  }
  advance(100);
  const message=m=>new Promise(resolve=>listener(m,{},resolve));
  return {video,sent,advance,message,runtime,timers,windowEvents,documentEvents,reinject(){vm.runInContext(source,scope);},fullscreen(){documentEvents.get('fullscreenchange')?.();},command(action,time){
    const videoId=sent.find(m=>m.type==='PlaybackSnapshot').videoId;
    return new Promise(resolve=>listener({type:'PlayerCommand',identity:{videoId},action,time},{},resolve));
  }};
}

test('seek acknowledges a completed clock change even without seeked',async()=>{
  const env=environment(),reply=env.command('seek',100);
  env.video._time=100;env.video.seeking=false;env.advance(50);
  assert.equal((await reply).ok,true);
});

test('script recovery republishes a video without duplicate listeners or a new identity',()=>{
  const env=environment(),identity=env.sent[0].videoId;
  env.reinject();env.reinject();
  assert.equal(env.sent.filter(m=>m.type==='PlaybackSnapshot').at(-1).videoId,identity);
  const before=env.sent.length;
  env.video.dispatchEvent(new Event('pause'));
  assert.equal(env.sent.length,before+1);
});

test('invalidated extension contexts stop timers and detach old listeners',()=>{
  const env=environment(),before=env.sent.length;
  Object.defineProperty(env.runtime,'id',{get(){throw Error('Extension context invalidated.');},configurable:true});
  assert.doesNotThrow(()=>env.advance(10000));
  assert.equal(env.sent.length,before);assert.equal(env.timers.size,0);
  assert.equal(env.windowEvents.size,0);assert.equal(env.documentEvents.size,0);
  env.video.dispatchEvent(new Event('pause'));assert.equal(env.sent.length,before);
  Object.defineProperty(env.runtime,'id',{value:'test-extension',configurable:true});
  env.reinject();env.advance(100);
  assert.ok(env.sent.length>before);assert.equal(env.timers.size,2);
});

test('runtime rejection during reload is handled and retires the old content instance',async()=>{
  const env=environment();
  env.runtime.sendMessage=async()=>{throw Error('Extension context invalidated.');};
  env.advance(100);await Promise.resolve();await Promise.resolve();
  assert.equal(env.timers.size,0);
  assert.doesNotThrow(()=>env.advance(10000));
});

test('fullscreen during viewing preserves the video identity and clock',()=>{
  const env=environment(),identity=env.sent[0].videoId;
  env.fullscreen();env.video._time=30;env.advance(100);
  assert.equal(env.sent.at(-1).videoId,identity);assert.equal(env.sent.at(-1).currentTime,30);
  assert.equal(env.sent.filter(m=>m.type==='CaptureAborted').length,0);
});

for(const [policy,previousPaused,paused] of [['play',true,false],['paused',false,true],['restore',true,true],['restore',false,false]])test(`capture boundary policy ${policy}, previously paused ${previousPaused}`,async()=>{
  const env=environment();env.video._time=10;
  const identity={videoId:env.sent[0].videoId},operationId='card';
  await env.message({type:'PrepareCapture',identity,operationId,start:10,end:12,screenshotTime:11,policy,previousRate:1.5,previousPaused});
  await env.message({type:'RunCapture',operationId});
  env.video._time=11;env.advance(100);
  assert.equal(env.video.paused,true);
  assert.equal(env.sent.filter(m=>m.type==='CaptureScreenshot').length,1);
  env.advance(1000);assert.equal(env.sent.filter(m=>m.type==='CaptureBoundary').length,0);
  assert.equal((await env.message({type:'ScreenshotReady',operationId:'stale'})).ok,false);
  assert.equal(env.video.paused,true);
  assert.equal((await env.message({type:'ScreenshotReady',operationId})).ok,true);
  assert.equal(env.video.paused,false);
  env.video._time=12;env.advance(100);
  assert.equal(env.video.paused,paused);assert.equal(env.video.playbackRate,1.5);assert.equal(env.sent.filter(m=>m.type==='CaptureBoundary').length,1);
  await env.message({type:'FinishCapture',operationId});assert.equal(env.video.paused,paused);
});

test('fullscreen changing mid-capture cancels the crop rather than attaching the wrong image',async()=>{
  const env=environment();env.video._time=10;
  const identity={videoId:env.sent[0].videoId},operationId='card';
  await env.message({type:'PrepareCapture',identity,operationId,start:10,end:12});
  env.fullscreen();
  assert.equal(env.sent.filter(m=>m.type==='CaptureAborted').length,1);
  assert.equal(env.sent.at(-1).type,'FullscreenChanged');
  assert.equal((await env.message({type:'ScreenshotReady',operationId})).ok,false);
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

test('English timing uses one caption track rather than mixing every language',()=>{
  const env=environment();
  const cues=Array.from({length:10},(_,i)=>({startTime:i*5,endTime:i*5+2}));
  env.video.textTracks=[{language:'ja',label:'Japanese',mode:'disabled',cues:cues.map(c=>({startTime:c.startTime+20,endTime:c.endTime+20}))},
    {language:'eng',label:'English',mode:'showing',cues},
    {language:'ger',label:'German',mode:'disabled',cues:cues.map(c=>({startTime:c.startTime+1,endTime:c.endTime+1}))}];
  env.advance(5000);
  const caption=env.sent.find(m=>m.type==='CaptionIntervals');
  assert.equal(caption.language,'eng');assert.equal(caption.intervals.length,10);assert.equal(caption.intervals[0][0],0);
});

test('caption discovery retries a resource after a temporary load failure',()=>{
  const env=environment();
  env.video.querySelectorAll=()=>[{src:'https://captions.test/en.vtt',srclang:'en',label:'English'}];
  env.advance(100);
  assert.equal(env.sent.filter(m=>m.type==='CaptionResource').length,1);
  env.advance(9900);
  assert.equal(env.sent.filter(m=>m.type==='CaptionResource').length,1);
  env.advance(100);
  assert.equal(env.sent.filter(m=>m.type==='CaptionResource').length,2);
});
