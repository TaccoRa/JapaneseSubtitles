/* One isolated-world instance per frame. No devtools or stream URL extraction. */
(()=>{
const videos=new Map(), ids=new WeakMap(), epochs=new WeakMap(), captionSources=new WeakMap();
let sequence=0, capture=null, suppress=0;
const roots=()=>{
  const all=[document];
  for(let i=0;i<all.length;i++)for(const e of all[i].querySelectorAll('*'))if(e.shadowRoot)all.push(e.shadowRoot);
  return all;
};
const send=m=>chrome.runtime.sendMessage(m).catch(()=>{});
function mediaKey(video){
  // Streaming duration can change as segments load without changing the episode.
  return location.href+'|'+video.currentSrc+'|'+(epochs.get(video)||0)+'|'+[...video.audioTracks||[]].map(t=>t.id+':'+t.enabled);
}
function publishCaptions(video,id){
  if(video.readyState<1)return;
  let seen=captionSources.get(video);if(!seen){seen=new Set();captionSources.set(video,seen);}
  for(const track of video.querySelectorAll('track[src]')){
    const resourceKey=mediaKey(video)+'|'+track.src+'|'+track.srclang+'|'+track.label;
    if(seen.has(resourceKey))continue;seen.add(resourceKey);
    send({type:'CaptionResource',url:track.src,videoId:id,mediaKey:mediaKey(video),language:track.srclang,label:track.label});
  }
}
function snapshot(video,id,event='tick'){
  const r=video.getBoundingClientRect();
  return {type:'PlaybackSnapshot',videoId:id,sequence:++sequence,
    currentTime:video.currentTime,duration:Number.isFinite(video.duration)?video.duration:0,
    paused:video.paused,buffering:video.readyState<3,seeking:video.seeking,rate:video.playbackRate,
    width:r.width,height:r.height,viewport:{width:innerWidth,height:innerHeight},
    // Full URL is hashed in the worker and never sent to the desktop.
    mediaKey:mediaKey(video),event};
}
function discover(){
  const found=new Set();
  for(const root of roots())for(const video of root.querySelectorAll('video')){
    found.add(video);
    if(ids.has(video)){publishCaptions(video,ids.get(video));continue;}
    const id=crypto.randomUUID();ids.set(video,id);videos.set(id,video);
    for(const event of ['play','playing','pause','seeking','seeked','waiting','stalled','ratechange','emptied','loadedmetadata','ended'])
      video.addEventListener(event,()=>{
        if(event==='emptied')epochs.set(video,(epochs.get(video)||0)+1);
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
    try{v.currentTime=t;done();}catch(e){cleanup();reject(e);}
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
chrome.runtime.onMessage.addListener((m,sender,reply)=>{
  if(m.type==='PlayerCommand'){command(m).then(state=>reply({ok:true,state}),e=>reply({ok:false,error:e.message}));return true;}
  if(m.type==='PrepareCapture'){
    (async()=>{
      if(capture)throw Error('A recording is already running');
      const v=videos.get(m.identity.videoId);if(!v)throw Error('Video replaced');
      suppress=performance.now()+15000;v.pause();v.playbackRate=1;await waitSeek(v,m.start);
      capture={...m,id:m.identity.videoId,video:v,started:false};
      const r=v.getBoundingClientRect();
      window.postMessage({subtitlePlayerCrop:true,operationId:m.operationId,rect:{x:r.x,y:r.y,width:r.width,height:r.height},viewport:{width:innerWidth,height:innerHeight}},'*');
      return {ok:true};
    })().then(reply,e=>reply({ok:false,error:e.message}));return true;
  }
  if(m.type==='RunCapture'){
    (async()=>{if(!capture||capture.operationId!==m.operationId)throw Error('Capture cancelled');
      suppress=performance.now()+500;await capture.video.play();capture.started=true;return {ok:true};
    })().then(reply,e=>reply({ok:false,error:e.message}));return true;
  }
  if(m.type==='FinishCapture'){
    if(capture?.operationId===m.operationId){suppress=performance.now()+500;capture.video.pause();capture=null;}
    reply({ok:true});
  }
});
// Translate video bounds through nested frames, including open shadow roots.
window.addEventListener('message',e=>{
  const m=e.data;if(!m?.subtitlePlayerCrop)return;
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
let lastCaption='';
setInterval(()=>{
  discover();for(const [id,v]of videos){
    send(snapshot(v,id));
    const native=[...v.textTracks].flatMap(t=>[...t.cues||[]].map(c=>[c.startTime,c.endTime]));
    if(native.length>=8&&sequence%50===0)send({type:'CaptionIntervals',videoId:id,intervals:native});
    if(capture?.id===id&&capture.started){
      send({type:'CaptureClock',operationId:capture.operationId,time:v.currentTime,buffering:v.readyState<3,paused:v.paused});
      if(v.currentTime>=capture.end){suppress=performance.now()+500;v.pause();send({type:'CaptureBoundary',operationId:capture.operationId,time:v.currentTime});capture.started=false;}
    }
  }
},100);
// Common custom players expose text without a native TextTrack. Observe it as
// evidence only; these approximate edges do not automatically earn confidence.
setInterval(()=>{for(const [id,v]of videos){
  const text=roots().flatMap(r=>[...r.querySelectorAll('#subText,[data-part="cue"],.vjs-text-track-display')]).map(x=>x.textContent).join('\n').trim();
  if(text!==lastCaption){send({type:'CaptionObservation',videoId:id,text,time:v.currentTime});lastCaption=text;}
}},100);
})();
