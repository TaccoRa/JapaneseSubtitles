const [tab] = await chrome.tabs.query({active:true,currentWindow:true});
const saved = await chrome.storage.local.get(['token']);
document.querySelector('#token').value = saved.token || '';
async function refreshVideos(){
  const state = await chrome.runtime.sendMessage({type:'ListVideos',tabId:tab.id});
  const native=document.querySelector('#native');
  native.hidden=state.backend!=='native';native.disabled=state.nativeEnabled;
  native.textContent=state.nativeEnabled?'Native recording enabled':'Enable native recording';
  const select=document.querySelector('#video'),chosen=select.value;
  const next=state.videos.map(v=>JSON.stringify(v.identity));
  if(JSON.stringify([...select.options].map(o=>o.value))!==JSON.stringify(next)){
    select.replaceChildren();
    for(const v of state.videos){
      const option=document.createElement('option');option.value=JSON.stringify(v.identity);
      option.textContent=`${v.width}×${v.height} · ${Math.floor(v.duration/60)} min · frame ${v.identity.frameId}`;
      select.append(option);
    }
    if(next.includes(chosen))select.value=chosen;
  }
  document.querySelector('#connect').disabled=!state.videos.length;
  if(!state.videos.length)document.querySelector('#status').textContent=state.discoveryError?'Cannot inspect this tab: '+state.discoveryError:'Looking for the video in this tab and its frames…';
  else document.querySelector('#status').textContent=state.status+(state.autoEnabled?' · automatic connection on':' · automatic connection paused');
}
await refreshVideos();
setInterval(()=>refreshVideos().catch(e=>{document.querySelector('#status').textContent=e.message;}),500);
document.querySelector('#connect').onclick=async()=>{
  try{
    const value=document.querySelector('#video').value;
    if(!value)throw Error('No video found. Reload this page after installing the extension.');
    const result=await chrome.runtime.sendMessage({type:'Connect',identity:JSON.parse(value),token:document.querySelector('#token').value.trim()});
    document.querySelector('#status').textContent=result.error||'Connected. Waiting for subtitle timing…';
  }catch(e){document.querySelector('#status').textContent=e.message;}
};
document.querySelector('#native').onclick=async()=>{
  try{
    const result=await chrome.runtime.sendMessage({type:'EnableNativeRecording',identity:JSON.parse(document.querySelector('#video').value)});
    document.querySelector('#status').textContent=result.error||'Native audio recording enabled';
  }catch(e){document.querySelector('#status').textContent=e.message;}
};
document.querySelector('#disconnect').onclick=async()=>{
  try{
    const result=await chrome.runtime.sendMessage({type:'Disconnect'});
    document.querySelector('#status').textContent=result.error||'Disconnected';
  }catch(e){document.querySelector('#status').textContent=e.message;}
};
