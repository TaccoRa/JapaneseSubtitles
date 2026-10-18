// Protocol tests run against the actual service worker, using mock Chrome APIs.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const {webcrypto}=require('node:crypto');
const source=fs.readFileSync(path.join(__dirname,'../../browser-extension/background.js'),'utf8');
test('popup loads its top-level-await script as a module',()=>{
  const html=fs.readFileSync(path.join(__dirname,'../../browser-extension/popup.html'),'utf8');
  assert.match(html,/<script\s+type="module"\s+src="popup\.js"/);
});
const identity={tabId:1,documentId:'doc',frameId:2,videoId:'video'};
const sender={tab:{id:1},documentId:'doc',frameId:2};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
async function settle(){for(let i=0;i<8;i++)await tick();}
async function until(predicate){
  const deadline=Date.now()+1000;
  while(!predicate()){
    if(Date.now()>deadline)throw Error('Expected asynchronous extension result did not arrive');
    await tick();
  }
}
function environment(){
  let listener,socket;
  let connectedStream=null;
  const frames=[],offscreen=[],sent=[],scripts=[],windowChanges=[],timers=new Map();let timerId=0;
  const browserWindow={state:'maximized',type:'normal'};
  class Socket{
    static OPEN=1;
    constructor(){socket=this;this.readyState=1;}
    send(value){sent.push(JSON.parse(value));}
    close(){}
  }
  const chrome={
    runtime:{onMessage:{addListener(fn){listener=fn;}},async sendMessage(m){offscreen.push(m);if(m.type==='ConnectStream')connectedStream=m.tabId;if(m.type==='DisconnectStream')connectedStream=null;return {ok:true,connected:connectedStream!==null,tabId:connectedStream};}},
    storage:{local:{async get(){return {};},async set(){}}},
    offscreen:{async hasDocument(){return true;},async createDocument(){}},
    tabCapture:{async getMediaStreamId(){return 'stream';}},
    scripting:{async executeScript(value){scripts.push(value);}},
    windows:{async get(){return {...browserWindow};},async update(id,change){windowChanges.push({id,...change});Object.assign(browserWindow,change);}},
    fetch:async()=>({ok:true,json:async()=>({service:'SubtitlePlayer',version:1}),text:async()=>Array.from({length:10},(_,i)=>`${i+1}\n00:00:${String(i*5).padStart(2,'0')}.000 --> 00:00:${String(i*5+2).padStart(2,'0')}.000\ntext`).join('\n\n')}),
    tabs:{async query(){return [];},async get(id){return {id,active:true,windowId:5};},async captureVisibleTab(){return 'data:image/png;base64,picture';},async sendMessage(tab,m,target){frames.push({tab,m,target});return {ok:true,state:{}};},onActivated:{addListener(){}},onRemoved:{addListener(){}},onUpdated:{addListener(){}}}
  };
  vm.runInNewContext(source,{chrome,fetch:(...args)=>chrome.fetch(...args),AbortSignal,WebSocket:Socket,crypto:webcrypto,TextEncoder,URL,console,setInterval:()=>1,clearInterval(){},setTimeout:(fn,delay)=>{timers.set(++timerId,{fn,delay});return timerId;},clearTimeout:id=>timers.delete(id)});
  const message=(m,s={})=>new Promise(resolve=>{const pending=listener(m,s,resolve);if(pending!==true)resolve();});
  return {chrome,frames,offscreen,sent,scripts,timers,browserWindow,windowChanges,message,get socket(){return socket;},
    async fireTimers(){const pending=[...timers.values()];timers.clear();for(const timer of pending)timer.fn();await settle();},
    async connect(paused=true,native=true){
      await message({type:'Connect',identity,token:'code'});socket.onopen();socket.onmessage({data:JSON.stringify({type:'Welcome'})});
      await settle();socket.onmessage({data:JSON.stringify({type:'RecorderConfig',backend:native?'native':'asbplayer'})});await settle();
      if(native)assert.equal((await message({type:'EnableNativeRecording',identity})).ok,true);
      await message({type:'PlaybackSnapshot',videoId:'video',sequence:1,currentTime:20,duration:1000,rate:1.5,paused,mediaKey:'episode',width:1000,height:500},sender);
      await new Promise(resolve=>setTimeout(resolve,15));await settle();
    },
    async receive(m){socket.onmessage({data:JSON.stringify(m)});await settle();}
  };
}
async function recording(env){
  const fingerprint=env.sent.find(m=>m.type==='PlaybackSnapshot').fingerprint;
  await env.receive({type:'CaptureRequest',operationId:'capture',identity,fingerprint,start:10,end:12});
}
test('rejects a stale document without controlling any frame',async()=>{
  const env=environment();await env.connect();env.frames.length=0;
  await env.receive({type:'PlayerCommand',operationId:'command',identity:{...identity,documentId:'old'},action:'play'});
  assert.equal(env.frames.length,0);assert.equal(env.sent.at(-1).ok,false);
});
test('capture commands target the exact bound document, not the active tab',async()=>{
  const env=environment();await env.connect();await recording(env);
  const prepare=env.frames.find(x=>x.m.type==='PrepareCapture');
  assert.equal(prepare.tab,1);assert.equal(prepare.target.documentId,'doc');assert.equal(prepare.target.frameId,2);
  assert.equal(env.offscreen.filter(x=>x.type==='StartRecording').length,1);
  await recording(env); // duplicate operation must not start/cancel a recording
  assert.equal(env.offscreen.filter(x=>x.type==='StartRecording').length,1);
  assert.equal(env.offscreen.filter(x=>x.type==='CancelRecording').length,0);
});
for(const [policy,paused,expected]of [['play',true,true],['paused',false,false],['restore',true,false],['restore',false,true]]){
  test(`post-capture ${policy}, previous paused ${paused}`,async()=>{
    const env=environment();await env.connect(paused);await recording(env);
    assert.equal(env.frames.filter(x=>x.m.action==='play').length,0);
    await env.receive({type:'CaptureCommit',operationId:'capture',ok:true,policy});
    assert.equal(env.frames.filter(x=>x.m.action==='play').length,expected?1:0);
    assert.equal(env.frames.find(x=>x.m.action==='rate').m.rate,1.5);
    assert.equal(env.sent.at(-1).type,'CaptureFinished');
  });
}
test('failed media write and abort never resume',async()=>{
  const env=environment();await env.connect(false);await recording(env);
  await env.receive({type:'CaptureCommit',operationId:'capture',ok:false,error:'Anki failed',policy:'play'});
  assert.equal(env.frames.filter(x=>x.m.action==='play').length,0);
  assert.equal(env.sent.at(-1).ok,false);
  await env.receive({type:'CaptureCommit',operationId:'capture',ok:true,policy:'play'});
  assert.equal(env.frames.filter(x=>x.m.action==='play').length,0);
});
test('a user abort invalidates delayed successful completion',async()=>{
  const env=environment();await env.connect(false);await recording(env);
  await env.message({type:'CaptureAborted',operationId:'capture',error:'User sought'},sender);await settle();
  await env.receive({type:'CaptureCommit',operationId:'capture',ok:true,policy:'play'});
  assert.equal(env.frames.filter(x=>x.m.action==='play').length,0);
});

