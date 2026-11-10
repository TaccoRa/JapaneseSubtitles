let stream,context,source,operation,streamTabId=null;
const clips=new Map();
let lifecycle=Promise.resolve();
let processor,clock=null,pcm=[],pcmCount=0,pcmStart=0,recordedPCM=[],recordingActive=false;
const canvas=document.querySelector('#image');
const notify=message=>{try{return chrome.runtime.sendMessage(message).catch(()=>{});}catch{return Promise.resolve();}};
const base64=async blob=>{const bytes=new Uint8Array(await blob.arrayBuffer());let out='';for(let i=0;i<bytes.length;i+=32768)out+=String.fromCharCode(...bytes.subarray(i,i+32768));return btoa(out);};
async function completeClip(clip){
  if(!clip.audio||!clip.image||(clip.images?.length||0)<(clip.screenshotCount||1)||clip.sending||clips.get(clip.id)!==clip)return;
  clip.sending=true;
  clip.images?.forEach(frame=>frame.close());
  await notify({type:'EncodedCapture',operationId:clip.id,audio:clip.audio,image:clip.image,audioMime:'audio/wav',endTime:clip.endTime});
  if(clips.get(clip.id)===clip)clips.delete(clip.id);
}
async function disconnect(){
  recordingActive=false;recordedPCM=[];pcm=[];pcmCount=0;clock=null;
  operation=null;for(const clip of clips.values())clip.images?.forEach(frame=>frame.close());clips.clear();
  // Detach before awaiting: cleanup is safe after failures and repeated disconnects.
  const oldStream=stream,oldContext=context,oldSource=source,oldProcessor=processor;
  stream=context=source=processor=null;
  streamTabId=null;
  if(oldProcessor){oldProcessor.port.onmessage=null;oldProcessor.disconnect();}
  oldSource?.disconnect();
  oldStream?.getTracks().forEach(t=>t.stop());
  if(oldContext&&oldContext.state!=='closed')await oldContext.close();
}
chrome.runtime.onMessage.addListener((m,sender,reply)=>{
  if(m.target!=='offscreen')return;
  const handle=async()=>{
    if(m.type==='StreamState')return {ok:true,connected:!!stream&&context?.state!=='closed'&&stream.getAudioTracks().some(t=>t.readyState==='live'),tabId:streamTabId};
    if(m.type==='ConnectStream'){
      await disconnect();
      try{
      stream=await navigator.mediaDevices.getUserMedia({
        audio:{mandatory:{chromeMediaSource:'tab',chromeMediaSourceId:m.streamId}},
        // Continuous video tab capture forces Chrome's fullscreen-within-tab mode.
        // A card's still image is captured separately, only on the bound tab.
        video:false});
      streamTabId=m.tabId;
      const connectedStream=stream;
      for(const track of stream.getAudioTracks())track.addEventListener('ended',()=>{
        if(stream!==connectedStream)return;
        recordingActive=false;
        notify({type:'NativeRecordingStopped',tabId:m.tabId});
      },{once:true});
      context=new AudioContext();source=context.createMediaStreamSource(stream);source.connect(context.destination);await context.resume();
      await context.audioWorklet.addModule('pcm-worklet.js');
      processor=new AudioWorkletNode(context,'subtitle-pcm');source.connect(processor);
      const silent=context.createGain();silent.gain.value=0;processor.connect(silent).connect(context.destination);
      processor.port.onmessage=({data})=>{
        if(recordingActive&&clock&&!clock.paused&&!clock.buffering&&!clock.seeking)recordedPCM.push(data.slice());
        if(!clock||clock.paused||clock.buffering||clock.seeking){pcm=[];pcmCount=0;return;}
        if(!pcmCount)pcmStart=clock.currentTime;
        pcm.push(data);pcmCount+=data.length;
        if(pcmCount>=context.sampleRate*25){
          const all=new Float32Array(pcmCount);let at=0;for(const chunk of pcm){all.set(chunk,at);at+=chunk.length;}
          const out=new Int16Array(Math.floor(all.length*16000/context.sampleRate));
          for(let i=0;i<out.length;i++){const index=i*context.sampleRate/16000;const left=Math.floor(index),f=index-left;out[i]=Math.max(-32768,Math.min(32767,((all[left]||0)*(1-f)+(all[left+1]||0)*f)*32767));}
          const bytes=new Uint8Array(out.buffer);let value='';for(let i=0;i<bytes.length;i+=32768)value+=String.fromCharCode(...bytes.subarray(i,i+32768));
          notify({type:'AudioChunk',audio:btoa(value),start:pcmStart,rate:clock.rate,fingerprint:clock.fingerprint});
          pcm=[];pcmCount=0;
        }
      };
      return {ok:true};
      }catch(error){await disconnect();throw error;}
    }
    if(m.type==='DisconnectStream'){await disconnect();return {ok:true};}
    if(m.type==='AnalysisClock'){
      if(clock&&(Math.abs(m.currentTime-clock.currentTime)>0.8||m.rate!==clock.rate||m.fingerprint!==clock.fingerprint)){pcm=[];pcmCount=0;}
      clock=m;return {ok:true};
    }
    if(m.type==='SetCrop'){
      const clip=clips.get(m.operationId)||{id:m.operationId};
      clip.crop=m;clips.set(m.operationId,clip);return {ok:true};
    }
    if(m.type==='StartRecording'){
      if(!stream||!stream.getAudioTracks().some(t=>t.readyState==='live')||!context||context.state==='closed')throw Error('Click Enable native recording in the extension to enable audio');
      if(recordingActive)throw Error('Recorder busy');
      operation=m.operationId;
      const clip=clips.get(operation)||{id:operation};
      clip.screenshotCount=m.screenshotCount||1;clip.images=[];
      clip.sampleRate=context.sampleRate;clips.set(operation,clip);
      recordedPCM=[];recordingActive=true;return {ok:true};
    }

    const clip=clips.get(m.operationId);
    if(!clip)return {ok:false,error:'Stale recording operation'};
    if(m.type==='StoreScreenshot'){
      if(!clip.crop)throw Error('Video crop is not ready');
      if(!/^data:image\/png;base64,/.test(m.image))throw Error('Screenshot must be local PNG data');
      const r=clip.crop.rect,view=clip.crop.viewport;
      const image=await createImageBitmap(await (await fetch(m.image)).blob());
      try{
        if(clips.get(clip.id)!==clip)throw Error('Capture cancelled');
        const sx=image.width/view.width,sy=image.height/view.height;
        const x=Math.max(0,r.x*sx),y=Math.max(0,r.y*sy);
        const w=Math.min(r.width*sx,image.width-x),h=Math.min(r.height*sy,image.height-y);
        if(![x,y,w,h].every(Number.isFinite)||w<8||h<8)throw Error('Video image is outside the captured tab');
        canvas.width=Math.round(w);canvas.height=Math.round(h);
        canvas.getContext('2d').drawImage(image,x,y,w,h,0,0,canvas.width,canvas.height);
        clip.images=clip.images||[];
        clip.images.push(await createImageBitmap(canvas));
        canvas.width=Math.max(...clip.images.map(frame=>frame.width));
        canvas.height=clip.images.reduce((height,frame)=>height+frame.height,0);
        let top=0;for(const frame of clip.images){canvas.getContext('2d').drawImage(frame,0,top);top+=frame.height;}
        clip.image=canvas.toDataURL('image/png').split(',')[1];
      }finally{image.close();}
      await completeClip(clip);
      return {ok:true};
    }
    if(m.type==='RecordingClock'){
      return {ok:true};
    }
    if(m.type==='CancelRecording'){
      clip.images?.forEach(frame=>frame.close());
      clips.delete(m.operationId);
      if(operation===m.operationId){operation=null;recordingActive=false;recordedPCM=[];}
      return {ok:true};
    }
    if(m.type==='StopRecording'){
      if(clip.stopped)return {ok:true};
      if(operation!==m.operationId)return {ok:false,error:'Stale recording operation'};
      clip.stopped=true;clip.endTime=m.time;
      recordingActive=false;
      const samples=recordedPCM;operation=null;recordedPCM=[];
      const count=samples.reduce((n,a)=>n+a.length,0);
      if(!count){clips.delete(clip.id);throw Error('No audio was recorded');}
      const bytes=new ArrayBuffer(44+count*2),view=new DataView(bytes);
      const put=(at,value)=>{for(let i=0;i<value.length;i++)view.setUint8(at+i,value.charCodeAt(i));};
      put(0,'RIFF');view.setUint32(4,36+count*2,true);put(8,'WAVE');put(12,'fmt ');view.setUint32(16,16,true);
      view.setUint16(20,1,true);view.setUint16(22,1,true);view.setUint32(24,clip.sampleRate,true);view.setUint32(28,clip.sampleRate*2,true);
      view.setUint16(32,2,true);view.setUint16(34,16,true);put(36,'data');view.setUint32(40,count*2,true);
      let index=44;for(const chunk of samples)for(const value of chunk){view.setInt16(index,Math.max(-32768,Math.min(32767,value*32767)),true);index+=2;}
      clip.audio=await base64(new Blob([bytes],{type:'audio/wav'}));
      await completeClip(clip);
      return {ok:true};
    }
    return {ok:false,error:'Unknown recorder request'};
  };
  let result;
  if(['ConnectStream','DisconnectStream'].includes(m.type)){
    result=lifecycle.then(handle);lifecycle=result.catch(()=>{});
  }else result=handle();
  result.then(reply,e=>reply({ok:false,error:e.message}));return true;
});
