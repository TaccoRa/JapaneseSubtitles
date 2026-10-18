const observed=new Map();
let socket=null,selected=null,recording=null,status='Disconnected',heartbeat=null;
let fingerprints=new Map(),captionCache=new Map(),incoming=Promise.resolve();
let connections=Promise.resolve(),reconnectTimer=null,connectionEpoch=0;
let backend='asbplayer',nativeEnabled=false;
let episodeContexts=new Map(),bindingLost=false;
let activeTabId=null,autoEnabled=true,storedToken='',initialized=false,autoTimer=null;
let reconnectDelay=1000;
const discoveries=new Map();
let fullscreenLease=null,fullscreenUpdates=Promise.resolve();
const fullscreenFrames=new Map();
const serializeConnection=task=>{const result=connections.then(task);connections=result.catch(()=>{});return result;};
const key=i=>JSON.stringify(i);
const send=m=>{if(socket?.readyState===WebSocket.OPEN)socket.send(JSON.stringify(m));};
const hash=async value=>[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value)))].map(x=>x.toString(16).padStart(2,'0')).join('');
function publishBinding(){
  send({type:'Bound',identity:selected});
  const context=episodeContexts.get(selected?.tabId);if(context)send({...context,identity:selected});
  for(const caption of captionCache.get(key(selected))||[])send(caption);
}
async function toFrame(type,extra={},identity=selected){
  if(!identity||key(identity)!==key(selected))throw Error('No matching video connected');
  const result=await chrome.tabs.sendMessage(identity.tabId,{...extra,type,identity},{documentId:identity.documentId,frameId:identity.frameId});
  if(!result?.ok)throw Error(result?.error||'Video did not acknowledge');return result;
}
async function offscreen(message){return chrome.runtime.sendMessage({target:'offscreen',...message});}
async function ensureOffscreen(){
  if(!await chrome.offscreen.hasDocument())await chrome.offscreen.createDocument({url:'offscreen.html',reasons:['USER_MEDIA'],justification:'Record connected tab audio and cropped video images locally'});
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
  await disconnect();
  try{
    selected=m.identity;
    autoEnabled=true;storedToken=m.token||storedToken;
    bindingLost=false;
    await chrome.storage.local.set({token:storedToken,selected,autoConnect:true});
    openSocket(storedToken);return {ok:true};
  }catch(error){await disconnect();throw error;}
}
async function disconnect(manual=false){
  fullscreenFrames.clear();await restoreWindowFullscreen();
  if(manual){autoEnabled=false;await chrome.storage.local.set({autoConnect:false});}
  send({type:'Unbound'});await fail('Disconnected');selected=null;
  connectionEpoch++;clearTimeout(reconnectTimer);clearInterval(heartbeat);
  if(socket){socket.onclose=null;socket.close();socket=null;}
  status='Disconnected';nativeEnabled=false;
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
      await chrome.scripting.executeScript({target:{tabId,allFrames:true},files:['episode-context.js','content.js']});
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
  if(backend!=='native')throw Error('asbplayer is selected in SubtitlePlayer. Enable recording with its action button.');
  if(key(m.identity)!==key(selected))throw Error('Connect this video first');
  await ensureOffscreen();
  const streamId=await chrome.tabCapture.getMediaStreamId({targetTabId:selected.tabId});
  const result=await offscreen({type:'ConnectStream',streamId});
  nativeEnabled=!!result?.ok;
  if(!nativeEnabled)throw Error(result?.error||'Cannot capture tab audio');
  send({type:'RecorderState',identity:selected,enabled:true});
  return {ok:true};
}
async function captureScreenshot(m){
  const active=recording;
  if(!active||active.operationId!==m.operationId||active.screenshotStarted)return;
  active.screenshotStarted=true;
  const stillCurrent=()=>recording===active&&key(selected)===key(active.identity);
  const timeout=setTimeout(()=>{if(stillCurrent())fail('Screenshot timed out');},5000);
  try{
    const tab=await chrome.tabs.get(active.identity.tabId);
    if(!tab.active)throw Error('Keep the connected video tab selected for its screenshot');
    const image=await chrome.tabs.captureVisibleTab(tab.windowId,{format:'png'});
    const after=await chrome.tabs.get(active.identity.tabId);
    if(!stillCurrent()||!after.active||after.windowId!==tab.windowId)throw Error('Video tab changed during screenshot');
    // Check the bound document still exists before accepting any captured pixels.
    await toFrame('CaptureCheckpoint',{operationId:active.operationId});
    const result=await offscreen({type:'StoreScreenshot',operationId:active.operationId,image});
    if(!result?.ok)throw Error(result?.error||'Screenshot failed');
    if(stillCurrent())await toFrame('ScreenshotReady',{operationId:active.operationId});
  }catch(error){if(stillCurrent())await fail(error.message);}
  finally{clearTimeout(timeout);}
}
function openSocket(token){
  const epoch=++connectionEpoch;
  clearTimeout(reconnectTimer);
  if(socket){socket.onclose=null;socket.close();}
  status='Connecting to SubtitlePlayer…';
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
        backend=m.backend==='native'?'native':'asbplayer';
        if(backend==='asbplayer')await serializeConnection(async()=>{
          nativeEnabled=false;
          if(await chrome.offscreen.hasDocument())await offscreen({type:'DisconnectStream'});
        });
        send({type:'RecorderState',identity:selected,enabled:nativeEnabled});
        return;
      }
      if(m.type==='PlayerCommand'){
        if(key(m.identity)!==key(selected))throw Error('Stale video identity');
        if(m.action==='abort'){await fail('Cancelled');return;}
        const result=await toFrame('PlayerCommand',m);
        const {mediaKey,...state}=result.state;
        send({type:'CommandAck',operationId:m.operationId,ok:true,state:{...state,identity:selected,fingerprint:observed.get(key(selected))?.fingerprint}});
      }
      if(m.type==='CaptureRequest'){
        if(recording?.operationId===m.operationId)return;
        if(recording)throw Error('Another recording is in progress');
        if(backend!=='native'||(!nativeEnabled&&!m.reuse))throw Error('Click Enable native recording in the SubtitlePlayer extension first');
        if(key(m.identity)!==key(selected))throw Error('Stale video identity');
        const current=observed.get(key(selected));
        if(!current||current.fingerprint!==m.fingerprint)throw Error('Media changed');
        if(!(m.end>m.start&&m.end-m.start<120))throw Error('Invalid recording interval');
        recording={...m,previousRate:current.rate,previousPaused:current.paused};
        await toFrame('PrepareCapture',m.reuse?{...m,start:m.end}:m);
        if(m.reuse){send({type:'CaptureResult',operationId:m.operationId,ok:true,reused:true,identity:selected});return;}
        const prepared=await offscreen({type:'StartRecording',operationId:m.operationId,screenshotTime:m.screenshotTime??(m.start+m.end)/2});
        if(!prepared?.ok)throw Error(prepared?.error||'Recorder failed');
        await toFrame('RunCapture',{operationId:m.operationId});
      }
      if(m.type==='CaptureCommit'){
        if(!recording||recording.operationId!==m.operationId)return;
        const r=recording;
        if(!m.ok){await fail(m.error||'Anki media write failed');return;}
        await toFrame('FinishCapture',{operationId:r.operationId});
        if(recording!==r)throw Error('Capture cancelled');
        await toFrame('PlayerCommand',{action:'rate',rate:r.previousRate});
        if(recording!==r)throw Error('Capture cancelled');
        recording=null;
        const play=m.policy==='play'||(m.policy==='restore'&&!r.previousPaused);
        if(play)await toFrame('PlayerCommand',{action:'play'});
        send({type:'CaptureFinished',operationId:r.operationId,ok:true});
      }
    }catch(e){
      if(recording&&recording.operationId===m.operationId)await fail(e.message);
      else send({type:m.type==='CaptureRequest'?'CaptureResult':'CommandAck',operationId:m.operationId,ok:false,error:e.message});
    }
  };
  ws.onmessage=event=>{incoming=incoming.then(()=>handleMessage(event)).catch(error=>{status='Browser connection: '+error.message;});};
  ws.onclose=event=>{
    if(epoch!==connectionEpoch)return;
    const denied=event.code===1008&&event.reason!=='Already connected';
    status=denied?'Connection not approved. Click Connect to try again.':'Waiting for SubtitlePlayer. Reconnecting automatically…';
    fail('Browser connection lost').catch(()=>{});clearInterval(heartbeat);
    if(!denied){
      reconnectTimer=setTimeout(()=>{if(selected&&epoch===connectionEpoch)openSocket(token);},reconnectDelay);
      reconnectDelay=Math.min(15000,reconnectDelay*2);
    }
  };
  clearInterval(heartbeat);heartbeat=setInterval(()=>send({type:'Heartbeat'}),20000);
}
chrome.runtime.onMessage.addListener((m,sender,reply)=>{
  if(m.target==='offscreen')return;
  if(m.type==='ListVideos'){
    activeTabId=m.tabId;
    discoverTab(m.tabId).then(discoveryError=>reply({videos:[...observed.values()].filter(v=>v.identity.tabId===m.tabId&&Date.now()-v.seen<5000),status,backend,nativeEnabled,discoveryError,autoEnabled}));return true;
  }
  if(m.type==='Connect'){serializeConnection(()=>connect(m)).then(reply,e=>reply({ok:false,error:e.message}));return true;}
  if(m.type==='EnableNativeRecording'){serializeConnection(()=>enableNative(m)).then(reply,e=>reply({ok:false,error:e.message}));return true;}
  if(m.type==='Disconnect'){
    serializeConnection(()=>disconnect(true)).then(reply,e=>reply({ok:false,error:e.message}));return true;
  }
  if(!sender.tab){
    if(m.type==='AudioChunk'&&selected)send({...m,identity:selected});
    if(m.type==='EncodedCapture'){
      if(recording?.operationId===m.operationId)send({...m,type:'CaptureResult',ok:true,identity:selected});
    }
    return;
  }
  const identity={tabId:sender.tab.id,documentId:sender.documentId,frameId:sender.frameId,videoId:m.videoId};
  if(m.type==='FullscreenChanged'){followFullscreen(m.fullscreen,sender);return;}
  if(m.type==='EpisodeContext'&&sender.frameId===0){
    (async()=>{
      const context={type:'EpisodeContext',seriesKey:await hash(m.seriesKey),episode:m.episode,season:m.season,scope:m.scope,title:String(m.title||'').slice(0,300)};
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
      const {mediaKey,...rest}=m;const value={...rest,identity,fingerprint,seen:Date.now()};
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
  if(m.operationId&&recording?.operationId===m.operationId){
    if(sender.tab.id!==selected.tabId||sender.documentId!==selected.documentId)return;
    if(m.type==='CaptureAborted')fail(m.error);
    if(m.type==='CaptureScreenshot')captureScreenshot(m);
    if(m.type==='CaptureClock')offscreen({...m,type:'RecordingClock'});
    if(m.type==='CaptureBoundary'){
      offscreen({type:'StopRecording',operationId:m.operationId,time:m.time}).then(r=>{if(!r?.ok)fail(r?.error||'Encoding failed');},e=>fail(e.message));
    }
  }
  if(m.type==='CaptionIntervals'||m.type==='CaptionObservation')send({...m,identity});
  if(m.type==='CaptionResource'&&/english|german|englisch|deutsch|\b(en|eng|de|deu|ger)\b/i.test(m.language+' '+m.label)){
    (async()=>{try{
      const url=new URL(m.url);if(!['http:','https:'].includes(url.protocol))return;
      const fingerprint=await hash(m.mediaKey),priority=m.priority??0;
      const cached=captionCache.get(key(identity))?.[0];
      if(cached?.fingerprint===fingerprint&&cached.priority<=priority){if(key(identity)===key(selected))send(cached);return;}
      const response=await fetch(url.href);if(!response.ok)throw Error('Caption request failed');
      const text=await response.text();
      const current=observed.get(key(identity));
      if(m.mediaKey&&current&&await hash(m.mediaKey)!==current.fingerprint)return;
      const previous=captionCache.get(key(identity))?.[0];
      if(previous?.fingerprint===fingerprint&&previous.priority<=priority)return;
      if(text.length<4000000&&(text.match(/-->/g)||[]).length>=8){const value={type:'CaptionText',identity,text,language:m.language,priority,fingerprint:await hash(m.mediaKey)};captionCache.set(key(identity),[value]);if(key(identity)===key(selected))send(value);}
    }catch{if(key(identity)===key(selected))send({type:'CaptionUnavailable',identity,error:'Site captions could not be loaded. Check that English captions are enabled.'});}})();
  }
});
chrome.tabs.onActivated.addListener(({tabId})=>{activeTabId=tabId;if(recording&&selected?.tabId!==tabId)fail('Selected tab changed');if(fullscreenLease&&fullscreenLease.tabId!==tabId){fullscreenFrames.clear();fullscreenUpdates=fullscreenUpdates.then(restoreWindowFullscreen);}discoverTab(tabId);scheduleAutoConnect();});
chrome.tabs.onRemoved.addListener(tabId=>{if(tabId===selected?.tabId){fail('Video tab closed');selected=null;fullscreenFrames.clear();fullscreenUpdates=fullscreenUpdates.then(restoreWindowFullscreen);}});
chrome.tabs.onUpdated.addListener((tabId,change)=>{if(tabId===selected?.tabId&&change.status==='loading'){
  fullscreenFrames.clear();fullscreenUpdates=fullscreenUpdates.then(restoreWindowFullscreen);
  bindingLost=true;fail('Video page changed');send({type:'VideoUnavailable',identity:selected});
}});
// A worker restart keeps the same bound document; it never adopts the active tab.
chrome.storage.local.get(['selected','token','autoConnect']).then(async saved=>{
  storedToken=saved.token||'';autoEnabled=saved.autoConnect!==false;initialized=true;
  if(!selected&&saved.selected&&autoEnabled){
  if(await chrome.offscreen.hasDocument()){
    const state=await offscreen({type:'StreamState'});
    nativeEnabled=!!state?.connected;
  }
  selected=saved.selected;openSocket(saved.token);
  }
  const [active]=await chrome.tabs.query({active:true,lastFocusedWindow:true});
  if(active){activeTabId=active.id;await discoverTab(active.id);scheduleAutoConnect();}
}).catch(()=>{});
setInterval(()=>{if(activeTabId!==null&&autoEnabled){discoverTab(activeTabId);scheduleAutoConnect();}},3000);