test('repeated connection requests serialize stream cleanup and acquisition',async()=>{
  const env=environment();
  const one=env.message({type:'Connect',identity,token:'code'});
  const two=env.message({type:'Disconnect'});
  const three=env.message({type:'Connect',identity,token:'code'});
  assert.ok((await one).ok);assert.ok((await two).ok);assert.ok((await three).ok);
  assert.deepEqual(env.offscreen.map(m=>m.type),['DisconnectStream','DisconnectStream','DisconnectStream']);
});

test('asbplayer mode follows the clock without acquiring or retaining tab audio',async()=>{
  const env=environment();await env.connect(true,false);
  assert.equal(env.offscreen.filter(m=>m.type==='ConnectStream').length,0);
  assert.equal(env.sent.filter(m=>m.type==='PlaybackSnapshot').length,1);
  assert.match((await env.message({type:'EnableNativeRecording',identity})).error,/asbplayer is selected/);
});

test('switching back to asbplayer releases native audio before another capture',async()=>{
  const env=environment();await env.connect();env.offscreen.length=0;
  await env.receive({type:'RecorderConfig',backend:'asbplayer'});
  assert.equal(env.offscreen.filter(m=>m.type==='DisconnectStream').length,1);
  await recording(env);
  assert.equal(env.sent.at(-1).ok,false);
  assert.equal(env.offscreen.filter(m=>m.type==='StartRecording').length,0);
});

test('episode adapters distinguish episodes from opaque player IDs and preserve series identity',()=>{
  const scope={URL};vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../browser-extension/episode-context.js'),'utf8'),scope);
  const parse=scope.subtitlePlayerEpisodeContext;
  const one=parse('https://www.miruro.to/watch/series/kimetsu-no-yaiba?ep=5');
  const two=parse('https://www.miruro.to/watch/series/kimetsu-no-yaiba?ep=6');
  assert.equal(one.seriesKey,two.seriesKey);assert.equal(two.episode,6);
  assert.equal(parse('https://www.miruro.to/watch/series/anime?ep=opaque-id'),null);
  const ani=parse('https://aniworld.to/anime/stream/kimetsu/staffel-2/episode-3');
  assert.equal(ani.season,2);assert.equal(ani.episode,3);
  assert.equal(parse('https://player.test/?id=145779'),null);
});

