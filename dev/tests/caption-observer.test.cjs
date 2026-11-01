const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../../browser-extension/caption-observer.js'),'utf8');
const reference=fs.readFileSync(path.join(__dirname,'../reviews/subtitleplayer-2026-10-18/source-en.vtt'),'utf8');
function environment(options={}){
  const posted=[],listeners=new Map(),location={href:'https://player.test/ep5',origin:'https://player.test'};
  let calls=0,resolve;const forwarded=[];
  const pending=new Promise(r=>resolve=r);
  const window={postMessage:data=>posted.push(data),addEventListener:(name,fn)=>listeners.set(name,fn)};
  class CustomEvent extends Event{constructor(type,options){super(type);this.detail=options.detail;}}
  const originalFetch=function(...args){calls++;forwarded.push({receiver:this,args});return pending;};
  const scope=vm.createContext({window,location,URL,CustomEvent,document:{querySelectorAll:()=>options.media===false?[]:[{src:'https://captions.test/opaque.jpeg',label:'English',srclang:''}]},
    fetch:originalFetch,MutationObserver:options.MutationObserver,console});
  vm.runInContext(source,scope);
  return {scope,posted,location,pending,resolve,originalFetch,forwarded,get calls(){return calls;},request(request){listeners.get('message')({source:window,data:{channel:'SubtitlePlayerCaptionTimingV1',request}});},replay(){this.request('replay');}};
}
const settle=()=>new Promise(r=>setImmediate(r));

test('non-video pages leave fetch untouched, including replay on a Reddit wiki',()=>{
  const env=environment({media:false});env.location.href='https://www.reddit.com/r/example/wiki/index';
  assert.equal(env.scope.fetch,env.originalFetch);env.replay();assert.equal(env.scope.fetch,env.originalFetch);
});

test('the observer starts when a video is added after document_start',async()=>{
  let changed,disconnected=false;
  class MutationObserver{constructor(callback){changed=callback;}observe(){}disconnect(){disconnected=true;}}
  const env=environment({media:false,MutationObserver});assert.equal(env.scope.fetch,env.originalFetch);
  env.scope.document.querySelectorAll=selector=>selector==='video,media-player,track[src]'?[{}]:[];
  changed();assert.notEqual(env.scope.fetch,env.originalFetch);assert.equal(disconnected,true);
  env.scope.fetch('https://captions.test/en.vtt');
  env.resolve({ok:true,url:'https://captions.test/en.vtt',headers:{get:()=>null},clone:()=>({text:async()=>reference})});
  await settle();assert.equal(env.posted[0].intervals.length,222);
});

test('fetch credentials, receiver, Request and returned Promise are preserved exactly',async()=>{
  const env=environment(),receiver={},request={url:'https://www.reddit.com/svc/events/preload-head'},init={credentials:'include'};
  assert.equal(env.scope.fetch.call(receiver,request,init),env.pending);
  assert.equal(env.forwarded[0].receiver,receiver);assert.equal(env.forwarded[0].args[0],request);assert.equal(env.forwarded[0].args[1],init);
  let clones=0;env.resolve({ok:true,url:request.url,headers:{get:()=> 'application/json'},clone(){clones++;}});
  await settle();assert.equal(init.credentials,'include');assert.equal(clones,0);
});
test('actual episode reference is observed without another fetch or changing the player Promise',async()=>{
  const env=environment();
  assert.equal(env.scope.fetch('https://captions.test/opaque.jpeg'),env.pending);
  env.resolve({ok:true,url:'https://captions.test/opaque.jpeg',headers:{get:()=>null},clone:()=>({text:async()=>reference})});
  await settle();
  assert.equal(env.calls,1);assert.equal(env.posted.length,1);assert.equal(env.posted[0].intervals.length,222);
  assert.equal(env.posted[0].priority,-100);assert.equal(env.posted[0].text,undefined);assert.equal(env.posted[0].url,undefined);
  env.replay();assert.equal(env.posted.length,2);
  env.location.href='https://player.test/ep6';env.replay();assert.equal(env.posted.length,2);
});
test('late subtitle responses from the previous episode are discarded',async()=>{
  const env=environment();env.scope.fetch('https://captions.test/en.vtt');env.location.href+='-next';
  env.resolve({ok:true,url:'https://captions.test/en.vtt',headers:{get:()=>null},clone:()=>({text:async()=>reference})});
  await settle();assert.equal(env.posted.length,0);
});
test('image sprite thumbnails are never treated as speech references',()=>{
  const env=environment();
  assert.equal(env.scope.subtitlePlayerCaptionObserver.parse('00:00:01.000 --> 00:00:02.000\nsprite.jpg#xywh=0,0,100,100').length,0);
});
test('episode replacement with an unchanged iframe URL drops its old timing cache',async()=>{
  const env=environment();env.scope.fetch('https://captions.test/en.vtt');
  env.request('reset');
  env.resolve({ok:true,url:'https://captions.test/en.vtt',headers:{get:()=>null},clone:()=>({text:async()=>reference})});
  await settle();env.replay();assert.equal(env.posted.length,0);
});
test('documented custom-player tracks work even when HTML video tracks are disabled',()=>{
  const env=environment(),cues=Array.from({length:12},(_,i)=>({startTime:i*7+1,endTime:i*7+3}));
  env.scope.document.querySelectorAll=selector=>selector==='media-player'?[{textTracks:[{language:'en',label:'English',src:'episode8.vtt',mode:'showing',cues}]}]:[];
  env.replay();assert.equal(env.posted.at(-1).intervals.length,12);assert.equal(env.posted.at(-1).priority,-100);
  const before=env.posted.length;env.request('reset');env.replay();assert.equal(env.posted.length,before);
  env.scope.document.querySelectorAll=selector=>selector==='media-player'?[{textTracks:[{language:'en',label:'English',src:'episode9.vtt',mode:'showing',cues}]}]:[];
  env.replay();assert.ok(env.posted.length>before);
});
test('plain English is selected and English 2 is excluded from timing evidence',()=>{
  const env=environment(),cues=Array.from({length:12},(_,i)=>({startTime:i*7+1,endTime:i*7+3}));
  const tracks=[
    {language:'en',label:'English 2',src:'alternate.vtt',mode:'showing',cues},
    {language:'en',label:'English',src:'primary.vtt',mode:'disabled',cues}
  ];
  env.scope.document.querySelectorAll=selector=>selector==='media-player'?[{textTracks:tracks}]:[];
  env.replay();
  const unique=[...new Map(env.posted.map(x=>[x.referenceId,x])).values()];
  assert.equal(unique.length,1);assert.equal(unique[0].priority,-100);
  assert.equal(tracks[1].mode,'showing');
});

