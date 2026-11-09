/* Reads subtitle timing and selects the user's preferred plain English track.
   Never changes the video clock or controls playback. */
(()=>{
if(globalThis.subtitlePlayerCaptionObserver)return;
const channel='SubtitlePlayerCaptionTimingV1',limit=4000000,cache=new Map(),obsolete=new Set(),resources=new Map();
const selections=new WeakMap();
let page=location.href,generation=0;
function reset(){for(const id of cache.keys())obsolete.add(id);cache.clear();resources.clear();netflixRequests.clear();netflixActiveScope=null;generation++;}
const attr=(node,name)=>String(node.getAttribute?.(name)??node[name]??'');
const trackURL=node=>{const src=attr(node,'src');try{return new URL(src,location.href).href;}catch{return src;}};
function trackElements(){
  const roots=[document],tracks=[];
  for(let i=0;i<roots.length;i++){
    for(const node of roots[i].querySelectorAll('*'))if(node.shadowRoot)roots.push(node.shadowRoot);
    tracks.push(...roots[i].querySelectorAll('track[src]'));
  }
  return tracks;
}
const seconds=(raw,tickRate=NaN,frameRate=NaN)=>{
  if(!raw)return NaN;
  raw=raw.trim().replace(',','.');
  if(/^\d+(?:\.\d+)?t$/.test(raw))return Number.isFinite(tickRate)&&tickRate>0?parseFloat(raw)/tickRate:NaN;
  if(/^\d+(?:\.\d+)?f$/.test(raw))return Number.isFinite(frameRate)&&frameRate>0?parseFloat(raw)/frameRate:NaN;
  if(/^[\d.]+(?:ms|s|m|h)$/.test(raw)){
    const value=parseFloat(raw);return raw.endsWith('ms')?value/1000:raw.endsWith('h')?value*3600:raw.endsWith('m')?value*60:value;
  }
  if(!/^\d+(?::\d+){1,2}(?:\.\d+)?$/.test(raw))return NaN;
  return raw.split(':').reverse().reduce((sum,value,i)=>sum+Number(value)*60**i,0);
};
function parse(text){
  const result=[];
  if(!text||text.length>limit||/xywh=|#t=\d+.*xywh/i.test(text))return result;
  for(const line of text.split(/\r?\n/)){
    const match=line.match(/^\s*([\d:.,]+)\s*-->\s*([\d:.,]+)/);
    if(match)result.push([seconds(match[1]),seconds(match[2])]);
  }
  // TTML/DFXP captions, including Netflix responses already fetched by its player.
  if(!result.length&&/<(?:\w+:)?tt[\s>]/i.test(text)&&typeof DOMParser==='function'){
    const xml=new DOMParser().parseFromString(text,'application/xml');
    const root=xml.documentElement;
    const parameter=name=>Number(root?.getAttributeNS?.('http://www.w3.org/ns/ttml#parameter',name)||root?.getAttribute('ttp:'+name));
    const tickRate=parameter('tickRate'),frameRate=parameter('frameRate');
    for(const cue of xml.getElementsByTagNameNS('*','p')){
      const a=seconds(cue.getAttribute('begin'),tickRate,frameRate);
      const end=cue.getAttribute('end');
      const b=end?seconds(end,tickRate,frameRate):a+seconds(cue.getAttribute('dur'),tickRate,frameRate);
      result.push([a,b]);
    }
  }
  return result.filter(([a,b])=>Number.isFinite(a)&&Number.isFinite(b)&&a>=0&&b>a&&b<86400).slice(0,10000).sort((a,b)=>a[0]-b[0]);
}
function language(url){
  try{
    for(const track of trackElements())if(trackURL(track)===url)return attr(track,'srclang')||attr(track,'label');
  }catch{}
  const match=String(url).match(/(?:^|[\/_.?=&-])(en|eng|english|de|deu|ger|german)(?:[\/_.?=&-]|$)/i);
  return match?.[1]||'';
}
function publish(value){window.postMessage({channel,...value},location.origin==='null'?'*':location.origin);}
const referenceId=value=>{let h=2166136261;for(const c of String(value))h=Math.imul(h^c.charCodeAt(0),16777619);return 'loaded-'+(h>>>0).toString(16);};
function acceptIntervals(intervals,lang,priority,id,label=''){
  if(location.href!==page){reset();page=location.href;}
  if(intervals.length<8)return;
  const value={intervals,language:lang,priority,page,referenceId:id,label};
  // Only timing leaves MAIN world. No caption text, URLs or stream bytes.
  const key=id;
  if(JSON.stringify(cache.get(key))===JSON.stringify(value))return;
  cache.set(key,value);while(cache.size>8)cache.delete(cache.keys().next().value);
  publish(value);
}
function resourceTiming(value){
  const tracks=trackElements(),primary=tracks.filter(t=>/^english$/i.test(attr(t,'label').trim()));
  const track=tracks.find(t=>[value.url,value.requestURL].includes(trackURL(t)));
  if(primary.length&&(!track||!primary.includes(track)))return;
  const label=track?attr(track,'label'):'';
  if(/english\s*\(?2\)?/i.test(label))return;
  const lang=track?(attr(track,'srclang')||label):language(value.requestURL||value.url);
  // Opaque subtitle URLs are not language evidence. The track elements often
  // appear after fetch completes, so retain timings and classify them on replay.
  if(!/^english$/i.test(label.trim())&&!/^(en|eng|english|de|deu|ger|german)$/i.test(lang))return;
  const priority=/^english$/i.test(label.trim())?-100:/^(en|eng|english)$/i.test(lang)?0:20;
  const id=referenceId(track?trackURL(track):value.url);obsolete.delete(id);
  acceptIntervals(value.intervals,lang,priority,id,label);
}
function accept(text,url,requestURL=url){
  if(location.href!==page){reset();page=location.href;}
  const intervals=parse(text);if(intervals.length<8)return;
  const value={url,requestURL,intervals};resources.set(requestURL,value);
  while(resources.size>32)resources.delete(resources.keys().next().value);
  resourceTiming(value);
}
// Netflix does not expose HTML TextTracks. Read the current session's selected
// text track and its already loaded caption URL. Never inspect video URLs or
// change the user's audio, subtitle selection or playback position.
const netflixRequests=new Set();
let netflixActiveScope=null;
function netflixPlayer(){
  if(!/(^|\.)netflix\.com$/i.test(location.hostname||new URL(location.href).hostname))return null;
  const movie=location.pathname?.match(/^\/watch\/(\d+)/)?.[1]||new URL(location.href).pathname.match(/^\/watch\/(\d+)/)?.[1];
  if(!movie||document.querySelectorAll('video').length!==1)return null;
  const api=globalThis.netflix?.appContext?.state?.playerApp?.getAPI?.();
  const videoPlayer=api?.videoPlayer;
  const players=(videoPlayer?.getAllPlayerSessionIds?.()||[]).map(id=>({id,player:videoPlayer.getVideoPlayerBySessionId?.(id)}))
    .filter(value=>String(value.player?.getMovieId?.())===movie);
  return players.length===1?{...players[0],api,movie}:null;
}
function netflixContext(session){
  const current=session.api.getVideoMetadataByVideoId?.(session.player.getMovieId())?.getCurrentVideo?.();
  const title=current?.getTitle?.();
  if(typeof title!=='string'||!title.trim())return;
  const episodic=current.isEpisodic?.()===true;
  const episode=episodic?Number(current.getEpisodeNumber?.()):null;
  const season=episodic?Number(current.getSeason?.()?._season?.seq):null;
  if(episodic&&(!Number.isInteger(episode)||episode<=0||!Number.isInteger(season)||season<=0))return;
  publish({page:location.href,mediaContext:{title:title.trim().slice(0,300),kind:episodic?'episode':'movie',episode,season,
    playerVideoId:String(session.player.getMovieId())}});
}
function netflixCaptionURL(session,trackId){
  const state=globalThis.netflix?.appContext?.state?.playerApp?.getState?.();
  const root=state?.videoPlayer?.cadmiumPlayerRepository?.playersById?.[session.id];
  const pending=[[root,0]],seen=new WeakSet();
  // Player internals are minified; match caption records by their structure
  // within this bound session, with limits on both traversal depth and work.
  for(let visited=0;pending.length&&visited<12000;visited++){
    const [node,depth]=pending.pop();
    if(!node||typeof node!=='object'||depth>20||seen.has(node)||node instanceof ArrayBuffer||ArrayBuffer.isView(node))continue;
    seen.add(node);
    let properties;try{properties=Object.getOwnPropertyDescriptors(node);}catch{continue;}
    const value=name=>properties[name]?.value;
    if(value('type')==='timedtext'&&value('trackId')===trackId&&Array.isArray(value('urls'))){
      const url=value('urls').find(item=>typeof item?.url==='string')?.url;
      if(url){try{const parsed=new URL(url);if(parsed.protocol==='https:'&&!parsed.username&&!parsed.password)return parsed.href;}catch{}}
    }
    for(const property of Object.values(properties))if(pending.length<12000&&property.value&&typeof property.value==='object')pending.push([property.value,depth+1]);
  }
  return null;
}
function netflixTracks(){
  try{
    const session=netflixPlayer();if(!session)return;
    try{netflixContext(session);}catch{}
    const track=session.player.getTimedTextTrack?.();
    const scope=location.href+'|'+session.id+'|'+(track?.trackId||'off');
    if(scope!==netflixActiveScope){cache.clear();resources.clear();netflixRequests.clear();netflixActiveScope=scope;}
    if(!track||track.isNoneTrack||track.isForcedNarrative||track.isImageBased)return;
    const lang=String(track.bcp47||'').toLowerCase();
    if(!/^(en|de)(?:-|$)/.test(lang)||typeof track.trackId!=='string')return;
    const trackId=track.trackId;
    const url=netflixCaptionURL(session,trackId);if(!url)return;
    if(netflixRequests.has(scope))return;
    netflixRequests.add(scope);while(netflixRequests.size>32)netflixRequests.delete(netflixRequests.values().next().value);
    const requestPage=location.href,requestGeneration=generation;
    originalFetch(url,{credentials:'omit',referrerPolicy:'no-referrer',signal:AbortSignal.timeout(5000)}).then(async response=>{
      if(!response.ok||Number(response.headers?.get('content-length'))>limit)return;
      const text=await response.text();
      const selected=netflixPlayer();
      if(location.href!==requestPage||generation!==requestGeneration||selected?.id!==session.id||selected.player.getTimedTextTrack?.()?.trackId!==trackId)return;
      const intervals=parse(text);
      acceptIntervals(intervals,lang,/^en(?:-|$)/.test(lang)?0:20,referenceId(scope),String(track.displayName||lang));
    }).catch(()=>{});
  }catch{}
}
function publicTracks(){
  // Vidstack keeps its parsed cues on the documented media-player TextTrack
  // API while the underlying HTML video's native tracks can remain disabled.
  const roots=[document];
  for(let i=0;i<roots.length;i++){
    for(const node of roots[i].querySelectorAll('*'))if(node.shadowRoot)roots.push(node.shadowRoot);
    for(const player of roots[i].querySelectorAll('media-player'))try{
      // React's <media-player> host need not expose instance properties. Use
      // the same discovery event as Vidstack's official MediaRemoteControl.
      let api=player.textTracks?player:null;
      if(!api&&typeof CustomEvent==='function')player.dispatchEvent(new CustomEvent('find-media-player',{detail:value=>{api=value;},bubbles:true,composed:true}));
      const list=api?.textTracks||api?.state?.textTracks;
      const tracks=list?.toArray?list.toArray():Array.from(list||[]);
      player.setAttribute?.('data-subtitleplayer-caption-api',api?'available':'events');
      player.setAttribute?.('data-subtitleplayer-caption-tracks',String(tracks.length));
      if(!tracks.length){
        const elements=[...player.querySelectorAll('track[src]')];
        const plain=elements.filter(t=>/^english$/i.test(attr(t,'label').trim()));
        if(plain.length===1&&selections.get(plain[0])!==generation&&typeof CustomEvent==='function'){
          selections.set(plain[0],generation);cache.clear();publish({page,preferredPending:true});
          player.dispatchEvent(new CustomEvent('media-text-track-change-request',{detail:{index:elements.indexOf(plain[0]),mode:'showing'},bubbles:true,composed:true,cancelable:true}));
          player.setAttribute?.('data-subtitleplayer-caption-requested','English');
        }
      }
      const primary=tracks.filter(t=>/^english$/i.test(String(t.label||'').trim()));
      const preferred=primary.length===1?primary[0]:null;
      if(preferred&&!obsolete.has(referenceId(preferred.src||preferred.id||preferred.label))&&selections.get(preferred)!==generation){
        selections.set(preferred,generation);cache.clear();
        // Documented Vidstack API: loads this caption track and deselects the
        // alternate, including after episode replacement. One attempt per track
        // generation avoids retrying blocked caption downloads indefinitely.
        if(preferred.mode!=='showing')preferred.mode='showing';
        player.setAttribute?.('data-subtitleplayer-caption-requested',String(preferred.label));
      }
      if(preferred&&!obsolete.has(referenceId(preferred.src||preferred.id||preferred.label))&&(!preferred.cues||preferred.cues.length<8))publish({page,preferredPending:true});
      for(const track of tracks){
        if(/english\s*\(?2\)?/i.test(String(track.label||'')))continue;
        if(preferred&&track!==preferred)continue;
        const lang=track.language||track.label||'',label=lang+' '+track.label;
        if(!/english|german|\b(en|eng|de|deu)\b/i.test(label)||!track.cues?.length)continue;
        const intervals=[...track.cues].map(c=>[c.startTime,c.endTime]).filter(([a,b])=>Number.isFinite(a)&&Number.isFinite(b)&&a>=0&&b>a&&b<86400).slice(0,10000);
        // Alternate English editions may share the SRT's old edit rather than
        // the playing video's edit. Prefer the primary English track whenever
        // already loaded; showing an alternate must not override it.
        const priority=track===preferred?-100:(/english|\b(en|eng)\b/i.test(label)?0:20)-(track.mode==='showing'?1:0);
        const id=referenceId(track.src||track.id||track.label);
        if(!obsolete.has(id))acceptIntervals(intervals,lang,priority,id,String(track.label||''));
      }
    }catch(error){player.setAttribute?.('data-subtitleplayer-caption-error',String(error.message||error).slice(0,200));}
  }
}
const candidate=(url,type)=>/\.vtt(?:[?#]|$)|\.srt(?:[?#]|$)|\.ttml(?:[?#]|$)|\.dfxp(?:[?#]|$)|\/timedtext(?:[/?]|$)|\/a\/[a-f\d]{32,}\.jpe?g(?:[?#]|$)/i.test(url)||/text\/vtt|subrip|ttml|ttaf/i.test(type||'')||trackElements().some(t=>trackURL(t)===url&&/english|german|\b(en|de)\b/i.test(attr(t,'srclang')+' '+attr(t,'label')));
let originalFetch=null,observersInstalled=false,mediaObserver=null;
function installResourceObservers(hasVideo=false){
  if(observersInstalled||(!hasVideo&&!document.querySelectorAll('video,media-player,track[src]').length))return;
  observersInstalled=true;mediaObserver?.disconnect();
  // Capture the page's current implementation only once a player exists.
  // Ordinary pages keep fetch/XHR untouched, including preload handling.
  originalFetch=globalThis.fetch;
  if(typeof originalFetch==='function'){
  globalThis.fetch=function(...args){
    const requestPage=location.href,requestGeneration=generation;
    const pending=Reflect.apply(originalFetch,this,args);
    pending.then(response=>{
      try{
        const url=response.url||String(args[0]?.url||args[0]);
        if(!response.ok||!candidate(url,response.headers?.get('content-type')))return;
        const size=Number(response.headers?.get('content-length'));
        if(size>limit)return;
        // Clone only subtitle responses. The player's body/Promise is unchanged.
        response.clone().text().then(text=>{if(location.href===requestPage&&requestGeneration===generation)accept(text,url,String(args[0]?.url||args[0]));}).catch(()=>{});
      }catch{}
    },()=>{});
    return pending;
  };
  }
  const xhr=globalThis.XMLHttpRequest?.prototype;
  if(xhr){
  const originalOpen=xhr.open;
  const requests=new WeakMap();
  xhr.open=function(...args){
    const url=String(args[1]||''),requestPage=location.href,requestGeneration=generation;
    const previous=requests.get(this);if(previous)this.removeEventListener('load',previous);
    const listener=()=>{
      try{
        if(location.href!==requestPage||requestGeneration!==generation||this.status<200||this.status>=300||!candidate(this.responseURL||url,this.getResponseHeader('content-type')))return;
        if(!this.responseType||this.responseType==='text')accept(this.responseText,this.responseURL||url,url);
        else if(this.responseType==='arraybuffer'&&this.response?.byteLength<=limit)accept(new TextDecoder().decode(this.response),this.responseURL||url,url);
      }catch{}
    };
    requests.set(this,listener);this.addEventListener('load',listener,{once:true});
    return Reflect.apply(originalOpen,this,args);
  };
  }
}
installResourceObservers();
if(!observersInstalled&&typeof MutationObserver==='function'){
  mediaObserver=new MutationObserver(()=>installResourceObservers());
  mediaObserver.observe(document,{childList:true,subtree:true});
}
window.addEventListener('message',event=>{
  if(event.source!==window||event.data?.channel!==channel)return;
  if(event.data.request==='reset'){reset();return;}
  if(event.data.request==='refresh')netflixRequests.clear();
  if(!['replay','refresh'].includes(event.data.request))return;
  if(location.href!==page){reset();page=location.href;}
  installResourceObservers(event.data.hasVideo===true);
  if(!observersInstalled)return;
  publicTracks();
  netflixTracks();
  for(const value of resources.values())resourceTiming(value);
  for(const value of cache.values())publish(value);
});
globalThis.subtitlePlayerCaptionObserver={parse};
})();
