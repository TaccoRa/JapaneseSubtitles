const [tab] = await chrome.tabs.query({active:true,currentWindow:true});
const saved = await chrome.storage.local.get(['token']);
document.querySelector('#token').value = saved.token || '';
async function refreshVideos(){
  const state = await chrome.runtime.sendMessage({type:'ListVideos',tabId:tab.id});
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
  if(!state.videos.length)document.querySelector('#status').textContent='Open this popup on the video tab. If it stays empty, reload the video page once after installing the extension.';
  else if(document.querySelector('#status').textContent.startsWith('Open this popup'))document.querySelector('#status').textContent=state.status;
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
document.querySelector('#disconnect').onclick=()=>chrome.runtime.sendMessage({type:'Disconnect'});
