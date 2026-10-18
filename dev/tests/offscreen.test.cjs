const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../../browser-extension/offscreen.js'),'utf8');

function environment(){
  let listener,failModule=false,acquireGate;
  const contexts=[],streams=[],constraints=[],sent=[],draws=[];
  const node=()=>({connect(other){return other;},disconnect(){this.disconnected=true;}});
  class Context{
    constructor(){this.state='running';this.sampleRate=16000;this.destination={};this.closed=0;contexts.push(this);this.audioWorklet={addModule:async()=>{if(failModule)throw Error('Worklet unavailable');}};}
    createMediaStreamSource(){this.source=node();return this.source;}
    createGain(){return {...node(),gain:{value:1}};}
    async resume(){}
    async close(){if(this.state==='closed')throw Error('Cannot close a closed AudioContext.');this.closed++;this.state='closed';}
  }
  class Worklet{constructor(context){Object.assign(this,node());this.port={onmessage:null};context.processor=this;}}
  const canvas={getContext:()=>({drawImage(...args){draws.push(args);}}),toDataURL:()=> 'data:image/png;base64,picture'};
  vm.runInNewContext(source,{
    document:{querySelector:()=>canvas},AudioContext:Context,AudioWorkletNode:Worklet,console,Blob,
    btoa:value=>Buffer.from(value,'binary').toString('base64'),
    fetch:async()=>({blob:async()=>new Blob()}),createImageBitmap:async()=>({width:1600,height:900,close(){}}),
    navigator:{mediaDevices:{async getUserMedia(value){
      constraints.push(value);if(acquireGate)await acquireGate;
      const track={readyState:'live',addEventListener(name,fn){this[name]=fn;},stop(){this.readyState='ended';}};
      const stream={getTracks:()=>[track],getAudioTracks:()=>[track]};streams.push(stream);return stream;
    }}},
    chrome:{runtime:{onMessage:{addListener(fn){listener=fn;}},async sendMessage(m){sent.push(m);}}}
  });
  return {contexts,streams,constraints,sent,draws,canvas,
    pcm:values=>contexts.at(-1).processor.port.onmessage({data:Float32Array.from(values)}),
    set failModule(value){failModule=value;},set gate(value){acquireGate=value;},
    message:type=>new Promise(resolve=>listener({target:'offscreen',...(typeof type==='string'?{type}:type)},{},resolve))};
}

test('disconnect is idempotent and reconnect acquires audio without a continuous video capture',async()=>{
  const env=environment();
  for(let i=0;i<3;i++){
    assert.equal((await env.message({type:'ConnectStream',streamId:'tab'})).ok,true);
    assert.equal(env.constraints[i].video,false);
    assert.equal((await env.message('DisconnectStream')).ok,true);
    assert.equal((await env.message('DisconnectStream')).ok,true);
  }
  assert.ok(env.contexts.every(context=>context.closed===1));
  assert.ok(env.streams.every(stream=>stream.getTracks()[0].readyState==='ended'));
  assert.equal((await env.message('StreamState')).connected,false);
});

test('native PCM encoder sends a playable mono WAV and the operation screenshot',async()=>{
  const env=environment();await env.message({type:'ConnectStream',streamId:'tab'});
  await env.message({type:'SetCrop',operationId:'card',rect:{x:0,y:0,width:800,height:450},viewport:{width:800,height:450}});
  await env.message({type:'StartRecording',operationId:'card'});
  await env.message({type:'AnalysisClock',currentTime:10,rate:1,fingerprint:'media',paused:false,buffering:false,seeking:false});
  env.pcm(Array(1600).fill(0.1));
  await env.message({type:'AnalysisClock',currentTime:10.1,rate:1,fingerprint:'media',paused:true,buffering:false,seeking:false});
  env.pcm(Array(1600).fill(0.9)); // Paused screenshot work must not add audio.
  await env.message({type:'StoreScreenshot',operationId:'card',image:'data:image/png;base64,AA=='});
  assert.equal((await env.message({type:'StopRecording',operationId:'card',time:10.1})).ok,true);
  const result=env.sent.find(m=>m.type==='EncodedCapture'),wav=Buffer.from(result.audio,'base64');
  assert.equal(result.operationId,'card');assert.equal(result.image,'picture');
  assert.equal(wav.toString('ascii',0,4),'RIFF');assert.equal(wav.toString('ascii',8,12),'WAVE');
  assert.equal(wav.readUInt16LE(22),1);assert.equal(wav.readUInt32LE(24),16000);
  assert.equal(wav.readUInt32LE(40),3200);assert.equal(wav.length,3244);
  assert.ok(Math.abs(wav.readInt16LE(44)-3276)<2);
});

test('stream ownership survives recording completion and reports an ended audio track',async()=>{
  const env=environment();await env.message({type:'ConnectStream',streamId:'tab',tabId:123});
  assert.equal((await env.message('StreamState')).tabId,123);
  const track=env.streams[0].getAudioTracks()[0];track.readyState='ended';track.ended();
  assert.equal((await env.message('StreamState')).connected,false);
  assert.equal(env.sent.at(-1).type,'NativeRecordingStopped');assert.equal(env.sent.at(-1).tabId,123);
});

test('a disconnect arriving during acquisition waits and releases the newly acquired stream',async()=>{
  const env=environment();let release;env.gate=new Promise(resolve=>release=resolve);
  const connected=env.message({type:'ConnectStream',streamId:'tab'}),disconnected=env.message('DisconnectStream');
  release();assert.equal((await connected).ok,true);assert.equal((await disconnected).ok,true);
  assert.equal(env.contexts[0].closed,1);assert.equal((await env.message('StreamState')).connected,false);
});

test('failed initialization releases partial audio resources and allows retry',async()=>{
  const env=environment();env.failModule=true;
  const failed=await env.message({type:'ConnectStream',streamId:'tab'});
  assert.equal(failed.ok,false);assert.match(failed.error,/Worklet unavailable/);
  assert.equal(env.contexts[0].closed,1);assert.equal(env.streams[0].getTracks()[0].readyState,'ended');
  env.failModule=false;assert.equal((await env.message({type:'ConnectStream',streamId:'new'})).ok,true);
});

test('a card screenshot crops browser pixels at device scale and rejects stale operations',async()=>{
  const env=environment();await env.message({type:'ConnectStream',streamId:'tab'});
  await env.message({type:'SetCrop',operationId:'card',rect:{x:100,y:50,width:600,height:300},viewport:{width:800,height:450}});
  await env.message({type:'StartRecording',operationId:'card'});
  assert.equal((await env.message({type:'StoreScreenshot',operationId:'old',image:'data:image/png;base64,AA=='})).ok,false);
  assert.equal((await env.message({type:'StoreScreenshot',operationId:'card',image:'data:image/png;base64,AA=='})).ok,true);
  assert.deepEqual(env.draws[0].slice(1),[200,100,1200,600,0,0,1200,600]);
  await env.message({type:'CancelRecording',operationId:'card'});
  assert.equal((await env.message({type:'StoreScreenshot',operationId:'card',image:'data:image/png;base64,AA=='})).ok,false);
});
