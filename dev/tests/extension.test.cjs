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
function environment(){
  let listener,socket;
  const frames=[],offscreen=[],sent=[];
  class Socket{
    static OPEN=1;
    constructor(){socket=this;this.readyState=1;}
    send(value){sent.push(JSON.parse(value));}
    close(){}
  }
  const chrome={
    runtime:{onMessage:{addListener(fn){listener=fn;}},async sendMessage(m){offscreen.push(m);return {ok:true};}},
    storage:{local:{async get(){return {};},async set(){}}},
    offscreen:{async hasDocument(){return true;},async createDocument(){}},
    tabCapture:{async getMediaStreamId(){return 'stream';}},
    tabs:{async sendMessage(tab,m,target){frames.push({tab,m,target});return {ok:true,state:{}};},onActivated:{addListener(){}},onRemoved:{addListener(){}}}
  };
  vm.runInNewContext(source,{chrome,WebSocket:Socket,crypto:webcrypto,TextEncoder,URL,console,setInterval:()=>1,clearInterval(){},setTimeout:()=>1});
  const message=(m,s={})=>new Promise(resolve=>{const pending=listener(m,s,resolve);if(pending!==true)resolve();});
  return {frames,offscreen,sent,message,get socket(){return socket;},
    async connect(paused=true){
      await message({type:'Connect',identity,token:'code'});socket.onopen();socket.onmessage({data:JSON.stringify({type:'Welcome'})});
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
