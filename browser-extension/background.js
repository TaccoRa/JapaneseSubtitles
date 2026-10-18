const observed=new Map();
let socket=null,selected=null,recording=null,status='Disconnected',heartbeat=null;
let fingerprints=new Map(),captionCache=new Map(),incoming=Promise.resolve();
const key=i=>JSON.stringify(i);
const send=m=>{if(socket?.readyState===WebSocket.OPEN)socket.send(JSON.stringify(m));};
const hash=async value=>[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value)))].map(x=>x.toString(16).padStart(2,'0')).join('');
async function toFrame(type,extra={}){
  if(!selected)throw Error('No video connected');
  const result=await chrome.tabs.sendMessage(selected.tabId,{...extra,type,identity:selected},{documentId:selected.documentId,frameId:selected.frameId});
  if(!result?.ok)throw Error(result?.error||'Video did not acknowledge');return result;
}
async function offscreen(message){return chrome.runtime.sendMessage({target:'offscreen',...message});}
async function ensureOffscreen(){
  if(!await chrome.offscreen.hasDocument())await chrome.offscreen.createDocument({url:'offscreen.html',reasons:['USER_MEDIA'],justification:'Record connected tab audio and cropped video images locally'});
}
async function fail(reason){
  if(!recording)return;const r=recording;recording=null;
  await offscreen({type:'CancelRecording',operationId:r.operationId}).catch(()=>{});
  await toFrame('FinishCapture',{operationId:r.operationId}).catch(()=>{});
  // Restore speed, but never resume after a failure or cancellation.
  await toFrame('PlayerCommand',{action:'rate',rate:r.previousRate}).catch(()=>{});
  send({type:'CaptureResult',operationId:r.operationId,ok:false,error:reason,identity:r.identity});
}
async function connect(m){
  if(recording)await fail('Connection changed');
  selected=m.identity;
  await ensureOffscreen();
  const streamId=await chrome.tabCapture.getMediaStreamId({targetTabId:selected.tabId});
  const result=await offscreen({type:'ConnectStream',streamId});
  if(!result?.ok)throw Error(result?.error||'Cannot capture this tab');
  await chrome.storage.local.set({token:m.token,selected});
  openSocket(m.token);return {ok:true};
}
function openSocket(token){
  if(socket){socket.onclose=null;socket.close();}
  socket=new WebSocket('ws://127.0.0.1:8767');
  socket.onopen=()=>socket.send(JSON.stringify({type:'Hello',version:1,token}));
  const handleMessage=async({data})=>{
    const m=JSON.parse(data);
    try{
      if(m.type==='Welcome'){status='Connected';send({type:'Bound',identity:selected});for(const c of captionCache.get(key(selected))||[])send(c);return;}
      if(m.type==='TimingMap')return;
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
        await toFrame('PlayerCommand',{action:'rate',rate:r.previousRate});
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
  socket.onmessage=event=>{incoming=incoming.then(()=>handleMessage(event));};
  socket.onclose=()=>{status='Disconnected';fail('Browser connection lost');clearInterval(heartbeat);setTimeout(()=>{if(selected)openSocket(token);},2000);};
  clearInterval(heartbeat);heartbeat=setInterval(()=>send({type:'Heartbeat'}),20000);
}
chrome.runtime.onMessage.addListener((m,sender,reply)=>{
  if(m.target==='offscreen')return;
  if(m.type==='ListVideos'){
    reply({videos:[...observed.values()].filter(v=>v.identity.tabId===m.tabId&&Date.now()-v.seen<1500),status});return;
  }
  if(m.type==='Connect'){connect(m).then(reply,e=>reply({error:e.message}));return true;}
  if(m.type==='Disconnect'){
    (async()=>{send({type:'Unbound'});await fail('Disconnected');selected=null;if(socket)socket.close();await offscreen({type:'DisconnectStream'});reply({ok:true});})();return true;
  }
  if(!sender.tab){
    if(m.type==='AudioChunk'&&selected)send({...m,identity:selected});
    if(m.type==='EncodedCapture'){
      if(recording?.operationId===m.operationId)send({...m,type:'CaptureResult',ok:true,identity:selected});
    }
    return;
  }
  const identity={tabId:sender.tab.id,documentId:sender.documentId,frameId:sender.frameId,videoId:m.videoId};
  if(m.type==='VideoRemoved'&&key(identity)===key(selected)){
    (async()=>{await fail('Video replaced');if(m.replacementVideoId){selected={...selected,videoId:m.replacementVideoId};await chrome.storage.local.set({selected});send({type:'Bound',identity:selected});}})();return;
  }
  if(m.type==='PlaybackSnapshot'){
    (async()=>{
      let fingerprint=fingerprints.get(m.mediaKey);if(!fingerprint){fingerprint=await hash(m.mediaKey);fingerprints.set(m.mediaKey,fingerprint);}
      const {mediaKey,...rest}=m;const value={...rest,identity,fingerprint,seen:Date.now()};
      const prior=observed.get(key(identity));if(prior&&prior.sequence>=m.sequence)return;
      observed.set(key(identity),value);
      if(key(identity)===key(selected)){
        send(value);
        offscreen({...value,type:'AnalysisClock'}).catch(()=>{});
        if(recording&&recording.fingerprint!==fingerprint)await fail('Media or audio track changed');
      }
    })();return;
  }
  if(m.type==='CropResult'){
    if(recording?.operationId===m.operationId&&sender.tab.id===selected.tabId)offscreen({...m,type:'SetCrop'});return;
  }
  if(key(identity)!==key(selected)&&!['CaptureClock','CaptureBoundary','CaptureAborted','CaptionResource'].includes(m.type))return;
  if(m.operationId&&recording?.operationId===m.operationId){
    if(sender.tab.id!==selected.tabId||sender.documentId!==selected.documentId)return;
    if(m.type==='CaptureAborted')fail(m.error);
    if(m.type==='CaptureClock')offscreen({...m,type:'RecordingClock'});
    if(m.type==='CaptureBoundary'){
      offscreen({type:'StopRecording',operationId:m.operationId,time:m.time}).then(r=>{if(!r?.ok)fail(r?.error||'Encoding failed');},e=>fail(e.message));
    }
  }
  if(m.type==='CaptionIntervals'||m.type==='CaptionObservation')send({...m,identity});
  if(m.type==='CaptionResource'&&/english|german|englisch|deutsch|^en$|^de$/i.test(m.language+' '+m.label)){
    (async()=>{try{
      const url=new URL(m.url);if(!['http:','https:'].includes(url.protocol))return;
      const response=await fetch(url.href);if(!response.ok)return;
      const text=await response.text();if(text.length<4000000){const value={type:'CaptionText',identity,text,language:m.language};captionCache.set(key(identity),[value]);if(key(identity)===key(selected))send(value);}
    }catch{}})();
  }
});
chrome.tabs.onActivated.addListener(({tabId})=>{if(recording&&selected?.tabId!==tabId)fail('Selected tab changed');});
chrome.tabs.onRemoved.addListener(tabId=>{if(tabId===selected?.tabId){fail('Video tab closed');selected=null;}});
// A worker restart keeps the same bound document; it never adopts the active tab.
chrome.storage.local.get(['selected','token']).then(async saved=>{
  if(selected||!saved.selected||!saved.token)return;
  if(await chrome.offscreen.hasDocument()){
    const state=await offscreen({type:'StreamState'});
    if(state?.connected){selected=saved.selected;openSocket(saved.token);}
  }
}).catch(()=>{});
