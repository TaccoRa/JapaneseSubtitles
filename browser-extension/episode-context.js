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