test('English is requested once while unloaded and pending timing is announced',()=>{
  const env=environment();let writes=0,mode='disabled';
  const primary={label:'English',language:'en',src:'primary.vtt',cues:[],get mode(){return mode;},set mode(value){writes++;mode=value;}};
  env.scope.document.querySelectorAll=selector=>selector==='media-player'?[{textTracks:[primary]}]:[];
  env.replay();env.replay();
  assert.equal(writes,1);assert.equal(mode,'showing');
  assert.ok(env.posted.every(m=>m.preferredPending===true));
});

test('React host resolves Vidstack instance through the official discovery event',()=>{
  const env=environment(),primary={label:'English',language:'en',src:'primary.vtt',mode:'disabled',cues:Array.from({length:12},(_,i)=>({startTime:i*7+1,endTime:i*7+3}))};
  const player={dispatchEvent(event){assert.equal(event.type,'find-media-player');event.detail({textTracks:[primary]});}};
  env.scope.document.querySelectorAll=selector=>selector==='media-player'?[player]:[];
  env.replay();assert.equal(primary.mode,'showing');
  assert.equal(env.posted.at(-1).label,'English');assert.equal(env.posted.at(-1).priority,-100);
});

test('early opaque responses wait for track metadata instead of publishing other languages',async()=>{
  const env=environment();let elements=[];
  env.scope.document.querySelectorAll=selector=>selector==='track[src]'?elements:[];
  env.scope.fetch('https://captions.test/opaque.jpeg');
  env.resolve({ok:true,url:'https://captions.test/opaque.jpeg',headers:{get:()=> 'text/vtt'},clone:()=>({text:async()=>reference})});
  await settle();assert.equal(env.posted.length,0);
  elements=[{getAttribute:name=>({src:'https://captions.test/opaque.jpeg',label:'English',srclang:'en'}[name]||'')}];
  env.replay();assert.equal(env.posted.at(-1).label,'English');assert.equal(env.posted.at(-1).intervals.length,222);
});

test('plain English selection uses the documented request when only DOM tracks exist',()=>{
  const env=environment(),events=[];
  const elements=['English 2','French','English'].map(label=>({getAttribute:name=>name==='label'?label:''}));
  const attributes={};
  const player={querySelectorAll:()=>elements,setAttribute:(name,value)=>attributes[name]=value,dispatchEvent:event=>events.push(event)};
  env.scope.document.querySelectorAll=selector=>selector==='media-player'?[player]:[];
  env.replay();env.replay();
  const requests=events.filter(e=>e.type==='media-text-track-change-request');
  assert.equal(requests.length,1);
  assert.deepEqual({...requests[0].detail},{index:2,mode:'showing'});
  assert.equal(attributes['data-subtitleplayer-caption-requested'],'English');
});

test('hashed JPEG caption responses are retained but need matching English metadata',async()=>{
  const env=environment(),url='https://captions.test/a/'+'a'.repeat(64)+'.jpeg';let elements=[];
  env.scope.document.querySelectorAll=selector=>selector==='track[src]'?elements:[];
  env.scope.fetch(url);
  env.resolve({ok:true,url,headers:{get:()=>null},clone:()=>({text:async()=>reference})});
  await settle();assert.equal(env.posted.length,0);
  elements=[{getAttribute:name=>({src:url,label:'French',srclang:'fr'}[name]||'')}];
  env.replay();assert.equal(env.posted.length,0);
  elements=[{getAttribute:name=>({src:url,label:'English',srclang:'en'}[name]||'')}];
  env.replay();assert.equal(env.posted.at(-1).label,'English');
});

