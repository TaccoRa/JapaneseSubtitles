/* One isolated-world instance per frame. No devtools or stream URL extraction. */
(()=>{
const existing=globalThis.subtitlePlayerContent;
if(existing?.version===3&&existing.alive()){existing.publish();return;}
existing?.dispose?.();
const videos=new Map(), ids=new WeakMap(), epochs=new WeakMap(), captionSources=new WeakMap(),captionObservations=new WeakMap();
let netflixMedia=null;
const pendingScreenshots=new Map();
const screenshotStyles=new Map();
const screenshotNodes=new Map(),screenshotVideos=new Map();
function restoreScreenshot(operationId){
  const saved=screenshotStyles.get(operationId);if(!saved)return;
  screenshotStyles.delete(operationId);clearTimeout(saved.timer);
  for(const node of saved.styles){
    const state=screenshotNodes.get(node);if(!state)continue;
    state.owners.delete(operationId);if(state.owners.size)continue;
    screenshotNodes.delete(node);
    if(node.style.getPropertyValue('visibility')==='hidden'){
      if(state.value)node.style.setProperty('visibility',state.value,state.priority);else node.style.removeProperty('visibility');
    }
  }
  for(const video of saved.native){
    const owners=screenshotVideos.get(video);if(!owners)continue;
    owners.delete(operationId);if(owners.size)continue;
    screenshotVideos.delete(video);if(video.isConnected)video.controls=true;
  }
}
async function prepareScreenshot(operationId,videoId){
  restoreScreenshot(operationId);
  const saved={styles:[],native:[],timer:null};screenshotStyles.set(operationId,saved);
  const selectors=['.vjs-control-bar','.vjs-big-play-button','.plyr__controls','.plyr__control--overlaid',
    '.jw-controlbar','.jw-display-icon-container','.jw-settings-menu',
    '.art-controls','.art-bottom','.art-top','.art-mask','.dplayer-controller','.dplayer-controller-mask',
    '.vds-controls','.shaka-controls-button-panel','.shaka-bottom-controls',
    '.fluid_controls_container','.fluid_initial_play_button'];
  if(/(^|\.)netflix\.com$/.test(location.hostname))selectors.push(
    '.watch-video--bottom-controls-container','.watch-video--top-controls-container',
    '[data-uia="player-loading"]');
  for(const root of roots())for(const node of root.querySelectorAll(selectors.join(','))){
    if(node.querySelector('.player-timedtext,.vjs-text-track-display,.plyr__captions,.jw-captions,.art-subtitle,.dplayer-subtitle'))continue;
    if(saved.styles.includes(node))continue;
    let state=screenshotNodes.get(node);
    if(!state){state={owners:new Set(),value:node.style.getPropertyValue('visibility'),priority:node.style.getPropertyPriority('visibility')};screenshotNodes.set(node,state);}
    state.owners.add(operationId);saved.styles.push(node);
    node.style.setProperty('visibility','hidden','important');
  }
  const video=videos.get(videoId);
  if(video&&(video.controls||screenshotVideos.has(video))){
    if(!screenshotVideos.has(video))screenshotVideos.set(video,new Set());
    screenshotVideos.get(video).add(operationId);saved.native.push(video);video.controls=false;
  }
  saved.timer=setTimeout(()=>restoreScreenshot(operationId),6000);
  const hidden=saved.styles.length+saved.native.length;
  if(!hidden)return {hidden:0,painted:true};
  // Hidden/advertising frames may never receive an animation frame. They must
  // not stall audio recording or the selected video's screenshot transaction.
  const painted=await new Promise(resolve=>{
    const timer=setTimeout(()=>resolve(false),150);
    requestAnimationFrame(()=>requestAnimationFrame(()=>{clearTimeout(timer);resolve(true);}));
  });
  return {hidden,painted};
}
let sequence=0, capture=null, suppress=0,shortcutBindings=[],shortcutScope=false;
const typing=()=>{
  let node=document.activeElement;
  while(node?.shadowRoot?.activeElement)node=node.shadowRoot.activeElement;
  return !!node&&(node.isContentEditable||/^(INPUT|TEXTAREA|SELECT)$/.test(node.tagName));
};
const keyToken=value=>({arrowleft:'left',arrowright:'right',arrowup:'up',arrowdown:'down',' ':'space',control:'ctrl',escape:'esc'}[value.toLowerCase()]||value.toLowerCase());
function ownShortcut(event){
  if(!shortcutScope||typing())return;
  if(capture&&event.key==='Escape'){event.preventDefault();event.stopImmediatePropagation();return;}
  const tokens=[];
  if(event.ctrlKey)tokens.push('ctrl');if(event.altKey)tokens.push('alt');if(event.shiftKey)tokens.push('shift');if(event.metaKey)tokens.push('cmd');
  const token=keyToken(event.key);if(!tokens.includes(token))tokens.push(token);
  const combination=tokens.sort().join('+');
  const physicalShortcut=!event.ctrlKey&&!event.altKey&&!event.shiftKey&&!event.metaKey&&/^(?:Digit[3-7]|Numpad[0-9]|NumpadEnter)$/.test(event.code||'')&&shortcutBindings.includes(event.code.toLowerCase());
  if(physicalShortcut||shortcutBindings.includes(combination)){event.preventDefault();event.stopImmediatePropagation();}
}
let lastEpisode='',lastEpisodeAt=0;
let disposed=false;
const timers=new Set(),listeners=[];
function dispose(){
  if(disposed)return;disposed=true;
  for(const operationId of screenshotStyles.keys())restoreScreenshot(operationId);
  for(const timer of timers)clearInterval(timer);
  for(const remove of listeners)try{remove();}catch{}
  capture=null;pendingScreenshots.clear();videos.clear();
  if(globalThis.subtitlePlayerContent?.dispose===dispose)delete globalThis.subtitlePlayerContent;
}
function alive(){
  if(disposed)return false;
  try{if(chrome.runtime.id)return true;}catch{}
  dispose();return false;
}
function listen(target,name,callback,capturePhase=false){
  const wrapped=(...args)=>{if(alive())callback(...args);};
  target.addEventListener(name,wrapped,capturePhase);listeners.push(()=>target.removeEventListener(name,wrapped,capturePhase));
}
function every(callback,delay){const timer=setInterval(()=>{if(alive())callback();},delay);timers.add(timer);return timer;}
const roots=()=>{
  if(!alive())return [];
  const all=[document];
  for(let i=0;i<all.length;i++)for(const e of all[i].querySelectorAll('*'))if(e.shadowRoot)all.push(e.shadowRoot);
  return all;
};
const send=m=>{
  if(!alive())return Promise.resolve();
  try{return chrome.runtime.sendMessage(m).catch(error=>{if(/context invalidated/i.test(error.message))dispose();});}
  catch{dispose();return Promise.resolve();}
};
function publish(){if(!alive())return;discover();for(const [id,v]of videos)send(snapshot(v,id,'discover'));window.postMessage({channel:'SubtitlePlayerCaptionTimingV1',request:'replay',hasVideo:videos.size>0},'*');}
globalThis.subtitlePlayerContent={version:3,publish,alive,dispose,prepareScreenshot,restoreScreenshot};
function mediaKey(video){
  // Streaming duration can change as segments load without changing the episode.
  return location.href+'|'+video.currentSrc+'|'+(epochs.get(video)||0)+'|'+[...video.audioTracks||[]].map(t=>t.id+':'+t.enabled);
}
function publishCaptions(video,id){
  if(video.readyState<1)return;
  let seen=captionSources.get(video);if(!seen){seen=new Map();captionSources.set(video,seen);}
  for(const track of video.querySelectorAll('track[src]')){
    const label=(track.srclang+' '+track.label).toLowerCase();
    if(/english\s*\(?2\)?/.test(label))continue;
    if(!/english|german|englisch|deutsch|\b(en|eng|de|deu|ger)\b/.test(label))continue;
    const resourceKey=mediaKey(video)+'|'+track.src+'|'+track.srclang+'|'+track.label;
    // Retry after a temporary offline/permission failure; a failed first fetch
    // must not leave alignment waiting forever for an already-discovered track.
    if(seen.has(resourceKey)&&performance.now()-seen.get(resourceKey)<10000)continue;
    seen.set(resourceKey,performance.now());
    const priority=/english|englisch|\b(en|eng)\b/.test(label)?(/(?:\(2\)|english\s*2)/.test(label)?10:0):20;
    send({type:'CaptionResource',url:track.src,videoId:id,mediaKey:mediaKey(video),language:track.srclang,label:track.label,priority});
  }
}
function snapshot(video,id,event='tick'){
  const r=video.getBoundingClientRect();
  return {type:'PlaybackSnapshot',videoId:id,sequence:++sequence,
    currentTime:video.currentTime,duration:Number.isFinite(video.duration)?video.duration:0,
    paused:video.paused,buffering:!video.paused&&video.readyState<3,seeking:video.seeking,rate:video.playbackRate,
    width:r.width,height:r.height,viewport:{width:innerWidth,height:innerHeight},
    fullscreen:!!document.fullscreenElement,
    textInput:typeof document.hasFocus==='function'&&document.hasFocus()&&typing(),
    // Full URL is hashed in the worker and never sent to the desktop.
    mediaKey:mediaKey(video),resourceKey:video.currentSrc+'|'+(epochs.get(video)||0),
    playerKey:netflixMedia?.playerVideoId||'',event};
}
function discover(){
  const found=new Set();
  for(const root of roots())for(const video of root.querySelectorAll('video')){
    found.add(video);
    if(ids.has(video)){publishCaptions(video,ids.get(video));continue;}
    const id=crypto.randomUUID();ids.set(video,id);videos.set(id,video);
    for(const event of ['play','playing','pause','seeking','seeked','waiting','stalled','ratechange','emptied','loadedmetadata','ended'])
      listen(video,event,()=>{
        if(event==='emptied'){netflixMedia=null;epochs.set(video,(epochs.get(video)||0)+1);window.postMessage({channel:'SubtitlePlayerCaptionTimingV1',request:'reset'},'*');}
        if(['seeking','emptied'].includes(event)){captionObservations.delete(video);send({type:'CaptionReset',videoId:id,mediaKey:mediaKey(video)});}
        send(snapshot(video,id,event));
        if(capture&&capture.id===id&&performance.now()>suppress&&['pause','seeking','ratechange','emptied'].includes(event))
          abort('Playback changed by the user or website');
      });
    publishCaptions(video,id);
  }
  for(const [id,v]of videos)if(!found.has(v)){videos.delete(id);if(capture?.id===id)abort('Video replaced');send({type:'VideoRemoved',videoId:id,replacementVideoId:videos.size===1?[...videos.keys()][0]:null});}
}
async function waitSeek(v,t){
  if(!Number.isFinite(t)||t<0)throw Error('Invalid seek position');
  if(Number.isFinite(v.duration))t=Math.min(t,v.duration);
  if(Math.abs(v.currentTime-t)<0.025&&!v.seeking)return;
  await new Promise((resolve,reject)=>{
    let timeout,poll;
    const events=['seeked','timeupdate','canplay','playing','loadeddata'];
    const done=()=>{if(!v.seeking&&Math.abs(v.currentTime-t)<0.25){cleanup();resolve();}};
    const fail=()=>{cleanup();reject(Error('Video source changed while seeking'));};
    const cleanup=()=>{clearTimeout(timeout);clearInterval(poll);for(const name of events)v.removeEventListener(name,done);v.removeEventListener('emptied',fail);};
    for(const name of events)v.addEventListener(name,done);
    v.addEventListener('emptied',fail);
    // Streaming players may omit seeked or deliver it before seeking clears.
    poll=setInterval(done,50);
    timeout=setTimeout(()=>{cleanup();reject(Error('Seek timed out: requested '+t.toFixed(2)+'s, player at '+v.currentTime.toFixed(2)+'s'));},10000);
    try{
      const host=new URL(location.href).hostname;
      if(host==='netflix.com'||host.endsWith('.netflix.com')){
        // Netflix owns its streaming pipeline. A raw currentTime write can
        // invalidate its playback state; never fall back to that write here.
        chrome.runtime.sendMessage({type:'NetflixSeek',videoId:ids.get(v),time:t,mediaKey:mediaKey(v),source:v.currentSrc,page:location.href})
          .then(result=>{if(!result?.ok){cleanup();reject(Error(result?.error||'Netflix player seek unavailable'));}else done();},e=>{cleanup();reject(e);});
      }else{v.currentTime=t;done();}
    }catch(e){cleanup();reject(e);}
  });
}
function abort(reason){if(!capture)return;const old=capture;capture=null;send({type:'CaptureAborted',operationId:old.operationId,error:reason});}
async function command(m){
  const v=videos.get(m.identity.videoId);if(!v||!v.isConnected)throw Error('Video instance no longer exists');
  if(capture&&m.action!=='abort')abort('New playback command');
  suppress=performance.now()+1000;
  switch(m.action){
    case 'pause':v.pause();break;
    case 'play':await v.play();break;
    case 'seek':await waitSeek(v,m.time);break;
    case 'rate':v.playbackRate=m.rate;break;
    case 'abort':abort('Cancelled');break;
    default:throw Error('Unsupported player command');
  }
  return snapshot(v,m.identity.videoId,'ack');
}
function onMessage(m,sender,respond){
  if(!alive())return;
  const reply=value=>{if(alive())try{respond(value);}catch{dispose();}};
  if(m.type==='InputScope'){shortcutScope=!!m.enabled;reply({ok:true});return;}
  if(m.type==='SkipIntro'){
    const node=globalThis.subtitlePlayerIntroTarget?.();
    if(!node){reply({ok:false,error:'No website Skip Intro control is available'});return;}
    reply({ok:true});setTimeout(()=>node.click(),50);return;
  }
  if(m.type==='EpisodeNavigateEmbedded'){
    try {
      if(window===top||!/(^|\.)miruro\.[a-z]+$/i.test(new URL(document.referrer).hostname))throw Error('Embedded episode navigation requires a Miruro parent');
      const target=globalThis.subtitlePlayerEmbeddedEpisodeTarget(m.direction);
      reply({ok:true,status:'accepted'});setTimeout(()=>target.click(),50);
    } catch(e){reply({ok:false,status:'failed',error:e.message});}
    return;
  }
  if(m.type==='EpisodeNavigate'){
    (async()=>{
      if(window!==top)throw Error('Episode navigation requires the top page');
      const node=await globalThis.subtitlePlayerFindEpisodeTarget(m.direction);
      reply({ok:true,status:'accepted'});
      setTimeout(()=>node.click(),50);
    })().catch(e=>reply({ok:false,status:/unsupported/i.test(e.message)?'unsupported':'failed',error:e.message}));
    return true;
  }
  if(m.type==='ShortcutConfig'){shortcutBindings=(m.bindings||[]).map(b=>b.toLowerCase().split('+').map(x=>keyToken(x.trim())).sort().join('+'));reply({ok:true});return;}
  if(m.type==='DiscoverVideos'){if(m.refreshCaptions){for(const v of videos.values())captionSources.delete(v);window.postMessage({channel:'SubtitlePlayerCaptionTimingV1',request:'refresh'},'*');}publish();reply({ok:true});return;}
  if(m.type==='PlaybackProbe'){const v=videos.get(m.identity.videoId);reply(v?{ok:true,state:snapshot(v,m.identity.videoId,'probe')}:{ok:false,error:'Video replaced'});return;}
  if(m.type==='PlayerCommand'){command(m).then(state=>reply({ok:true,state}),e=>reply({ok:false,error:e.message}));return true;}
  if(m.type==='PrepareCapture'){
    (async()=>{
      if(capture)throw Error('A recording is already running');
      const v=videos.get(m.identity.videoId);if(!v)throw Error('Video replaced');
      suppress=performance.now()+15000;v.pause();v.playbackRate=1;await waitSeek(v,m.start);
      capture={...m,screenshotTime:m.screenshotTime??(m.start+m.end)/2,id:m.identity.videoId,video:v,started:false};
      const r=v.getBoundingClientRect();
      window.postMessage({subtitlePlayerCrop:true,operationId:m.operationId,rect:{x:r.x,y:r.y,width:r.width,height:r.height},viewport:{width:innerWidth,height:innerHeight}},'*');
      return {ok:true};
    })().then(reply,e=>reply({ok:false,error:e.message}));return true;
  }
  if(m.type==='RunCapture'){
    (async()=>{if(!capture||capture.operationId!==m.operationId)throw Error('Capture cancelled');
      suppress=performance.now()+500;await capture.video.play();capture.started=true;
      send({type:'CaptureStarted',operationId:capture.operationId,time:capture.video.currentTime});return {ok:true};
    })().then(reply,e=>reply({ok:false,error:e.message}));return true;
  }
  if(m.type==='ScreenshotReady'){
    const pending=pendingScreenshots.get(m.operationId);
    pendingScreenshots.delete(m.operationId);
    if(capture?.operationId===m.operationId)capture.screenshotPending=false;
    reply({ok:!!pending});return;
  }
  if(m.type==='CaptureCheckpoint'){
    const pending=pendingScreenshots.get(m.operationId);
    reply({ok:!!pending&&videos.get(pending.id)===pending.video&&mediaKey(pending.video)===pending.mediaKey});return;
  }
  if(m.type==='FinishCapture'){
    pendingScreenshots.delete(m.operationId);
    if(capture?.operationId===m.operationId){suppress=performance.now()+500;capture.video.pause();capture=null;}
    reply({ok:true});
  }
}
if(!alive())return;
// Explicit user input invalidates delayed external-recorder playback policies.
function playbackIntervention(event){
  if(!event.isTrusted||typing()||event.ctrlKey||event.altKey||event.metaKey)return;
  for(const [videoId,v]of videos)send({type:'PlaybackIntervention',videoId,mediaKey:mediaKey(v)});
}
listen(window,'pointerdown',playbackIntervention,true);
listen(window,'keydown',playbackIntervention,true);
function reportInputFocus(){send({type:'InputFocus',textInput:typing(),focused:typeof document.hasFocus==='function'&&document.hasFocus()});}
listen(document,'focusin',reportInputFocus,true);listen(document,'focusout',()=>setTimeout(reportInputFocus,0),true);
listen(window,'focus',reportInputFocus,true);listen(window,'blur',reportInputFocus,true);
every(reportInputFocus,1000);reportInputFocus();
listen(window,'keydown',ownShortcut,true);listen(window,'keyup',ownShortcut,true);
chrome.runtime.onMessage.addListener(onMessage);
listeners.push(()=>chrome.runtime.onMessage.removeListener(onMessage));
// Translate video bounds through nested frames, including open shadow roots.
listen(window,'message',e=>{
  const m=e.data;
  if(e.source===window&&m?.channel==='SubtitlePlayerCaptionTimingV1'&&m.page===location.href&&videos.size===1){
    if(m.mediaContext&&window===top&&/(^|\.)netflix\.com$/i.test(new URL(location.href).hostname)){
      const context=m.mediaContext,title=typeof context.title==='string'?context.title.trim().slice(0,300):'';
      if(!title)return;
      const episode=context.kind==='episode'?context.episode:null,season=context.kind==='episode'?context.season:null;
      if(context.kind==='episode'&&(!Number.isInteger(episode)||episode<=0||episode>=10000||!Number.isInteger(season)||season<=0||season>=1000))return;
      const url=new URL(location.href);
      netflixMedia={title,kind:episode?'episode':'movie',episode,season,
        playerVideoId:/^\d+$/.test(String(context.playerVideoId||''))?String(context.playerVideoId):'',
        pageKey:url.origin+url.pathname,pageUrl:url.href,scope:season?'season':'global',
        ...(episode?{seriesKey:url.origin+'/series/'+encodeURIComponent(title.toLowerCase())}:{})};return;
    }
    if(m.preferredPending===true){const [id,v]=videos.entries().next().value;send({type:'CaptionReferencePending',videoId:id,mediaKey:mediaKey(v)});return;}
    if(!Array.isArray(m.intervals)||m.intervals.length<8||m.intervals.length>10000)return;
    if(!m.intervals.every(x=>Array.isArray(x)&&x.length===2&&x.every(Number.isFinite)&&x[0]>=0&&x[1]>x[0]&&x[1]<86400))return;
    const [id,v]=videos.entries().next().value;
    send({type:'CaptionIntervals',videoId:id,mediaKey:mediaKey(v),intervals:m.intervals,referenceId:String(m.referenceId||'loaded').slice(0,128),language:String(m.language||'').slice(0,30),label:String(m.label||'').slice(0,100),priority:Number.isFinite(m.priority)?m.priority:30});return;
  }
  if(!m?.subtitlePlayerCrop)return;
  let r=m.rect;
  if(e.source!==window){
    const frame=roots().flatMap(r=>[...r.querySelectorAll('iframe,frame')]).find(f=>f.contentWindow===e.source);
    if(!frame)return;const f=frame.getBoundingClientRect();
    const sx=f.width/m.viewport.width,sy=f.height/m.viewport.height;
    r={x:f.x+r.x*sx,y:f.y+r.y*sy,width:r.width*sx,height:r.height*sy};
  }
  const next={...m,rect:r,viewport:{width:innerWidth,height:innerHeight}};
  if(window===top)send({...next,type:'CropResult'});else parent.postMessage(next,'*');
});
let lastMediaContext='',lastMediaAt=0;
// A crop belongs to one layout. Do not attach a stale crop after fullscreen/resize.
listen(document,'fullscreenchange',()=>{
  if(capture)abort('Fullscreen changed during capture');
  send({type:'FullscreenChanged',fullscreen:!!document.fullscreenElement});
});
listen(window,'resize',()=>{if(capture)abort('Video layout changed during capture');});
every(()=>{
  const pageMedia=netflixMedia?.pageUrl===location.href?netflixMedia:
    window===top&&typeof subtitlePlayerMediaContext==='function'?subtitlePlayerMediaContext(location.href,document):null;
  if(window===top&&typeof subtitlePlayerMediaContext==='function'){
    const media=pageMedia;
    if(media){const value=JSON.stringify(media);if(value!==lastMediaContext||performance.now()-lastMediaAt>2000){send({type:'MediaContext',...media});lastMediaContext=value;lastMediaAt=performance.now();}}
  }
  if(window===top&&typeof subtitlePlayerEpisodeContext==='function'){
    const context=subtitlePlayerEpisodeContext(location.href)||(pageMedia?.episode&&pageMedia.seriesKey?
      {seriesKey:pageMedia.seriesKey,episode:pageMedia.episode,season:pageMedia.season,scope:pageMedia.scope}:null);
    if(context){
      context.title=pageMedia?.title||document.title;
      context.pageUrl=location.href;
      const value=JSON.stringify(context);
      if(value!==lastEpisode||performance.now()-lastEpisodeAt>2000){
        send({type:'EpisodeContext',...context});lastEpisode=value;lastEpisodeAt=performance.now();
      }
    }
  }
  discover();for(const [id,v]of videos){
    send(snapshot(v,id));
    const tracks=[...v.textTracks].filter(t=>!/english\s*\(?2\)?/i.test(t.label||'')&&/english|german|englisch|deutsch|\b(en|eng|de|deu|ger)\b/i.test(t.language+' '+t.label));
    const priority=t=>(/english|\b(en|eng)\b/i.test(t.label+' '+t.language)?(/(?:\(2\)|english\s*2)/i.test(t.label)?10:0):20)-(t.mode==='showing'?1:0);
    tracks.sort((a,b)=>priority(a)-priority(b));
    const track=tracks.find(t=>t.cues?.length>=8);
    if(track&&sequence%50===0)send({type:'CaptionIntervals',videoId:id,mediaKey:mediaKey(v),referenceId:'native:'+track.language+':'+track.label,language:track.language,priority:priority(track),intervals:[...track.cues].map(c=>[c.startTime,c.endTime])});
    if(capture?.id===id&&capture.started){
      const times=capture.screenshotTimes?.length?capture.screenshotTimes:[capture.screenshotTime];
      const index=capture.screenshotIndex||0;
      if(!capture.screenshotPending&&index<times.length&&v.currentTime>=times[index]){
        capture.screenshotIndex=index+1;
        capture.screenshotRequested=true;capture.screenshotPending=true;
        pendingScreenshots.set(capture.operationId,{id,video:v,mediaKey:mediaKey(v)});
        send({type:'CaptureScreenshot',operationId:capture.operationId,time:v.currentTime});
      }
      send({type:'CaptureClock',operationId:capture.operationId,time:v.currentTime,buffering:v.readyState<3,paused:v.paused});
      if(v.currentTime>=capture.end){
        const r=capture;capture=null;suppress=performance.now()+500;
        // The policy belongs to the frozen clip, never a late Anki callback.
        v.playbackRate=r.previousRate??1;
        if(r.policy==='paused'||(r.policy==='restore'&&r.previousPaused))v.pause();
        send({type:'CaptureBoundary',operationId:r.operationId,time:v.currentTime});
        send(snapshot(v,id,'recording-end'));
      }
    }
  }
  if(videos.size&&sequence%50===0)window.postMessage({channel:'SubtitlePlayerCaptionTimingV1',request:'replay',hasVideo:true},'*');
},100);
// Common custom players expose text without a native TextTrack. Observe it as
// evidence only; these approximate edges do not automatically earn confidence.
every(()=>{for(const [id,v]of videos){
  if(v.seeking)continue;
  const text=roots().flatMap(r=>[...r.querySelectorAll('#subText,[data-part="cue"],.vjs-text-track-display,.player-timedtext')]).map(x=>x.textContent).join('\n').trim();
  const scope=mediaKey(v),previous=captionObservations.get(v);
  if(!previous&&!text){captionObservations.set(v,{scope,text});continue;}
  if(text!==previous?.text||scope!==previous?.scope){send({type:'CaptionObservation',videoId:id,mediaKey:scope,text,time:v.currentTime});captionObservations.set(v,{scope,text});}
}},100);
})();
