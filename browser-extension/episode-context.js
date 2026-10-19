/* Explicit episode metadata only. A video-clock reset is never an episode number. */
globalThis.subtitlePlayerEpisodeContext=href=>{
  const url=new URL(href),positive=value=>/^\d{1,4}$/.test(value||'')&&Number(value)>0?Number(value):null;
  let episode=null,season=null;
  if(/(^|\.)miruro\.[a-z]+$/i.test(url.hostname)&&url.pathname.startsWith('/watch/')){
    episode=positive(url.searchParams.get('ep'));season=positive(url.searchParams.get('season'));
    return episode?{seriesKey:url.origin+url.pathname,episode,season,scope:'season'}:null;
  }
  const match=url.pathname.match(/\/(?:staffel|season)-(\d+)\/(?:episode|folge)-(\d+)(?:\/|$)/i);
  if(match){
    season=positive(match[1]);episode=positive(match[2]);
    return episode?{seriesKey:url.origin+url.pathname.slice(0,match.index),episode,season,scope:'season'}:null;
  }
  // Generic adapters require a named parameter, never an opaque provider ID.
  episode=positive(url.searchParams.get('episode'));
  if(!episode)return null;
  season=positive(url.searchParams.get('season'));url.searchParams.delete('episode');url.searchParams.delete('season');
  return {seriesKey:url.origin+url.pathname+'?'+url.searchParams.toString(),episode,season,scope:season?'season':'global'};
};

// Display metadata is independent of subtitle-library names and episode mapping.
globalThis.subtitlePlayerMediaContext=(href,doc)=>{
  const url=new URL(href),context=globalThis.subtitlePlayerEpisodeContext(href);
  const netflix=/(^|\.)netflix\.com$/i.test(url.hostname);
  const node=doc.querySelector(netflix?'[data-uia="video-title"]':'h1.ytd-watch-metadata,meta[property="og:title"]');
  let title=(node?.getAttribute?.('content')||node?.textContent||(!netflix?doc.title:'')||'').trim();
  title=title.replace(/\s*(?:[-|]\s*)?(?:YouTube|Netflix)\s*$/i,'').trim();
  if(!title)return null; // Netflix controls fade: retain the last known title.
  const episodeMatch=netflix?title.match(/\bS(\d+)\s*[: ]\s*[EF](\d+)\b/i):null;
  const episode=context?.episode||(episodeMatch?Number(episodeMatch[2]):null);
  const season=context?.season||(episodeMatch?Number(episodeMatch[1]):null);
  return {title:title.slice(0,300),kind:episode?'episode':'movie',episode,season,pageKey:url.origin+url.pathname};
};
