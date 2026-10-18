const [tab] = await chrome.tabs.query({active:true,currentWindow:true});
const saved = await chrome.storage.local.get(['token']);
document.querySelector('#token').value = saved.token || '';
const state = await chrome.runtime.sendMessage({type:'ListVideos',tabId:tab.id});
for(const v of state.videos){
  const option = document.createElement('option');
  option.value=JSON.stringify(v.identity);
  option.textContent=`${v.width}×${v.height} · ${Math.floor(v.duration/60)} min · frame ${v.identity.frameId}`;
  document.querySelector('#video').append(option);
}
document.querySelector('#status').textContent=state.status;
document.querySelector('#connect').onclick=async()=>{
  try{
    const value=document.querySelector('#video').value;
    if(!value)throw Error('No video found. Reload this page after installing the extension.');
    const result=await chrome.runtime.sendMessage({type:'Connect',identity:JSON.parse(value),token:document.querySelector('#token').value.trim()});
    document.querySelector('#status').textContent=result.error||'Connected. Waiting for subtitle timing…';
  }catch(e){document.querySelector('#status').textContent=e.message;}
};
document.querySelector('#disconnect').onclick=()=>chrome.runtime.sendMessage({type:'Disconnect'});
