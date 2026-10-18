(function () {
  const VERSION = '20261001-protected-svg-layout';
  if (window.ankiStudyController && window.ankiStudyController.version === VERSION) {
    window.ankiStudyController.mount();
    return;
  }
  if (window.ankiStudyController) window.ankiStudyController.dispose();
  let active = null;
  const memory = window.__ankiStudyDropdownMemory || (window.__ankiStudyDropdownMemory = {});
  const number = (v, fallback) => Number.isFinite(parseFloat(v)) ? parseFloat(v) : fallback;
  const yes = (v, fallback) => {
    v = String(v || '').trim().toLowerCase();
    return ['true','1','on','yes'].includes(v) ? true : ['false','0','off','no'].includes(v) ? false : fallback;
  };
  const key = setting => 'anki-study-dropdown:1645113496789:' + setting;
  function readState(setting) {
    try {
      const value = sessionStorage.getItem(key(setting));
      if (value === 'true' || value === 'false') return value === 'true';
    } catch (_) {}
    return typeof memory[key(setting)] === 'boolean' ? memory[key(setting)] : null;
  }
  function saveState(setting, open) {
    memory[key(setting)] = open;
    try { sessionStorage.setItem(key(setting), String(open)); } catch (_) {}
  }
  const alive = state => active === state && state.root.isConnected;
  function dispose() {
    if (!active) return;
    active.abort.abort();
    active.observer.disconnect();
    cancelAnimationFrame(active.frame);
    clearTimeout(active.retry);
    active = null;
  }
  function schedule(state) {
    if (!alive(state) || state.frame) return;
    state.frame = requestAnimationFrame(function () {
      state.frame = 0;
      if (alive(state)) fit(state);
    });
  }
  function measureSection(state, section, css) {
    const summary = section.querySelector('summary');
    const setting = section.dataset.dropdownSetting;
    const width = Math.floor(state.root.clientWidth);
    const signature = [width, section.querySelector('.study-extra-content').innerHTML,
      css.fontSize, css.fontFamily, css.getPropertyValue(setting.replace('-dropdown','-title')),
      ...Array.from(section.querySelectorAll('img')).map(img => img.naturalWidth + ':' + img.naturalHeight)].join('|');
    if (section.__studyMeasureSignature === signature) return;
    // Measure a separate invisible copy. Never open a real section for layout.
    const host = document.createElement('div');
    host.setAttribute('aria-hidden','true');
    host.style.cssText = 'position:fixed;left:-100000px;top:0;visibility:hidden;pointer-events:none;contain:layout style;overflow-anchor:none;';
    host.style.width = width + 'px';
    host.style.font = css.font;
    const copy = section.cloneNode(true);
    copy.removeAttribute('id');
    copy.querySelectorAll('[id]').forEach(el => el.removeAttribute('id'));
    copy.open = true;
    copy.dataset.dropdownEnabled = 'true';
    copy.style.width = 'fit-content';
    ['--sentence-ja-title','--sentence-de-title','--image-title'].forEach(prop => copy.style.setProperty(prop, css.getPropertyValue(prop)));
    host.appendChild(copy);
    state.root.appendChild(host);
    const gap = number(css.fontSize,35) * 0.35;
    const toggleWidth = copy.querySelector('summary').getBoundingClientRect().width + gap;
    copy.style.setProperty('--study-toggle-width', toggleWidth + 'px');
    const expandedWidth = Math.ceil(copy.getBoundingClientRect().width);
    host.remove();
    section.style.setProperty('--study-toggle-width', toggleWidth + 'px');
    section.style.setProperty('--study-section-width', expandedWidth + 'px');
    section.__studyMeasureSignature = signature;
  }
  function applySections(state) {
    if (!alive(state)) return;
    state.root.querySelectorAll('details[data-dropdown-setting]').forEach(section => {
      const css = getComputedStyle(section);
      const setting = section.dataset.dropdownSetting;
      const enabled = yes(css.getPropertyValue(setting), true);
      const defaultOpen = yes(css.getPropertyValue(setting.replace('-dropdown','-open-default')), false);
      const remember = yes(css.getPropertyValue('--dropdown-remember-state'), true);
      const config = [enabled,defaultOpen,remember].join(':');
      if (section.__studyConfig !== config) {
        section.__studyConfig = config;
        section.dataset.dropdownEnabled = String(enabled);
        const saved = remember ? readState(setting) : null;
        section.open = !enabled || (saved === null ? defaultOpen : saved);
      }
      if (enabled) measureSection(state,section,css);
    });
  }
  function fit(state) {
    if (!alive(state)) return;
    applySections(state);
    const root = state.root;
    const front = root.querySelector('.study-front');
    const card = document.querySelector('.card');
    if (!front || !card) return;
    const css = getComputedStyle(card);
    const min = Math.max(1,number(css.getPropertyValue('--stroke-tile-min'),80));
    const max = Math.max(0,number(css.getPropertyValue('--stroke-grid-max-width'),600));
    const padding = Math.max(0,number(css.getPropertyValue('--study-fit-padding'),8));
    const viewportHeight = window.visualViewport ? window.visualViewport.height : window.innerHeight;
    const end = root.querySelector('.study-translation') || front;
    // On a reverse answer, German extras keep their position above the Japanese
    // section. Bound this optional area so an open picture cannot hide the word.
    const extras = root.querySelector('.study-german-extras');
    if (root.dataset.direction === 'de-ja' && root.dataset.side === 'answer' && extras) {
      extras.style.overflowY = 'auto';
      const word = front.querySelector('.kanji');
      // Subtract the current extras height mathematically. Collapsing the live
      // element to measure it would clamp the page's scroll position to zero.
      const extrasHeight = extras.getBoundingClientRect().height;
      const wordBottomWithoutExtras = word
        ? word.getBoundingClientRect().bottom + window.scrollY - extrasHeight : 0;
      const available = Math.max(0,viewportHeight-wordBottomWithoutExtras-padding);
      // In unusually short windows let the page scroll instead of hiding controls.
      extras.style.maxHeight = available >= 60 ? Math.floor(Math.min(available,viewportHeight*0.35))+'px' : 'none';
    }
    const rows = Array.from(front.querySelectorAll('.stroke-order-row')).map(el => {
      const count = el.querySelectorAll('.stroke-order-media').length;
      const style = getComputedStyle(el);
      return {el,count,cols:count===1?1:2,lines:Math.ceil(count/2),gapX:number(style.columnGap,0),gapY:number(style.rowGap,0)};
    }).filter(row => row.count);
    if (!rows.length) return;
    const widthLimit = Math.min(...rows.map(row => (Math.min(row.el.clientWidth,max || Infinity)-row.gapX*(row.cols-1))/row.cols));
    const upper = Math.floor(widthLimit);
    if (upper < 1) return;
    const lower = Math.min(upper,Math.ceil(min));
    function apply(size) {
      rows.forEach(row => {
        const height = size*row.lines+row.gapY*(row.lines-1);
        row.el.style.setProperty('--stroke-cols',String(row.cols));
        row.el.style.setProperty('--stroke-size',size+'px');
        row.el.style.height=height+'px';row.el.style.flex='0 0 '+height+'px';
      });
    }
    // Only grid height changes with square size. Compute that budget directly
    // instead of shrinking the real page repeatedly during a binary search.
    const currentGridHeight = rows.reduce((sum,row) => sum + row.el.getBoundingClientRect().height, 0);
    const fixedBottom = end.getBoundingClientRect().bottom + window.scrollY - currentGridHeight;
    const lines = rows.reduce((sum,row) => sum + row.lines, 0);
    const gaps = rows.reduce((sum,row) => sum + row.gapY * (row.lines-1), 0);
    const heightLimit = Math.floor((viewportHeight-padding-fixedBottom-gaps) / lines);
    apply(Math.max(lower, Math.min(upper, heightLimit)));
  }
  function highlight(state) {
    const kanji = state.root.querySelector('.kanji');
    if (!kanji) return;
    const copy=kanji.cloneNode(true);
    copy.querySelectorAll('rt,.sound').forEach(el=>el.remove());
    const expression=(copy.textContent||'').replace(/\[[^\]]*]/g,'').replace(/\s+/g,'');
    if (expression) state.root.querySelectorAll('.sentenceJA').forEach(sentence=>{
      const nodes=[],mapping=[];let flat='';
      const walker=document.createTreeWalker(sentence,NodeFilter.SHOW_TEXT,{acceptNode(node){return node.parentElement.closest('rt')?NodeFilter.FILTER_REJECT:NodeFilter.FILTER_ACCEPT;}});
      while(walker.nextNode()) {
        const node=walker.currentNode;nodes.push(node);
        for(let i=0;i<node.nodeValue.length;i++){mapping.push({node,offset:i});flat+=node.nodeValue[i];}
      }
      const selected=new Map();let from=0;
      for(;;){const at=flat.indexOf(expression,from);if(at<0)break;
        for(let i=at;i<at+expression.length;i++){const m=mapping[i];if(!selected.has(m.node))selected.set(m.node,new Set());selected.get(m.node).add(m.offset);}from=at+expression.length;}
      selected.forEach((offsets,node)=>{
        const frag=document.createDocumentFragment(),text=node.nodeValue;let i=0;
        while(i<text.length){const matched=offsets.has(i);let end=i+1;while(end<text.length&&offsets.has(end)===matched)end++;
          if(matched){const span=document.createElement('span');span.className='highlight';span.textContent=text.slice(i,end);frag.appendChild(span);}
          else frag.appendChild(document.createTextNode(text.slice(i,end)));i=end;}
        node.replaceWith(frag);
      });
    });
    if(state.root.dataset.side==='answer') {
      kanji.querySelectorAll('ruby').forEach(el=>el.classList.add('show'));
      state.root.querySelectorAll('.sentenceJA ruby').forEach(el=>{if(el.querySelector('.highlight'))el.classList.add('show');});
    }
  }
  function mount() {
    const root=document.querySelector('.study-card');
    if(!root)return;
    if(active && active.root===root){schedule(active);return;}
    dispose();
    const state={root,abort:new AbortController(),frame:0,retry:0,attempts:0,observer:null};
    active=state;
    const signal=state.abort.signal;
    state.observer=new ResizeObserver(()=>schedule(state));
    state.observer.observe(root);
    root.addEventListener('click',event=>{
      const summary=event.target.closest('summary');
      if(summary && root.contains(summary)) {
        const section=summary.parentElement;
        if(!section.matches('details[data-dropdown-setting]')||section.dataset.dropdownEnabled==='false')return;
        event.preventDefault();section.open=!section.open;
        if(yes(getComputedStyle(section).getPropertyValue('--dropdown-remember-state'),true))saveState(section.dataset.dropdownSetting,section.open);
        schedule(state);return;
      }
      const ruby=event.target.closest('ruby');
      if(ruby){event.preventDefault();ruby.classList.toggle('show');schedule(state);}
    },{signal});
    root.addEventListener('load',()=>schedule(state),{capture:true,signal});
    window.addEventListener('resize',()=>schedule(state),{signal});
    window.addEventListener('orientationchange',()=>schedule(state),{signal});
    highlight(state);
    applySections(state);
    function initStrokes() {
      if(!alive(state))return;
      if(root.querySelector('.kanji')) {
        if(!window.strokeAutoInit){if(state.attempts++<60)state.retry=setTimeout(initStrokes,50);return;}
        window.strokeAutoInit({prefix:'_stroke_',autoplay:root.dataset.side==='answer'?1:0,idleFinal:1,maxKanji:0});
      }
      fit(state);
    }
    initStrokes();
    const shown=()=>{
      if(!alive(state))return;
      fit(state);
      // Starting at the top belongs to the first display, never later refreshes.
      if(!state.shownOnce){state.shownOnce=true;window.scrollTo(0,0);}
    };
    if(typeof onUpdateHook!=='undefined')onUpdateHook.push(()=>schedule(state));
    if(typeof onShownHook!=='undefined')onShownHook.push(shown);
    if(document.fonts)document.fonts.ready.then(()=>schedule(state));
    schedule(state);
  }
  window.ankiStudyController={version:VERSION,mount,dispose};
  window.ankiStudySections={apply:()=>{if(active)applySections(active);}};
  window.ankiStrokeLayout={fit:()=>{if(active)fit(active);},schedule:()=>{if(active)schedule(active);}};
  mount();
})();
