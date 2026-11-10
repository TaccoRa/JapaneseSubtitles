const observed=new Map();
// Runs only in the selected Netflix document's MAIN world. This private player
// interface can change: reject safely instead of assigning video.currentTime.
function seekNetflixPlayer(request){
  if(location.href!==request.page)throw Error('Netflix page changed before seeking');
  const host=location.hostname;
  if(host!=='netflix.com'&&!host.endsWith('.netflix.com'))throw Error('Not a Netflix player');
  const videos=[...document.querySelectorAll('video')].filter(v=>v.isConnected&&v.currentSrc===request.source&&v.getBoundingClientRect().width>=240);
  if(videos.length!==1)throw Error('Netflix video is ambiguous or was replaced');
  if(!Number.isFinite(request.time)||request.time<0)throw Error('Invalid Netflix seek time');
  const api=globalThis.netflix?.appContext?.state?.playerApp?.getAPI?.()?.videoPlayer;
  if(!api?.getAllPlayerSessionIds||!api?.getVideoPlayerBySessionId)throw Error('Netflix player controls unavailable. Use Netflix controls and refresh the page.');
  const players=api.getAllPlayerSessionIds().map(id=>api.getVideoPlayerBySessionId(id))
    .filter(p=>typeof p?.seek==='function'&&typeof p?.getCurrentTime==='function'&&Math.abs(p.getCurrentTime()/1000-videos[0].currentTime)<2);
  if(players.length!==1)throw Error('Cannot identify the Netflix playback session safely');
  players[0].seek(Math.round(request.time*1000));
  return {ok:true};
}
const captionFetchStates=new Map();
let socket=null,selected=null,recording=null,status='Disconnected',heartbeat=null;
let fingerprints=new Map(),captionCache=new Map(),incoming=Promise.resolve();
const preferredCaptionPending=new Map();
let connections=Promise.resolve(),reconnectTimer=null,connectionEpoch=0;
let backend='native',nativeEnabled=false;
let recorderRevision=0;
const finishing=new Map(),completedCaptures=new Set();
let offscreenCreation=null;
let episodeContexts=new Map(),bindingLost=false;
const mediaContexts=new Map();
let activeTabId=null,autoEnabled=true,storedToken='',initialized=false,autoTimer=null;
let reconnectDelay=1000;
const discoveries=new Map();
let fullscreenLease=null,fullscreenUpdates=Promise.resolve();
const fullscreenFrames=new Map();
const serializeConnection=task=>{const result=connections.then(task);connections=result.catch(()=>{});return result;};
const key=i=>JSON.stringify(i);
const send=m=>{if(socket?.readyState===WebSocket.OPEN)socket.send(JSON.stringify(m));};
const hash=async value=>[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value)))].map(x=>x.toString(16).padStart(2,'0')).join('');
const inputFrames=new Map();
let inputRequest=0,inputSequence=0,lastInputValue='',lastInputAt=0;
async function publishInputContext(force=false){
  const identity=selected,request=++inputRequest;
  if(!identity)return;
  try{
    const tab=await chrome.tabs.get(identity.tabId);
    const win=await chrome.windows.get(tab.windowId);
    if(request!==inputRequest||key(identity)!==key(selected))return;
    const now=Date.now();
    for(const [frame,value]of inputFrames)if(now-value.seen>2500)inputFrames.delete(frame);
    const focusedFrames=[...inputFrames.values()].filter(value=>value.tabId===identity.tabId&&value.focused);
    const textInput=focusedFrames.some(value=>value.textInput);
    const context={identity,active:!!tab.active,windowFocused:!!win.focused,pageFocused:focusedFrames.length>0,textInput};
    const value=JSON.stringify(context);
    if(force||value!==lastInputValue||now-lastInputAt>=1000){
      lastInputValue=value;lastInputAt=now;
      send({type:'InputContext',...context,sequence:++inputSequence});
      await chrome.tabs.sendMessage(identity.tabId,{type:'InputScope',enabled:context.active&&context.windowFocused&&context.pageFocused&&!textInput}).catch(()=>{});
    }
  }catch{
    if(request===inputRequest&&key(identity)===key(selected))send({type:'InputContext',identity,active:false,windowFocused:false,textInput:false,sequence:++inputSequence});
  }
}
function publishBinding(){
  lastInputValue='';publishInputContext(true);
  toFrame('DiscoverVideos',{refreshCaptions:true}).catch(()=>{});
  send({type:'Bound',identity:selected});
  refreshRecorderState().catch(()=>{});
  const context=episodeContexts.get(selected?.tabId);if(context)send({...context,identity:selected});
  const media=mediaContexts.get(selected?.tabId);if(media)send({...media,identity:selected});
  for(const caption of captionCache.get(key(selected))||[])send(caption);
  if(preferredCaptionPending.has(key(selected)))send({type:'CaptionReferencePending',identity:selected,fingerprint:preferredCaptionPending.get(key(selected))});
}
async function toFrame(type,extra={},identity=selected){
  if(!identity||key(identity)!==key(selected))throw Error('No matching video connected');
  const result=await chrome.tabs.sendMessage(identity.tabId,{...extra,type,identity},{documentId:identity.documentId,frameId:identity.frameId});
  if(!result?.ok)throw Error(result?.error||'Video did not acknowledge');return result;
}
async function offscreen(message){return chrome.runtime.sendMessage({target:'offscreen',...message});}
async function refreshRecorderState(){
  const identity=selected,revision=recorderRevision;
  if(!identity)return false;
  let state;
  try{if(await chrome.offscreen.hasDocument())state=await offscreen({type:'StreamState'});}catch{}
  if(key(identity)!==key(selected)||revision!==recorderRevision)return nativeEnabled;
  nativeEnabled=!!state?.connected&&state.tabId===identity.tabId;
  send({type:'RecorderState',identity,enabled:nativeEnabled,
    reason:nativeEnabled?'':'No live tab audio for this video. Open the extension on this tab once.'});
  return nativeEnabled;
}
async function ensureOffscreen(){
  if(offscreenCreation)return offscreenCreation;
  if(await chrome.offscreen.hasDocument())return;
  // Multiple initialization requests share one offscreen document creation.
  if(!offscreenCreation)offscreenCreation=chrome.offscreen.createDocument({url:'offscreen.html',reasons:['USER_MEDIA'],justification:'Record connected tab audio and cropped video images locally'}).catch(async error=>{
    if(!await chrome.offscreen.hasDocument())throw error;
  }).finally(()=>{offscreenCreation=null;});
  return offscreenCreation;
}
async function fail(reason){
  if(!recording)return;const r=recording;recording=null;
  await offscreen({type:'CancelRecording',operationId:r.operationId}).catch(()=>{});
  await toFrame('FinishCapture',{operationId:r.operationId},r.identity).catch(()=>{});
  // Restore speed, but never resume after a failure or cancellation.
  await toFrame('PlayerCommand',{action:'rate',rate:r.previousRate},r.identity).catch(()=>{});
  send({type:'CaptureResult',operationId:r.operationId,ok:false,error:reason,identity:r.identity});
}
async function connect(m){
  if(selected?.tabId===m.identity.tabId){
    if(recording&&key(selected)!==key(m.identity))throw Error('Finish or cancel the current recording before changing video');
    // The stream belongs to the tab, not its replaceable video/frame. Keep it
    // alive across episode changes and repeated clicks on Connect.
    selected=m.identity;autoEnabled=true;bindingLost=false;
    storedToken=m.token||storedToken;
    await chrome.storage.local.set({token:storedToken,selected,autoConnect:true});
    if(socket?.readyState===WebSocket.OPEN&&status==='Connected')publishBinding();
    else await openSocket(storedToken);
    return {ok:true};
  }
  await disconnect();
  try{
    selected=m.identity;
    autoEnabled=true;storedToken=m.token||storedToken;
    bindingLost=false;
    await chrome.storage.local.set({token:storedToken,selected,autoConnect:true});
    await openSocket(storedToken);return {ok:true};
  }catch(error){await disconnect();throw error;}
}
async function disconnect(manual=false){
  fullscreenFrames.clear();await restoreWindowFullscreen();
  if(manual){autoEnabled=false;await chrome.storage.local.set({autoConnect:false});}
  if(selected)await chrome.tabs.sendMessage(selected.tabId,{type:'ShortcutConfig',bindings:[]}).catch(()=>{});
  send({type:'Unbound'});await fail('Disconnected');selected=null;
  connectionEpoch++;clearTimeout(reconnectTimer);clearInterval(heartbeat);
  if(socket){socket.onclose=null;socket.close();socket=null;}
  status='Disconnected';nativeEnabled=false;recorderRevision++;
  await chrome.storage.local.set({selected:null});
  if(await chrome.offscreen.hasDocument()){
    const result=await offscreen({type:'DisconnectStream'});
    if(!result?.ok)throw Error(result?.error||'Could not release tab audio');
  }
  return {ok:true};
}
async function restoreWindowFullscreen(){
  const lease=fullscreenLease;fullscreenLease=null;
  if(!lease)return;
  try{
    const window=await chrome.windows.get(lease.windowId);
    // A manual F11/restore already superseded our fullscreen change.
    if(window.state==='fullscreen')await chrome.windows.update(lease.windowId,{state:lease.previousState});
  }catch{}
}
function followFullscreen(fullscreen,sender){
  if(!selected||sender.tab?.id!==selected.tabId||
     (sender.frameId!==0&&sender.documentId!==selected.documentId)||typeof fullscreen!=='boolean')return;
  const frame=sender.frameId+':'+sender.documentId;
  if(fullscreenFrames.get(frame)===fullscreen)return;
  fullscreenFrames.set(frame,fullscreen);
  const identity=selected;
  fullscreenUpdates=fullscreenUpdates.then(async()=>{
    if(key(identity)!==key(selected))return;
    if(![...fullscreenFrames.values()].some(Boolean)){await restoreWindowFullscreen();return;}
    if(fullscreenLease)return;
    const tab=await chrome.tabs.get(identity.tabId);
    if(!tab.active)return;
    const window=await chrome.windows.get(tab.windowId);
    if(key(identity)!==key(selected)||![...fullscreenFrames.values()].some(Boolean))return;
    // Preserve browser fullscreen that the user enabled before the player did.
    if(window.state==='fullscreen'||window.type!=='normal')return;
    fullscreenLease={tabId:identity.tabId,windowId:tab.windowId,previousState:window.state==='maximized'?'maximized':'normal'};
    await chrome.windows.update(tab.windowId,{state:'fullscreen'});
  }).catch(error=>{fullscreenLease=null;status='Fullscreen: '+error.message;});
}
async function discoverTab(tabId){
  const previous=discoveries.get(tabId);
  if(previous&&Date.now()-previous.at<3000)return previous.promise;
  const promise=(async()=>{
    try{
      // Reinject into existing and newly replaced nested frames. The content
      // script is idempotent and republishes clocks without changing video IDs.
      await chrome.scripting.executeScript({target:{tabId,allFrames:true},world:'MAIN',files:['caption-observer.js']});
      await chrome.scripting.executeScript({target:{tabId,allFrames:true},files:['episode-context.js','episode-navigation.js','content.js']});
      await chrome.tabs.sendMessage(tabId,{type:'DiscoverVideos'}).catch(()=>{});
      return null;
    }catch(error){return error.message;}
  })();
  discoveries.set(tabId,{at:Date.now(),promise});return promise;
}
function scheduleAutoConnect(){
  if(autoTimer||!initialized||!autoEnabled||activeTabId===null)return;
  autoTimer=setTimeout(()=>{
    autoTimer=null;
    if(!autoEnabled||recording)return;
    const candidates=[...observed.values()].filter(v=>v.identity.tabId===activeTabId&&Date.now()-v.seen<5000&&v.width>=240&&v.height>=120);
    // Prefer the watching video over tiny previews. Ambiguous equal-sized
    // players remain selectable in the popup instead of guessing.
    candidates.sort((a,b)=>b.width*b.height-a.width*a.height);
    const next=candidates[0];
    if(!next||(candidates[1]&&next.width*next.height<candidates[1].width*candidates[1].height*1.5)||key(next.identity)===key(selected))return;
    serializeConnection(()=>connect({identity:next.identity,token:storedToken})).catch(error=>{status=error.message;});
  },300);
}
async function enableNative(m){
  if(key(m.identity)!==key(selected))throw Error('Connect this video first');
  recorderRevision++;
  await ensureOffscreen();
  const current=await offscreen({type:'StreamState'});
  if(current?.connected&&current.tabId===selected.tabId){
    nativeEnabled=true;send({type:'RecorderState',identity:selected,enabled:true});return {ok:true};
  }
  const streamId=await chrome.tabCapture.getMediaStreamId({targetTabId:selected.tabId});
  const result=await offscreen({type:'ConnectStream',streamId,tabId:selected.tabId});
  nativeEnabled=!!result?.ok;
  if(!nativeEnabled)throw Error(result?.error||'Cannot capture tab audio');
  send({type:'RecorderState',identity:selected,enabled:true});
  return {ok:true};
}
async function captureScreenshot(m){
  const active=recording;
  if(!active||active.operationId!==m.operationId||active.screenshotStarted)return;
  active.screenshotStarted=true;
  const stillCurrent=()=>(recording===active||finishing.get(active.operationId)===active)&&key(selected)===key(active.identity);
  const failThis=async reason=>{
    if(recording===active)return fail(reason);
    if(finishing.get(active.operationId)!==active)return;
    finishing.delete(active.operationId);
    await offscreen({type:'CancelRecording',operationId:active.operationId}).catch(()=>{});
    await toFrame('FinishCapture',{operationId:active.operationId},active.identity).catch(()=>{});
    send({type:'CaptureResult',operationId:active.operationId,identity:active.identity,ok:false,error:reason});
  };
  const timeout=setTimeout(()=>{failThis('Screenshot timed out').catch(()=>{});},5000);
  const cleanFrames=async restore=>chrome.scripting.executeScript({target:{tabId:active.identity.tabId,allFrames:true},
    func:async(operationId,videoId,restore)=>{
      const content=globalThis.subtitlePlayerContent;
      return restore?content?.restoreScreenshot?.(operationId):await content?.prepareScreenshot?.(operationId,videoId);
    },args:[active.operationId,active.identity.videoId,restore]});
  try{
    const tab=await chrome.tabs.get(active.identity.tabId);
    if(!tab.active)throw Error('Keep the connected video tab selected for its screenshot');
    try{
      const frames=await cleanFrames(false);
      if(!frames.some(frame=>frame.result?.hidden))console.info('Screenshot: no recognized controls; unknown player UI may remain visible');
      if(frames.some(frame=>frame.result?.hidden&&!frame.result.painted))console.info('Screenshot: a frame could not confirm control repaint');
    }catch(error){console.info('Screenshot control cleanup unavailable:',error.message);}
    if(!stillCurrent())throw Error('Capture cancelled before screenshot');
    const image=await chrome.tabs.captureVisibleTab(tab.windowId,{format:'png'});
    const after=await chrome.tabs.get(active.identity.tabId);
    if(!stillCurrent()||!after.active||after.windowId!==tab.windowId)throw Error('Video tab changed during screenshot');
    // Check the bound document still exists before accepting any captured pixels.
    await toFrame('CaptureCheckpoint',{operationId:active.operationId});
    const result=await offscreen({type:'StoreScreenshot',operationId:active.operationId,image});
    if(!result?.ok)throw Error(result?.error||'Screenshot failed');
    if(stillCurrent())await toFrame('ScreenshotReady',{operationId:active.operationId});
  }catch(error){await failThis(error.message);}
  finally{clearTimeout(timeout);await cleanFrames(true).catch(()=>{});active.screenshotStarted=false;}
}
function scheduleReconnect(epoch,token){
  if(epoch!==connectionEpoch)return;
  status='Waiting for SubtitlePlayer. Start the desktop app; reconnection is automatic.';
  clearTimeout(reconnectTimer);
  reconnectTimer=setTimeout(()=>{if(selected&&epoch===connectionEpoch)openSocket(token);},reconnectDelay);
  reconnectDelay=Math.min(15000,reconnectDelay*2);
}
async function openSocket(token){
  const epoch=++connectionEpoch;
  clearTimeout(reconnectTimer);clearInterval(heartbeat);
  if(socket){socket.onclose=null;socket.close();socket=null;}
  status='Connecting to SubtitlePlayer…';
  try{
    const response=await fetch('http://127.0.0.1:8767/health',{cache:'no-store',redirect:'error',signal:AbortSignal.timeout(1500)});
    const health=response.ok&&await response.json();
    if(!health||health.service!=='SubtitlePlayer'||health.version!==1)throw Error('Desktop bridge is not ready');
  }catch{scheduleReconnect(epoch,token);return;}
  if(epoch!==connectionEpoch||!selected)return;
  const ws=new WebSocket('ws://127.0.0.1:8767');socket=ws;
  ws.onopen=()=>{if(epoch===connectionEpoch)ws.send(JSON.stringify({type:'Hello',version:1,token,pair:true}));};
  ws.onerror=()=>{if(epoch===connectionEpoch)status='Waiting for SubtitlePlayer. Start the desktop app; reconnection is automatic.';};
  const handleMessage=async({data})=>{
    if(epoch!==connectionEpoch)return;
    const m=JSON.parse(data);
    try{
      if(m.type==='Welcome'){reconnectDelay=1000;if(m.token){token=m.token;storedToken=token;await chrome.storage.local.set({token});}status='Connected';publishBinding();return;}
      if(m.type==='TimingMap')return;
      if(m.type==='RecorderConfig'){
        backend='native';
        await refreshRecorderState();
        return;
      }
      if(m.type==='RecorderProbe'){
        if(key(m.identity)===key(selected))await refreshRecorderState();
        return;
      }
      if(m.type==='RefreshCaptions'){
        if(key(m.identity)!==key(selected))return;
        await toFrame('DiscoverVideos',{refreshCaptions:true});
        return;
      }
      if(m.type==='ShortcutConfig'){
        if(key(m.identity)!==key(selected))return;
        await chrome.tabs.sendMessage(selected.tabId,{type:'ShortcutConfig',bindings:m.bindings});return;
      }
      if(m.type==='PlaybackProbe'){
        if(key(m.identity)!==key(selected))throw Error('Stale video identity');
        const identity=selected,fingerprint=observed.get(key(identity))?.fingerprint;
        const result=await toFrame('PlaybackProbe',{},identity);
        if(key(identity)!==key(selected)||observed.get(key(identity))?.fingerprint!==fingerprint)return;
        const {mediaKey,resourceKey,playerKey,...state}=result.state;
        const resourceFingerprint=resourceKey?await hash(resourceKey):'';
        const playerFingerprint=playerKey?await hash(playerKey):'';
        send({...state,type:'PlaybackSnapshot',identity,fingerprint,resourceFingerprint,playerFingerprint});
        return;
      }
      if(m.type==='PlayerCommand'){
        if(key(m.identity)!==key(selected))throw Error('Stale video identity');
        if(m.action==='abort'){await fail('Cancelled');return;}
        const identity=selected,fingerprint=observed.get(key(identity))?.fingerprint;
        const result=await toFrame('PlayerCommand',m,identity);
        if(key(identity)!==key(selected)||observed.get(key(identity))?.fingerprint!==fingerprint)throw Error('Video changed before command completed');
        const {mediaKey,resourceKey,playerKey,...state}=result.state;
        const resourceFingerprint=resourceKey?await hash(resourceKey):'';
        const playerFingerprint=playerKey?await hash(playerKey):'';
        send({type:'CommandAck',operationId:m.operationId,ok:true,state:{...state,identity,fingerprint,resourceFingerprint,playerFingerprint}});
      }
      if(m.type==='EpisodeNavigate'){
        if(key(m.identity)!==key(selected))throw Error('Stale video identity');
        if(recording||finishing.size)throw Error('Wait for recording to finish before changing episodes');
        let result=await chrome.tabs.sendMessage(selected.tabId,{type:'EpisodeNavigate',direction:m.direction},{frameId:0});
        if(!result?.ok&&result?.status==='failed'&&selected.frameId!==0){
          result=await toFrame('EpisodeNavigateEmbedded',{direction:m.direction});
        }
        send({type:'EpisodeNavigationAck',operationId:m.operationId,...result});
        return;
      }
      if(m.type==='SkipIntro'){
        if(key(m.identity)!==key(selected)||observed.get(key(selected))?.fingerprint!==m.fingerprint)throw Error('Stale video identity');
        if(recording||finishing.size)throw Error('Wait for recording to finish before skipping the intro');
        const identity=selected;
        let result=await chrome.tabs.sendMessage(identity.tabId,{type:'SkipIntro'},{frameId:0});
        if(!result?.ok&&identity.frameId!==0)result=await toFrame('SkipIntro',{},identity);
        if(key(identity)!==key(selected))throw Error('Video changed during intro skip');
        send({type:'SkipIntroAck',operationId:m.operationId,...result});return;
      }
      if(m.type==='CaptureRequest'){
        if(recording?.operationId===m.operationId||finishing.has(m.operationId)||completedCaptures.has(m.operationId))return;
        if(recording)throw Error('Another recording is in progress');
        if(key(m.identity)!==key(selected))throw Error('Stale video identity');
        if(!await refreshRecorderState())throw Error('No live native audio for this tab. Open the SubtitlePlayer extension once.');
        if(key(m.identity)!==key(selected))throw Error('Video changed while checking the recorder');
        const current=observed.get(key(selected));
        if(!current||current.fingerprint!==m.fingerprint)throw Error('Media changed');
        if(!(m.end>m.start&&m.end-m.start<120))throw Error('Invalid recording interval');
        recording={...m,previousRate:current.rate,previousPaused:m.previousPaused??current.paused};
        await toFrame('PrepareCapture',{...m,previousRate:current.rate,previousPaused:m.previousPaused??current.paused});
        const prepared=await offscreen({type:'StartRecording',operationId:m.operationId,screenshotCount:m.screenshotTimes?.length||1,screenshotTime:m.screenshotTime??(m.start+m.end)/2});
        if(!prepared?.ok)throw Error(prepared?.error||'Recorder failed');
        await toFrame('RunCapture',{operationId:m.operationId});
      }
      if(m.type==='CaptureCommit'){
        // Attachment completion has no ownership of the player.
        if(!finishing.has(m.operationId))return;
        finishing.delete(m.operationId);completedCaptures.add(m.operationId);
        if(completedCaptures.size>512)completedCaptures.delete(completedCaptures.values().next().value);
        send({type:'CaptureFinished',operationId:m.operationId,ok:!!m.ok});
      }
    }catch(e){
      if(recording&&recording.operationId===m.operationId)await fail(e.message);
      else send({type:m.type==='SkipIntro'?'SkipIntroAck':m.type==='EpisodeNavigate'?'EpisodeNavigationAck':m.type==='CaptureRequest'?'CaptureResult':'CommandAck',operationId:m.operationId,ok:false,status:'failed',error:e.message});
    }
  };
  ws.onmessage=event=>{incoming=incoming.then(()=>handleMessage(event)).catch(error=>{status='Browser connection: '+error.message;});};
  ws.onclose=event=>{
    if(epoch!==connectionEpoch)return;
    const denied=event.code===1008&&event.reason!=='Already connected';
    status=denied?'Connection not approved. Click Connect to try again.':'Waiting for SubtitlePlayer. Reconnecting automatically…';
    if(selected)chrome.tabs.sendMessage(selected.tabId,{type:'ShortcutConfig',bindings:[]}).catch(()=>{});
    fail('Browser connection lost').catch(()=>{});clearInterval(heartbeat);
    if(!denied)scheduleReconnect(epoch,token);
  };
  clearInterval(heartbeat);heartbeat=setInterval(()=>send({type:'Heartbeat'}),20000);
}
chrome.runtime.onMessage.addListener((m,sender,reply)=>{
  if(m.target==='offscreen')return;
  if(m.type==='ListVideos'){
    activeTabId=m.tabId;
    (async()=>{const discoveryError=await discoverTab(m.tabId);await refreshRecorderState();reply({videos:[...observed.values()].filter(v=>v.identity.tabId===m.tabId&&Date.now()-v.seen<5000),selected,status,backend,nativeEnabled,discoveryError,autoEnabled});})();return true;
  }
  if(m.type==='Connect'){serializeConnection(()=>connect(m)).then(reply,e=>reply({ok:false,error:e.message}));return true;}
  if(m.type==='EnableNativeRecording'){serializeConnection(()=>enableNative(m)).then(reply,e=>reply({ok:false,error:e.message}));return true;}
  if(m.type==='Disconnect'){
    serializeConnection(()=>disconnect(true)).then(reply,e=>reply({ok:false,error:e.message}));return true;
  }
  if(!sender.tab){
    if(m.type==='NativeRecordingStopped'&&m.tabId===selected?.tabId){
      // A delayed ended event from a replaced stream must not disable its live
      // replacement or cancel that replacement's recording.
      refreshRecorderState().then(enabled=>{
        if(!enabled&&m.tabId===selected?.tabId)return fail('Tab audio stopped. Open the SubtitlePlayer extension to enable it again.');
      }).catch(()=>{});
    }
    if(m.type==='AudioChunk'&&selected)send({...m,identity:selected});
    if(m.type==='EncodedCapture'){
      const r=finishing.get(m.operationId);
      if(r)send({...m,type:'CaptureResult',ok:true,identity:r.identity});
    }
    return;
  }
  if(m.type==='InputFocus'){
    if(sender.tab.id===selected?.tabId){
      const frame=sender.tab.id+':'+sender.frameId,prior=inputFrames.get(frame);
      inputFrames.set(frame,{tabId:sender.tab.id,focused:!!m.focused,textInput:!!m.textInput,seen:Date.now()});
      publishInputContext(!prior||prior.focused!==!!m.focused||prior.textInput!==!!m.textInput);
    }
    return;
  }
  const identity={tabId:sender.tab.id,documentId:sender.documentId,frameId:sender.frameId,videoId:m.videoId};
  if(m.type==='NetflixSeek'){
    (async()=>{
      if(key(identity)!==key(selected)||!sender.documentId)throw Error('Netflix video binding changed');
      const url=new URL(sender.url||'');
      if(url.protocol!=='https:'||(url.hostname!=='netflix.com'&&!url.hostname.endsWith('.netflix.com')))throw Error('Invalid Netflix seek sender');
      if(!Number.isFinite(m.time)||m.time<0)throw Error('Invalid Netflix seek time');
      const fingerprint=await hash(m.mediaKey);
      if(key(identity)!==key(selected)||observed.get(key(identity))?.fingerprint!==fingerprint)throw Error('Netflix episode changed');
      const result=await chrome.scripting.executeScript({target:{tabId:sender.tab.id,documentIds:[sender.documentId]},world:'MAIN',func:seekNetflixPlayer,args:[{time:m.time,source:m.source,page:m.page}]});
      return result?.[0]?.result||{ok:false,error:'Netflix player seek unavailable'};
    })().then(reply,e=>reply({ok:false,error:e.message}));return true;
  }
  if(m.type==='FullscreenChanged'){followFullscreen(m.fullscreen,sender);return;}
  if(m.type==='MediaContext'&&sender.frameId===0){
    if(!['movie','episode'].includes(m.kind)||!m.title)return;
    const context={type:'MediaContext',title:String(m.title).slice(0,300),kind:m.kind,episode:m.episode,season:m.season,pageKey:String(m.pageKey||'').slice(0,1000),pageUrl:String(m.pageUrl||'').slice(0,2000),scope:m.scope};
    mediaContexts.set(sender.tab.id,context);
    if(selected?.tabId===sender.tab.id)send({...context,identity:selected});
    return;
  }
  if(m.type==='EpisodeContext'&&sender.frameId===0){
    (async()=>{
      const pageUrl=String(m.pageUrl||'').slice(0,2000);
      const context={type:'EpisodeContext',seriesKey:await hash(m.seriesKey),episode:m.episode,season:m.season,scope:m.scope,title:String(m.title||'').slice(0,300),pageUrl};
      const media=mediaContexts.get(sender.tab.id);
      if(media?.pageUrl&&pageUrl&&media.pageUrl!==pageUrl)return;
      episodeContexts.set(sender.tab.id,context);
      if(selected?.tabId===sender.tab.id)send({...context,identity:selected});
    })();return;
  }
  if(m.type==='VideoRemoved'&&key(identity)===key(selected)){
    (async()=>{await fail('Video replaced');bindingLost=true;send({type:'VideoUnavailable',identity:selected});if(m.replacementVideoId){selected={...selected,videoId:m.replacementVideoId};bindingLost=false;await chrome.storage.local.set({selected});publishBinding();}})();return;
  }
  if(m.type==='PlaybackSnapshot'){
    (async()=>{
      let fingerprint=fingerprints.get(m.mediaKey);if(!fingerprint){fingerprint=await hash(m.mediaKey);fingerprints.set(m.mediaKey,fingerprint);}
      const {mediaKey,resourceKey,playerKey,...rest}=m;
      const resourceFingerprint=resourceKey?await hash(resourceKey):'';
        const playerFingerprint=playerKey?await hash(playerKey):'';
      const value={...rest,identity,fingerprint,resourceFingerprint,playerFingerprint,seen:Date.now()};
      const prior=observed.get(key(identity));if(prior&&prior.sequence>=m.sequence)return;
      observed.set(key(identity),value);
      scheduleAutoConnect();
      if(prior&&prior.fingerprint!==fingerprint)captionCache.delete(key(identity));
      // Follow replacement players only inside the tab the user already bound.
      // Keep every command scoped to the new document/frame/video identity.
      if(selected&&identity.tabId===selected.tabId&&key(identity)!==key(selected)&&value.width>=240&&value.height>=120&&
         (bindingLost||(identity.frameId===selected.frameId&&identity.documentId!==selected.documentId)||Date.now()-(observed.get(key(selected))?.seen||0)>500)){
        const candidates=[...observed.values()].filter(v=>v.identity.tabId===selected.tabId&&Date.now()-v.seen<500&&v.width>=240&&v.height>=120&&key(v.identity)!==key(selected));
        if(candidates.length===1){
          await fail('Video replaced');
          fullscreenFrames.delete(selected.frameId+':'+selected.documentId);
          selected=identity;bindingLost=false;
          await chrome.storage.local.set({selected});publishBinding();
        }
      }
      if(key(identity)===key(selected)){
        followFullscreen(m.fullscreen,sender);
        if(bindingLost){bindingLost=false;publishBinding();}
        send(value);
        offscreen({...value,type:'AnalysisClock'}).catch(()=>{});
        if(recording&&recording.fingerprint!==fingerprint)await fail('Media or audio track changed');
      }
    })();return;
  }
  if(m.type==='CropResult'){
    if(recording?.operationId===m.operationId&&sender.tab.id===selected.tabId)offscreen({...m,type:'SetCrop'});return;
  }
  if(key(identity)!==key(selected)&&!['CaptureClock','CaptureBoundary','CaptureAborted','CaptureScreenshot','CaptionResource'].includes(m.type))return;
  if(m.type==='PlaybackIntervention'){
    const current=observed.get(key(identity));
    if(current)send({type:'PlaybackIntervention',identity,fingerprint:current.fingerprint});
    return;
  }
  if(m.operationId&&recording?.operationId===m.operationId){
    if(sender.tab.id!==selected.tabId||sender.documentId!==selected.documentId)return;
    if(m.type==='CaptureAborted')fail(m.error);
    if(m.type==='CaptureScreenshot')captureScreenshot(m);
    if(m.type==='CaptureClock')offscreen({...m,type:'RecordingClock'});
    if(m.type==='CaptureStarted')send({type:'CaptureStarted',operationId:m.operationId,identity:recording.identity,time:m.time});
    if(m.type==='CaptureBoundary'){
      const r=recording;
      // Detach player ownership now. Encoding/Anki may finish after another clip.
      finishing.set(r.operationId,r);recording=null;
      send({type:'RecordingEnded',operationId:r.operationId,identity:r.identity,time:m.time});
      offscreen({type:'StopRecording',operationId:m.operationId,time:m.time}).then(result=>{
        if(!result?.ok){finishing.delete(r.operationId);send({type:'CaptureResult',operationId:r.operationId,identity:r.identity,ok:false,error:result?.error||'Encoding failed'});}
      },e=>{finishing.delete(r.operationId);send({type:'CaptureResult',operationId:r.operationId,identity:r.identity,ok:false,error:e.message});});
    }
  }
  if(m.type==='CaptionIntervals'||m.type==='CaptionObservation'||m.type==='CaptionReset'||m.type==='CaptionReferencePending'){
    (async()=>{
      const fingerprint=m.mediaKey?await hash(m.mediaKey):observed.get(key(identity))?.fingerprint;
      if(!fingerprint||key(identity)!==key(selected)||observed.get(key(identity))?.fingerprint!==fingerprint)return;
      const {mediaKey,...rest}=m;
      if(m.type==='CaptionReferencePending'){captionCache.delete(key(identity));preferredCaptionPending.set(key(identity),fingerprint);send({...rest,identity,fingerprint});return;}
      if(m.type!=='CaptionIntervals'){send({...rest,identity,fingerprint});return;}
      if(!Array.isArray(m.intervals)||m.intervals.length<8||m.intervals.length>10000)return;
      if(!m.intervals.every(x=>Array.isArray(x)&&x.length===2&&x.every(Number.isFinite)&&x[0]>=0&&x[1]>x[0]&&x[1]<86400))return;
      let intervals=m.intervals.map(x=>[...x]).sort((a,b)=>a[0]-b[0]);
      const priority=Number.isFinite(m.priority)?m.priority:0,previous=captionCache.get(key(identity))?.[0];
      if(preferredCaptionPending.get(key(identity))===fingerprint&&priority>0)return;
      if(previous?.fingerprint===fingerprint&&previous.priority<priority)return;
      const referenceId=String(m.referenceId||'native').slice(0,128);
      if(previous?.type==='CaptionIntervals'&&previous.fingerprint===fingerprint&&previous.priority===priority&&previous.language===m.language&&previous.referenceId===referenceId){
        intervals=[...new Map([...previous.intervals,...intervals].map(x=>[JSON.stringify(x),x])).values()].sort((a,b)=>a[0]-b[0]).slice(0,10000);
        if(JSON.stringify(intervals)===JSON.stringify(previous.intervals))return;
      }
      const value={type:'CaptionIntervals',identity,fingerprint,language:m.language,label:m.label,priority,referenceId,intervals};
      preferredCaptionPending.delete(key(identity));
      captionCache.set(key(identity),[value]);send(value);
    })();return;
  }
  if(m.type==='CaptionResource'&&!/english\s*\(?2\)?/i.test(m.label||'')&&/english|german|englisch|deutsch|\b(en|eng|de|deu|ger)\b/i.test(m.language+' '+m.label)){
    if(key(identity)!==key(selected))return;
    (async()=>{let fetchState,hostname='';try{
      const url=new URL(m.url);if(!['http:','https:'].includes(url.protocol))return;
      hostname=url.hostname;
      const fingerprint=await hash(m.mediaKey),priority=m.priority??0;
      if(preferredCaptionPending.get(key(identity))===fingerprint&&priority>0)return;
      if(key(identity)!==key(selected)||observed.get(key(identity))?.fingerprint!==fingerprint)return;
      const scope=key(identity)+'|'+fingerprint;
      fetchState=captionFetchStates.get(scope);
      if(!fetchState){
        fetchState={failed:false,attempted:new Set()};captionFetchStates.set(scope,fetchState);
        if(captionFetchStates.size>128)captionFetchStates.delete(captionFetchStates.keys().next().value);
      }
      // A denied resource must not become an endless browser/antivirus alert loop.
      // Claim before awaiting so repeated announcements cannot duplicate requests.
      if(fetchState.failed||fetchState.attempted.has(url.href))return;
      const cached=captionCache.get(key(identity))?.[0];
      if(cached?.fingerprint===fingerprint&&cached.priority<=priority){if(key(identity)===key(selected))send(cached);return;}
      fetchState.attempted.add(url.href);
      const response=await fetch(url.href,{redirect:'error',credentials:'omit',referrerPolicy:'no-referrer',signal:AbortSignal.timeout(5000)});if(!response.ok)throw Error('Caption request failed');
      const text=await response.text();
      if(key(identity)!==key(selected))return;
      const current=observed.get(key(identity));
      if(m.mediaKey&&current&&await hash(m.mediaKey)!==current.fingerprint)return;
      const previous=captionCache.get(key(identity))?.[0];
      if(preferredCaptionPending.get(key(identity))===fingerprint&&priority>0)return;
      if(previous?.fingerprint===fingerprint&&previous.priority<=priority)return;
      if(text.length<4000000&&(text.match(/-->/g)||[]).length>=8){const value={type:'CaptionText',identity,text,language:m.language,priority,fingerprint:await hash(m.mediaKey)};captionCache.set(key(identity),[value]);if(key(identity)===key(selected))send(value);}
    }catch{
      if(fetchState)fetchState.failed=true;
      if(key(identity)===key(selected))send({type:'CaptionUnavailable',identity,error:'External subtitle request failed'+(hostname?' ('+hostname+')':'')+'. Automatic URL requests stopped for this video. Enable English/German site captions to use already loaded timing.'});
    }})();
  }
});
chrome.windows.onFocusChanged?.addListener(()=>{publishInputContext(true);});
chrome.tabs.onActivated.addListener(({tabId})=>{activeTabId=tabId;publishInputContext(true);if(recording&&selected?.tabId!==tabId)fail('Selected tab changed');if(fullscreenLease&&fullscreenLease.tabId!==tabId){fullscreenFrames.clear();fullscreenUpdates=fullscreenUpdates.then(restoreWindowFullscreen);}discoverTab(tabId);scheduleAutoConnect();});
chrome.tabs.onRemoved.addListener(tabId=>{if(tabId===selected?.tabId){fail('Video tab closed');selected=null;fullscreenFrames.clear();fullscreenUpdates=fullscreenUpdates.then(restoreWindowFullscreen);}});
chrome.tabs.onUpdated.addListener((tabId,change)=>{if(change.status==='loading'){mediaContexts.delete(tabId);episodeContexts.delete(tabId);for(const [frame,value]of inputFrames)if(value.tabId===tabId)inputFrames.delete(frame);publishInputContext();}if(tabId===selected?.tabId&&change.status==='loading'){
  fullscreenFrames.clear();fullscreenUpdates=fullscreenUpdates.then(restoreWindowFullscreen);
  bindingLost=true;fail('Video page changed');send({type:'VideoUnavailable',identity:selected});
}});
// A worker restart keeps the same bound document; it never adopts the active tab.
chrome.storage.local.get(['selected','token','autoConnect']).then(async saved=>{
  storedToken=saved.token||'';autoEnabled=saved.autoConnect!==false;initialized=true;
  if(!selected&&saved.selected&&autoEnabled){
  let restoredNative=false;
  if(await chrome.offscreen.hasDocument()){
    const state=await offscreen({type:'StreamState'});
    restoredNative=!!state?.connected&&state.tabId===saved.selected.tabId;
  }
  if(!selected){nativeEnabled=restoredNative;selected=saved.selected;openSocket(saved.token);}
  }
  const [active]=await chrome.tabs.query({active:true,lastFocusedWindow:true});
  if(active){activeTabId=active.id;await discoverTab(active.id);scheduleAutoConnect();}
}).catch(()=>{});
setInterval(()=>{if(activeTabId!==null&&autoEnabled){discoverTab(activeTabId);scheduleAutoConnect();}},3000);