test('next episode player replacement stays in the bound tab and adopts its new document',async()=>{
  const env=environment();await env.connect();
  await env.message({type:'PlaybackSnapshot',videoId:'new-video',sequence:1,currentTime:0,duration:1447,rate:1,paused:true,mediaKey:'next-episode',width:1000,height:500},{...sender,documentId:'new-document'});
  await settle();
  const bound=env.sent.filter(m=>m.type==='Bound').at(-1);
  assert.equal(bound.identity.documentId,'new-document');assert.equal(bound.identity.tabId,1);
  env.frames.length=0;
  await env.receive({type:'PlayerCommand',operationId:'old',identity,action:'play'});
  assert.equal(env.frames.length,0);assert.equal(env.sent.at(-1).ok,false);
});

test('captions arriving before the replacement video binds are replayed for alignment',async()=>{
  const env=environment();await env.connect();
  const nextSender={...sender,documentId:'new-document'};
  await env.message({type:'CaptionResource',videoId:'new-video',mediaKey:'next',url:'https://captions.test/en.vtt',language:'en',label:'English',priority:0},nextSender);
  await settle();
  await env.message({type:'PlaybackSnapshot',videoId:'new-video',sequence:1,currentTime:0,duration:1447,rate:1,paused:true,mediaKey:'next',width:1000,height:500},nextSender);
  await until(()=>env.sent.some(m=>m.type==='CaptionText'&&m.identity.documentId==='new-document'));
  const caption=env.sent.filter(m=>m.type==='CaptionText').at(-1);
  assert.equal(caption.identity.documentId,'new-document');assert.equal(caption.identity.videoId,'new-video');
});

test('opening the popup recovers scripts in existing frames and refreshes the video list',async()=>{
  const env=environment();await env.connect();
  const reply=await env.message({type:'ListVideos',tabId:1});
  assert.equal(reply.videos.length,1);assert.equal(env.scripts.length,1);
  assert.equal(env.scripts[0].target.tabId,1);assert.equal(env.scripts[0].target.allFrames,true);
  assert.deepEqual(Array.from(env.scripts[0].files),['episode-context.js','content.js']);
});

test('the single visible video automatically connects without acquiring audio',async()=>{
  const env=environment();await settle();
  await env.message({type:'ListVideos',tabId:1});
  await env.message({type:'PlaybackSnapshot',videoId:'video',sequence:1,currentTime:10,duration:1000,rate:1,paused:true,mediaKey:'auto-episode',width:1000,height:500},sender);
  await until(()=>[...env.timers.values()].some(timer=>timer.delay===300));await env.fireTimers();
  await until(()=>env.socket);assert.equal(env.offscreen.filter(m=>m.type==='ConnectStream').length,0);
  env.socket.onopen();env.socket.onmessage({data:JSON.stringify({type:'Welcome'})});await settle();
  assert.equal(env.sent.find(m=>m.type==='Bound').identity.videoId,'video');
});

test('explicit disconnect keeps automatic reconnection paused',async()=>{
  const env=environment();await env.connect();await env.message({type:'ListVideos',tabId:1});
  await env.message({type:'Disconnect'});env.sent.length=0;
  await env.message({type:'PlaybackSnapshot',videoId:'video',sequence:2,currentTime:11,duration:1000,rate:1,paused:true,mediaKey:'episode',width:1000,height:500},sender);
  await settle();await env.fireTimers();
  assert.equal((await env.message({type:'ListVideos',tabId:1})).autoEnabled,false);
  assert.equal(env.sent.length,0);
});

test('a refused bridge connection stays retryable until the app starts',async()=>{
  const env=environment();await env.connect();const first=env.socket;
  first.onerror();first.onclose({code:1006});
  assert.match((await env.message({type:'ListVideos',tabId:1})).status,/Waiting for SubtitlePlayer/);
  await env.fireTimers();assert.notEqual(env.socket,first);
  env.socket.onopen();env.socket.onmessage({data:JSON.stringify({type:'Welcome'})});await settle();
  assert.equal((await env.message({type:'ListVideos',tabId:1})).status,'Connected');
});

