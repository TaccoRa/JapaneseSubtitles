let stream,context,source,recorder,chunks=[],operation,crop,screenshot,screenshotTime;
let processor,clock=null,pcm=[],pcmCount=0,pcmStart=0,recordedPCM=[],recordingActive=false;
const preview=document.querySelector('#preview'),canvas=document.querySelector('#image');
const base64=async blob=>{const bytes=new Uint8Array(await blob.arrayBuffer());let out='';for(let i=0;i<bytes.length;i+=32768)out+=String.fromCharCode(...bytes.subarray(i,i+32768));return btoa(out);};
async function disconnect(){
  recordingActive=false;recordedPCM=[];pcm=[];pcmCount=0;clock=null;
  if(recorder?.state!=='inactive')recorder?.stop();recorder=null;operation=null;
  stream?.getTracks().forEach(t=>t.stop());await context?.close();stream=null;
}
chrome.runtime.onMessage.addListener((m,sender,reply)=>{
  if(m.target!=='offscreen')return;
  (async()=>{
    if(m.type==='StreamState')return {ok:true,connected:!!stream&&stream.getTracks().some(t=>t.readyState==='live')};
    if(m.type==='ConnectStream'){
      await disconnect();stream=await navigator.mediaDevices.getUserMedia({
        audio:{mandatory:{chromeMediaSource:'tab',chromeMediaSourceId:m.streamId}},
        video:{mandatory:{chromeMediaSource:'tab',chromeMediaSourceId:m.streamId}}});
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
          chrome.runtime.sendMessage({type:'AudioChunk',audio:btoa(value),start:pcmStart,rate:clock.rate,fingerprint:clock.fingerprint}).catch(()=>{});
          pcm=[];pcmCount=0;
        }
      };
      preview.srcObject=stream;await preview.play();return {ok:true};
    }
    if(m.type==='DisconnectStream'){await disconnect();return {ok:true};}
    if(m.type==='AnalysisClock'){
      if(clock&&(Math.abs(m.currentTime-clock.currentTime)>0.8||m.rate!==clock.rate||m.fingerprint!==clock.fingerprint)){pcm=[];pcmCount=0;}
      clock=m;return {ok:true};
    }
    if(m.type==='SetCrop'){crop=m;return {ok:true};}
    if(m.type==='StartRecording'){
      if(!stream||!stream.getAudioTracks().length)throw Error('Click Connect in the extension to enable audio');
      if(recorder&&recorder.state!=='inactive')throw Error('Recorder busy');
      operation=m.operationId;chunks=[];screenshot=null;screenshotTime=m.screenshotTime;
      recordedPCM=[];recordingActive=true;return {ok:true};
    }

    if(m.operationId!==operation)return {ok:false,error:'Stale recording operation'};
    if(m.type==='RecordingClock'){
      const paused=m.paused||m.buffering;
      if(!screenshot&&m.time>=screenshotTime&&crop?.operationId===operation){
        const r=crop.rect,view=crop.viewport;
        const sx=preview.videoWidth/view.width,sy=preview.videoHeight/view.height;
        const x=Math.max(0,r.x*sx),y=Math.max(0,r.y*sy);
        const w=Math.min(r.width*sx,preview.videoWidth-x),h=Math.min(r.height*sy,preview.videoHeight-y);
        if(w<8||h<8)throw Error('Video image is outside the captured tab');
        canvas.width=Math.round(w);canvas.height=Math.round(h);
        canvas.getContext('2d').drawImage(preview,x,y,w,h,0,0,canvas.width,canvas.height);
        screenshot=canvas.toDataURL('image/png').split(',')[1];
      }
      return {ok:true};
    }
    if(m.type==='CancelRecording'){operation=null;recordingActive=false;recordedPCM=[];return {ok:true};}
    if(m.type==='StopRecording'){
      const id=operation;
      recordingActive=false;
      if(!screenshot)throw Error('Video screenshot was not captured');
      const count=recordedPCM.reduce((n,a)=>n+a.length,0);
      if(!count)throw Error('No audio was recorded');
      const bytes=new ArrayBuffer(44+count*2),view=new DataView(bytes);
      const put=(at,value)=>{for(let i=0;i<value.length;i++)view.setUint8(at+i,value.charCodeAt(i));};
      put(0,'RIFF');view.setUint32(4,36+count*2,true);put(8,'WAVE');put(12,'fmt ');view.setUint32(16,16,true);
      view.setUint16(20,1,true);view.setUint16(22,1,true);view.setUint32(24,context.sampleRate,true);view.setUint32(28,context.sampleRate*2,true);
      view.setUint16(32,2,true);view.setUint16(34,16,true);put(36,'data');view.setUint32(40,count*2,true);
      let index=44;for(const chunk of recordedPCM)for(const value of chunk){view.setInt16(index,Math.max(-32768,Math.min(32767,value*32767)),true);index+=2;}
      const audio=await base64(new Blob([bytes],{type:'audio/wav'}));
      await chrome.runtime.sendMessage({type:'EncodedCapture',operationId:id,audio,image:screenshot,audioMime:'audio/wav',endTime:m.time});
      return {ok:true};
    }
    return {ok:false,error:'Unknown recorder request'};
  })().then(reply,e=>reply({ok:false,error:e.message}));return true;
});