function netflixEnvironment(){
  const env=environment();
  env.location.href='https://www.netflix.com/watch/80045950';env.location.origin='https://www.netflix.com';
  const track={trackId:'german-dialogue',bcp47:'de-DE',displayName:'Deutsch'};
  const player={getMovieId:()=>80045950,getTimedTextTrack:()=>track};
  const metadata={getTitle:()=> 'Assassination Classroom',isEpisodic:()=>true,getEpisodeNumber:()=>2,getSeason:()=>({_season:{seq:1}})};
  const root={nested:{caption:{type:'timedtext',trackId:track.trackId,urls:[{url:'https://captions.test/opaque-subtitle'}]}}};root.cycle=root;
  const app={getAPI:()=>({videoPlayer:{getAllPlayerSessionIds:()=>['current'],getVideoPlayerBySessionId:()=>player},
    getVideoMetadataByVideoId:()=>({getCurrentVideo:()=>metadata})}),getState:()=>({videoPlayer:{cadmiumPlayerRepository:{playersById:{current:root}}}})};
  env.scope.netflix={appContext:{state:{playerApp:app}}};
  env.scope.document.querySelectorAll=selector=>selector==='video'?[{}]:[];
  env.scope.AbortSignal=AbortSignal;
  return Object.assign(env,{track,player,metadata,root,app});
}

test('Netflix publishes the real episode and loaded German timing without native tracks',async()=>{
  const env=netflixEnvironment();env.replay();
  assert.deepEqual({...env.posted[0].mediaContext},{title:'Assassination Classroom',kind:'episode',episode:2,season:1});
  env.resolve({ok:true,headers:{get:()=>null},text:async()=>reference});await settle();
  const caption=env.posted.find(value=>value.intervals);
  assert.equal(caption.intervals.length,222);assert.equal(caption.language,'de-de');assert.equal(caption.label,'Deutsch');
  assert.equal(caption.url,undefined);assert.equal(caption.text,undefined);
});

test('Netflix does not reuse a late response after changing the active track or episode',async()=>{
  for(const change of [env=>{env.track.trackId='other';},env=>{env.player.getMovieId=()=>80045951;},env=>env.request('reset')]){
    const env=netflixEnvironment();env.replay();change(env);
    env.resolve({ok:true,headers:{get:()=>null},text:async()=>reference});await settle();
    assert.equal(env.posted.filter(value=>value.intervals).length,0);
  }
});

test('Netflix ignores forced/image tracks and ambiguous playback sessions',()=>{
  for(const change of [env=>{env.track.isForcedNarrative=true;},env=>{env.track.isImageBased=true;},
    env=>{const api=env.app.getAPI();api.videoPlayer.getAllPlayerSessionIds=()=>['one','two'];env.app.getAPI=()=>api;}]){
    const env=netflixEnvironment();change(env);env.replay();assert.equal(env.calls,0);
  }
});

test('Netflix retries a failed caption only after an explicit refresh',async()=>{
  const env=netflixEnvironment();env.replay();env.replay();
  env.resolve({ok:false});await settle();env.replay();assert.equal(env.calls,1);
  env.request('refresh');assert.equal(env.calls,2);
});

test('Netflix caption timing remains available when display metadata is temporarily missing',async()=>{
  const env=netflixEnvironment();
  const api=env.app.getAPI();api.getVideoMetadataByVideoId=()=>{throw Error('Metadata not loaded');};env.app.getAPI=()=>api;
  env.replay();env.resolve({ok:true,headers:{get:()=>null},text:async()=>reference});await settle();
  assert.equal(env.posted.find(value=>value.intervals).intervals.length,222);
});

test('Netflix never replays timing for a subtitle track that has been switched off',async()=>{
  const env=netflixEnvironment();env.replay();
  env.resolve({ok:true,headers:{get:()=>null},text:async()=>reference});await settle();
  assert.ok(env.posted.some(value=>value.intervals));
  const before=env.posted.length;env.track.isNoneTrack=true;env.track.trackId='off';env.replay();
  assert.equal(env.posted.slice(before).filter(value=>value.intervals).length,0);
});

test('TTML tick and frame times use declared rates, including duration-only cues',()=>{
  const env=environment();
  const attributes={'ttp:tickRate':'10000000','ttp:frameRate':'25'};
  const cues=[{begin:'10000000t',end:'30000000t'},{begin:'40000000t',dur:'20000000t'},{begin:'200f',end:'250f'}];
  env.scope.DOMParser=class{parseFromString(){return {documentElement:{getAttribute:name=>attributes[name]||null},
    getElementsByTagNameNS:()=>cues.map(values=>({getAttribute:name=>values[name]||null}))};}};
  const parse=()=>JSON.parse(JSON.stringify(env.scope.subtitlePlayerCaptionObserver.parse('<tt:tt xmlns:tt="http://www.w3.org/ns/ttml"/>')));
  assert.deepEqual(parse(),[[1,3],[4,6],[8,10]]);
  delete attributes['ttp:tickRate'];delete attributes['ttp:frameRate'];assert.deepEqual(parse(),[]);
});
