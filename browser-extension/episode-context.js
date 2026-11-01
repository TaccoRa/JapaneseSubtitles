/* Explicit episode metadata only. A video-clock reset is never an episode number. */
globalThis.subtitlePlayerEpisodeContext=href=>{
  const url=new URL(href),positive=value=>/^\d{1,4}$/.test(value||'')&&Number(value)>0?Number(value):null;
  let episode=null,season=null;
  if(/(^|\.)miruro\.[a-z]+$/i.test(url.hostname)&&url.pathname.startsWith('/watch/')){
    episode=positive(url.searchParams.get('ep'));season=positive(url.searchParams.get('season'));
    const parts=url.pathname.split('/');
    // Miruro gives each season a separate watch ID; ep is season-local.
    // Prefer an explicit parameter, then an unambiguous season marker in its slug.
    let slug=parts[3]||'';
    try{slug=decodeURIComponent(slug);}catch{}
    const marker=slug.match(/(?:^|[-_])(?:season[-_](\d{1,3})|(\d{1,3})(?:st|nd|rd|th)?[-_]season)(?=[-_]|$)/i);
    if(!season&&marker)season=positive(marker[1]||marker[2]);
    const id=parts[2];
    return episode?{seriesKey:url.origin+'/watch/'+id,episode,season,scope:'season'}:null;
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
  const evidence=netflix?doc.querySelector('[data-uia="evidence-overlay"]'):null;
  const node=evidence?.querySelector('h2')||doc.querySelector(netflix?'[data-uia="video-title"]':'h1.ytd-watch-metadata,main h1');
  // SPA navigation updates document.title; og:title often remains the first anime.
  let title=(node?.textContent||(!netflix?doc.title:'')||(!netflix?doc.querySelector('meta[property="og:title"]')?.content:'')||'').trim();
  if(/(^|\.)miruro\.[a-z]+$/i.test(url.hostname)){
    const parts=url.pathname.split('/').filter(Boolean);
    if(['watch','info'].includes(parts[0])&&parts[2]){
      try{title=decodeURIComponent(parts[2]).replace(/-/g,' ').trim();}catch{}
    }
  }
  title=title.replace(/\s*(?:[-|]\s*)?(?:YouTube|Netflix)\s*$/i,'').trim();
  if(!title)return null; // Netflix controls fade: retain the last known title.
  const episodeMatch=netflix?title.match(/\bS(\d+)\s*[: ]\s*[EF](\d+)\b/i):null;
  const episodeLabel=evidence?.querySelector('[data-uia="evidence-overlay-episode-title"]')?.textContent||'';
  const seasonLabel=evidence?.querySelector('[data-uia="evidence-overlay-season-title"]')?.textContent||'';
  const numberedEpisode=episodeLabel.match(/(?:\b(?:Ep\.?|Episode|Folge)\s*|第\s*)(\d{1,4})(?:\b|\s*話)/i);
  const numberedSeason=seasonLabel.match(/(?:\b(?:Season|Staffel)\s*|シーズン\s*)(\d{1,3})\b/i);
  const episode=context?.episode||(episodeMatch?Number(episodeMatch[2]):null)||(numberedEpisode?Number(numberedEpisode[1]):null);
  const season=context?.season||(episodeMatch?Number(episodeMatch[1]):null)||(numberedSeason?Number(numberedSeason[1]):null);
  // The compact controls concatenate the series, episode number and title.
  // Keep the series name usable as a Jimaku alias across episode changes.
  if(netflix&&!evidence)title=title.replace(/\s*S\d+\s*[: ]?\s*[EF]\d+\b.*$/i,'').replace(/\s*E\d+\b.*$/,'').trim();
  return {title:title.slice(0,300),kind:episode?'episode':'movie',episode,season,
    ...(netflix&&episode?{seriesKey:url.origin+'/series/'+encodeURIComponent(title.toLowerCase())}:{}),
    pageKey:url.origin+url.pathname,pageUrl:url.href,scope:context?.scope||(season?'season':'global')};
};