test('an offline desktop is retried without constructing refused WebSockets',async()=>{
  const env=environment(),healthy=env.chrome.fetch;
  env.chrome.fetch=async()=>{throw Error('Connection refused');};
  assert.equal((await env.message({type:'Connect',identity,token:'code'})).ok,true);
  assert.equal(env.socket,undefined);
  assert.match((await env.message({type:'ListVideos',tabId:1})).status,/Start the desktop app/);
  await env.fireTimers();assert.equal(env.socket,undefined);
  env.chrome.fetch=healthy;await env.fireTimers();await until(()=>env.socket);
  env.socket.onopen();env.socket.onmessage({data:JSON.stringify({type:'Welcome'})});await settle();
  assert.equal((await env.message({type:'ListVideos',tabId:1})).status,'Connected');
});

test('a different local service is never sent the pairing token',async()=>{
  const env=environment();env.chrome.fetch=async()=>({ok:true,json:async()=>({service:'other',version:1})});
  await env.message({type:'Connect',identity,token:'secret'});
  assert.equal(env.socket,undefined);assert.equal(env.sent.length,0);
});

test('same-tab reconnect and repeated enable preserve the existing native stream',async()=>{
  const env=environment();await env.connect();const socket=env.socket;
  env.offscreen.length=0;
  await env.message({type:'Connect',identity,token:'code'});
  await env.message({type:'EnableNativeRecording',identity});
  assert.equal(env.socket,socket);
  assert.equal(env.offscreen.filter(m=>['ConnectStream','DisconnectStream'].includes(m.type)).length,0);
  assert.equal((await env.message({type:'ListVideos',tabId:1})).nativeEnabled,true);
});

test('a replacement episode publishes recorder readiness under its new document identity',async()=>{
  const env=environment();await env.connect();env.offscreen.length=0;
  await env.message({type:'PlaybackSnapshot',videoId:'next-video',sequence:1,currentTime:0,duration:1447,rate:1,paused:true,mediaKey:'next-episode',width:1000,height:500},{...sender,documentId:'new-document'});
  await until(()=>env.sent.some(m=>m.type==='RecorderState'&&m.identity?.documentId==='new-document'));
  assert.equal(env.sent.filter(m=>m.type==='RecorderState').at(-1).enabled,true);
  assert.equal(env.offscreen.filter(m=>m.type==='DisconnectStream').length,0);
});

test('loss of tab audio disables readiness and aborts recording without resuming',async()=>{
  const env=environment();await env.connect();await recording(env);
  await env.message({type:'NativeRecordingStopped',tabId:1});await settle();
  assert.equal(env.sent.filter(m=>m.type==='RecorderState').at(-1).enabled,false);
  assert.equal(env.frames.filter(f=>f.m.action==='play').length,0);
  assert.equal(env.sent.at(-1).ok,false);
});

for(const [backend,enabled,expected]of [['native',false,1],['native',true,0],['asbplayer',false,0]]){
  test(`opening popup auto-enables native once: backend ${backend}, enabled ${enabled}`,async()=>{
    const elements={};
    for(const id of ['token','native','video','connect','status','disconnect'])elements[id]={value:'',options:[],replaceChildren(){this.options=[];},append(option){this.options.push(option);this.value=option.value;}};
    const requests=[],intervals=[];let nativeEnabled=enabled;
    const scope={document:{querySelector:selector=>elements[selector.slice(1)],createElement:()=>({})},setInterval:fn=>intervals.push(fn),
      chrome:{tabs:{query:async()=>[{id:1}]},storage:{local:{get:async()=>({})}},runtime:{sendMessage:async m=>{
        requests.push(m);
        if(m.type==='EnableNativeRecording'){nativeEnabled=true;return {ok:true};}
        return {backend,nativeEnabled,selected:identity,status:'Connected',autoEnabled:true,videos:[{identity,width:1000,height:500,duration:1000}]};
      }}}};
    await vm.runInNewContext('(async()=>{'+fs.readFileSync(path.join(__dirname,'../../browser-extension/popup.js'),'utf8')+'})()',scope);
    await intervals[0]();await intervals[0]();
    assert.equal(requests.filter(m=>m.type==='EnableNativeRecording').length,expected);
    if(backend==='native')assert.equal(elements.native.textContent,'Native recording enabled');
  });
}

test('captured player fullscreen expands the browser and restores its previous size',async()=>{
  const env=environment();await env.connect();
  await env.message({type:'FullscreenChanged',fullscreen:true},sender);await settle();
  assert.equal(env.browserWindow.state,'fullscreen');assert.equal(env.windowChanges[0].id,5);
  await env.message({type:'FullscreenChanged',fullscreen:false},sender);await settle();
  assert.equal(env.browserWindow.state,'maximized');
});

