/* Explicit site controls/links only. Never manufacture player IDs or episode URLs. */
globalThis.subtitlePlayerEpisodeNavigation = function(direction, doc = document, href = location.href) {
  if (!['next', 'previous'].includes(direction)) throw Error('Invalid episode direction');
  const url = new URL(href);
  const available = node => node && !node.disabled && node.getAttribute('aria-disabled') !== 'true'
    && (!node.getClientRects || node.getClientRects().length > 0);
  if (/(^|\.)miruro\.[a-z]+$/i.test(url.hostname)) {
    const current = Number(url.searchParams.get('ep'));
    const candidates = [...doc.querySelectorAll('a[href]')].map(node => {
      try { return {node, url: new URL(node.getAttribute('href'), href)}; } catch { return null; }
    }).filter(item => item && item.url.origin === url.origin && item.url.pathname === url.pathname
      && Number(item.url.searchParams.get('ep')) === current + (direction === 'next' ? 1 : -1)
      && item.url.searchParams.get('season') === url.searchParams.get('season') && available(item.node));
    if (!Number.isInteger(current) || current <= 0 || !candidates.length) throw Error('Adjacent Miruro episode link unavailable');
    return candidates[0].node;
  }
  if (/(^|\.)netflix\.com$/i.test(url.hostname)) {
    const selectors = direction === 'next'
      ? ['[data-uia="next-episode-seamless-button"]', '[data-uia="control-next"]', '[data-uia="next-episode"]']
      : ['[data-uia="control-previous"]', '[data-uia="previous-episode"]'];
    for (const selector of selectors) {
      const node = doc.querySelector(selector);
      if (available(node)) return node;
    }
    const items = [...doc.querySelectorAll('[data-uia="episode-pane-item"][role="option"]')];
    const selected = items.findIndex(node => node.getAttribute('aria-selected') === 'true');
    const adjacent = selected < 0 ? null : items[selected + (direction === 'next' ? 1 : -1)];
    if (available(adjacent)) return adjacent;
    const links = [...doc.querySelectorAll('[data-uia*="episode"] a[href*="/watch/"], a[data-uia*="episode"][href*="/watch/"]')];
    const index = links.findIndex(node => new URL(node.getAttribute('href'), href).pathname === url.pathname);
    const target = index < 0 ? null : links[index + (direction === 'next' ? 1 : -1)];
    if (available(target)) return target;
    throw Error('Netflix adjacent episode control unavailable; open the episode list and try again');
  }
  throw Error('Episode navigation unsupported on this website');
};

globalThis.subtitlePlayerFindEpisodeTarget = async function(direction) {
  try { return globalThis.subtitlePlayerEpisodeNavigation(direction); }
  catch (error) {
    if (!/(^|\.)netflix\.com$/i.test(location.hostname)) throw error;
    const menu = document.querySelector('[data-uia="control-episodes"]');
    if (!menu) throw error;
    menu.click();
    return await new Promise((resolve, reject) => {
      const timer = setTimeout(() => { observer.disconnect(); reject(error); }, 2000);
      const observer = new MutationObserver(() => {
        try {
          const target = globalThis.subtitlePlayerEpisodeNavigation(direction);
          observer.disconnect(); clearTimeout(timer); resolve(target);
        } catch {}
      });
      observer.observe(document.documentElement, {childList:true, subtree:true, attributes:true});
      try {
        const target = globalThis.subtitlePlayerEpisodeNavigation(direction);
        observer.disconnect(); clearTimeout(timer); resolve(target);
      } catch {}
    });
  }
};

globalThis.subtitlePlayerIntroTarget = function(doc = document) {
  const candidates = [...doc.querySelectorAll('[data-uia="player-skip-intro"], [data-uia="skip-intro"], button, [role="button"]')];
  return candidates.find(node => !node.disabled && node.getAttribute('aria-disabled') !== 'true'
    && (!node.getClientRects || node.getClientRects().length > 0)
    && (/^(player-skip-intro|skip-intro)$/.test(node.getAttribute('data-uia') || '')
      || /^(skip intro|skip opening|intro überspringen|vorspann überspringen|イントロをスキップ|オープニングをスキップ)$/i.test(
          (node.getAttribute('aria-label') || node.textContent || '').trim()))) || null;
};

globalThis.subtitlePlayerEmbeddedEpisodeTarget = function(direction, doc = document) {
  if(!['next','previous'].includes(direction))throw Error('Invalid episode direction');
  const label=direction==='next'?'Next Episode':'Previous Episode';
  const target=doc.querySelector(`[aria-label="${label}" i], [title="${label}" i]`);
  if(!target||target.disabled||target.getAttribute('aria-disabled')==='true')throw Error('Embedded episode control unavailable');
  return target;
};