test('fullscreen belonging to another video tab or document cannot resize the watching window',async()=>{
  const env=environment();await env.connect();
  await env.message({type:'FullscreenChanged',fullscreen:true},{...sender,tab:{id:99}});
  await env.message({type:'FullscreenChanged',fullscreen:true},{...sender,documentId:'other-doc'});await settle();
  assert.equal(env.windowChanges.length,0);
});

test('preexisting browser fullscreen is preserved on player fullscreen exit',async()=>{
  const env=environment();await env.connect();env.browserWindow.state='fullscreen';
  await env.message({type:'FullscreenChanged',fullscreen:true},sender);await settle();
  await env.message({type:'FullscreenChanged',fullscreen:false},sender);await settle();
  assert.equal(env.windowChanges.length,0);assert.equal(env.browserWindow.state,'fullscreen');
});

test('a replaced fullscreen frame cannot leave the new episode stuck in browser fullscreen',async()=>{
  const env=environment();await env.connect();
  await env.message({type:'FullscreenChanged',fullscreen:true},sender);await settle();
  assert.equal(env.browserWindow.state,'fullscreen');
  await env.message({type:'PlaybackSnapshot',videoId:'next-video',sequence:1,currentTime:0,duration:1447,rate:1,paused:true,mediaKey:'next-episode',width:1000,height:500,fullscreen:false},{...sender,documentId:'new-document'});
  await until(()=>env.browserWindow.state==='maximized');
  assert.equal(env.sent.filter(m=>m.type==='Bound').at(-1).identity.documentId,'new-document');
});

test('nested fullscreen remains expanded until both frames exit; manual restore takes priority',async()=>{
  const env=environment();await env.connect();
  const outer={tab:{id:1},documentId:'outer',frameId:0};
  await env.message({type:'FullscreenChanged',fullscreen:true},sender);
  await env.message({type:'FullscreenChanged',fullscreen:true},outer);await settle();
  await env.message({type:'FullscreenChanged',fullscreen:false},sender);await settle();
  assert.equal(env.browserWindow.state,'fullscreen');
  env.browserWindow.state='normal'; // Manual F11/restore supersedes automatic state.
  await env.message({type:'FullscreenChanged',fullscreen:false},outer);await settle();
  assert.equal(env.browserWindow.state,'normal');assert.equal(env.windowChanges.length,1);
});

test('screenshot uses the bound browser window and resumes only after cropping succeeds',async()=>{
  const env=environment();await env.connect();await recording(env);
  const captures=[];env.chrome.tabs.captureVisibleTab=async(window,options)=>{captures.push({window,options});return 'data:image/png;base64,picture';};
  await env.message({type:'CaptureScreenshot',operationId:'capture'},sender);await settle();
  assert.equal(captures.length,1);assert.equal(captures[0].window,5);
  assert.equal(env.offscreen.filter(m=>m.type==='StoreScreenshot').length,1);
  assert.equal(env.frames.filter(f=>f.m.type==='CaptureCheckpoint').length,1);
  assert.equal(env.frames.filter(f=>f.m.type==='ScreenshotReady').length,1);
});

test('an inactive bound tab never captures another tab and never resumes',async()=>{
  const env=environment();await env.connect();await recording(env);
  env.chrome.tabs.get=async id=>({id,active:false,windowId:5});
  env.chrome.tabs.captureVisibleTab=async()=>{throw Error('Must not capture');};
  await env.message({type:'CaptureScreenshot',operationId:'capture'},sender);await settle();
  assert.equal(env.offscreen.filter(m=>m.type==='StoreScreenshot').length,0);
  assert.equal(env.frames.filter(f=>f.m.type==='ScreenshotReady').length,0);
  assert.equal(env.sent.at(-1).ok,false);assert.match(env.sent.at(-1).error,/selected/);
});

test('abort during a delayed screenshot discards the image and does not resume',async()=>{
  const env=environment();await env.connect();await recording(env);
  let release;env.chrome.tabs.captureVisibleTab=()=>new Promise(resolve=>release=resolve);
  await env.message({type:'CaptureScreenshot',operationId:'capture'},sender);await settle();
  await env.message({type:'CaptureAborted',operationId:'capture',error:'User sought'},sender);await settle();
  release('data:image/png;base64,picture');await settle();
  assert.equal(env.offscreen.filter(m=>m.type==='StoreScreenshot').length,0);
  assert.equal(env.frames.filter(f=>f.m.type==='ScreenshotReady').length,0);
});
