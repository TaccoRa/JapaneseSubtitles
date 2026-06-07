(function(){
if (window.strokeAutoInit) return;
const DEFAULT_PREFIX = "stroke_";

function isKanji(ch){
  const c = ch.codePointAt(0);
  return (c>=0x3400&&c<=0x4DBF)||(c>=0x4E00&&c<=0x9FFF)||(c>=0xF900&&c<=0xFAFF)||(c>=0x20000&&c<=0x2A6DF)||(c>=0x2A700&&c<=0x2B81F)||(c>=0x2B820&&c<=0x2CEAF);
}
function uniqKanji(text){
  const seen=new Set(), out=[];
  for(const ch of text){ if(isKanji(ch)&&!seen.has(ch)){ seen.add(ch); out.push(ch); } }
  return out;
}
function toHex5(ch){ return ch.codePointAt(0).toString(16).padStart(5,'0'); }
function num(v,d){ const n=parseFloat(v); return Number.isFinite(n)?n:d; }
function strOr(v,d){ const s=(v==null)?'':String(v).trim(); return s!==''?s:d; }
function boolish(v,d){
  if(v==null||String(v).trim()==='') return d;
  const s=String(v).trim().toLowerCase();
  if(s==='1'||s==='true'||s==='yes'||s==='on') return true;
  if(s==='0'||s==='false'||s==='no'||s==='off') return false;
  const n=parseFloat(s); return Number.isFinite(n)?n!==0:d;
}
function sleep(ms){ return new Promise(function(r){ setTimeout(r,ms); }); }
function pathLength(p){ try{ const n=p.getTotalLength(); return Number.isFinite(n)&&n>0?n:100; }catch(_e){ return 100; } }
function orderedStrokes(doc){
  const out=[];
  doc.querySelectorAll('path[id*="-s"]').forEach(function(path){
    const m=(path.id||'').match(/-s(\d+)$/);
    if(m) out.push({index:parseInt(m[1],10), path:path});
  });
  out.sort(function(a,b){ return a.index-b.index; });
  return out;
}
function bumpToken(obj){
  const n=(parseInt(obj.dataset.strokeToken||'0',10)||0)+1;
  obj.dataset.strokeToken=String(n);
  return String(n);
}

function readSettings(el, conf){
  const css=getComputedStyle(el);
  const speed=Math.max(0.01, num(css.getPropertyValue('--stroke-speed'), num(conf.speed,1)));
  const gap=Math.max(0, num(css.getPropertyValue('--stroke-gap'), num(conf.gap,0.2)));
  const brush=boolish(css.getPropertyValue('--stroke-brush'), boolish(conf.brush,true));
  const backward=boolish(css.getPropertyValue('--stroke-backward'), boolish(conf.backward,false));
  const autoplay=boolish(css.getPropertyValue('--stroke-autoplay'), boolish(conf.autoplay,true));
  const idleFinal=boolish(css.getPropertyValue('--stroke-idle-final'), boolish(conf.idleFinal,true));
  const bg=strOr(css.getPropertyValue('--stroke-bg'), strOr(conf.bg,'#000'));
  const line=strOr(css.getPropertyValue('--stroke-line'), strOr(conf.line,'#fff'));
  const brushFill=strOr(css.getPropertyValue('--stroke-brush-fill'), strOr(conf.brushFill,'#e53935'));
  const brushStroke=strOr(css.getPropertyValue('--stroke-brush-stroke'), strOr(conf.brushStroke,'#555'));
  const grid=boolish(css.getPropertyValue('--stroke-grid'), boolish(conf.grid,true));
  const gridColor=strOr(css.getPropertyValue('--stroke-grid-color'), strOr(conf.gridColor,'#50463a'));
  const gridWidth=Math.max(0.3, num(css.getPropertyValue('--stroke-grid-width'), num(conf.gridWidth,2)));
  const gridBorderWidth=Math.max(0, num(css.getPropertyValue('--stroke-grid-border-width'), num(conf.gridBorderWidth,1.5)));
  const gridBorderColor=strOr(css.getPropertyValue('--stroke-grid-border-color'), strOr(conf.gridBorderColor,gridColor));
  const gridOpacity=Math.min(1, Math.max(0, num(css.getPropertyValue('--stroke-grid-opacity'), num(conf.gridOpacity,1))));
  const gridDash=strOr(css.getPropertyValue('--stroke-grid-dash'), strOr(conf.gridDash,'5 5'));
  const gridCenterHole=Math.max(0, num(css.getPropertyValue('--stroke-grid-center-hole'), num(conf.gridCenterHole,6)));
  return {
    speed:speed,gap:gap,brush:brush,backward:backward,autoplay:autoplay,idleFinal:idleFinal,
    bg:bg,line:line,brushFill:brushFill,brushStroke:brushStroke,
    grid:grid,gridColor:gridColor,gridWidth:gridWidth,gridBorderWidth:gridBorderWidth,gridBorderColor:gridBorderColor,gridOpacity:gridOpacity,gridDash:gridDash,gridCenterHole:gridCenterHole
  };
}

function svgBox(root){
  const raw=(root.getAttribute('viewBox')||'').trim();
  if(raw){
    const p=raw.split(/[\s,]+/).map(function(v){ return parseFloat(v); });
    if(p.length===4 && Number.isFinite(p[0]) && Number.isFinite(p[1]) && Number.isFinite(p[2]) && Number.isFinite(p[3]) && p[2]>0 && p[3]>0){
      return {x:p[0],y:p[1],w:p[2],h:p[3]};
    }
  }
  const w=Math.max(1, num(root.getAttribute('width'),109));
  const h=Math.max(1, num(root.getAttribute('height'),109));
  return {x:0,y:0,w:w,h:h};
}

function ensureGrid(doc, settings){
  const root=doc.querySelector('svg');
  if(!root) return;

  const ns='http://www.w3.org/2000/svg';
  let grid=doc.getElementById('anki-stroke-grid');
  if(!settings.grid){
    if(grid && grid.parentNode) grid.parentNode.removeChild(grid);
    return;
  }

  if(!grid || grid.getAttribute('data-grid-layout')!=='split-center-hole-v3'){
    if(grid && grid.parentNode) grid.parentNode.removeChild(grid);
    grid=doc.createElementNS(ns,'g');
    grid.setAttribute('id','anki-stroke-grid');
    grid.setAttribute('data-grid-layout','split-center-hole-v3');
    grid.setAttribute('pointer-events','none');
    grid.style.pointerEvents='none';
    grid.setAttribute('shape-rendering','geometricPrecision');

    const hLeft=doc.createElementNS(ns,'line');
    hLeft.setAttribute('id','anki-stroke-grid-hl');
    grid.appendChild(hLeft);

    const hRight=doc.createElementNS(ns,'line');
    hRight.setAttribute('id','anki-stroke-grid-hr');
    grid.appendChild(hRight);

    const vTop=doc.createElementNS(ns,'line');
    vTop.setAttribute('id','anki-stroke-grid-vt');
    grid.appendChild(vTop);

    const vBottom=doc.createElementNS(ns,'line');
    vBottom.setAttribute('id','anki-stroke-grid-vb');
    grid.appendChild(vBottom);

    const border=doc.createElementNS(ns,'rect');
    border.setAttribute('id','anki-stroke-grid-border');
    border.setAttribute('fill','none');
    grid.appendChild(border);

    const strokeGroup=doc.querySelector('g[id*="StrokePaths"]');
    if(strokeGroup && strokeGroup.parentNode===root){
      root.insertBefore(grid, strokeGroup);
    }else{
      root.insertBefore(grid, root.firstChild);
    }
  }

  const border=doc.getElementById('anki-stroke-grid-border');
  const hLeft=doc.getElementById('anki-stroke-grid-hl');
  const hRight=doc.getElementById('anki-stroke-grid-hr');
  const vTop=doc.getElementById('anki-stroke-grid-vt');
  const vBottom=doc.getElementById('anki-stroke-grid-vb');
  if(!border || !hLeft || !hRight || !vTop || !vBottom) return;

  const box=svgBox(root);
  const x=box.x;
  const y=box.y;
  const w=box.w;
  const h=box.h;
  const cx=x+(w/2);
  const cy=y+(h/2);
  const color=settings.gridColor;
  const opacity=String(settings.gridOpacity);
  const dash=(settings.gridDash||'').trim();
  const inset=Math.max(0,settings.gridBorderWidth/2);
  const xMin=x+inset;
  const xMax=x+w-inset;
  const yMin=y+inset;
  const yMax=y+h-inset;
  const maxHole=Math.max(0,Math.min(xMax-xMin,yMax-yMin)-1);
  const hole=Math.min(maxHole,Math.max(0,settings.gridCenterHole));
  const halfHole=hole/2;
  const leftEnd=cx-halfHole;
  const rightStart=cx+halfHole;
  const topEnd=cy-halfHole;
  const bottomStart=cy+halfHole;

  border.setAttribute('x', String(x));
  border.setAttribute('y', String(y));
  border.setAttribute('width', String(w));
  border.setAttribute('height', String(h));
  border.setAttribute('stroke', settings.gridBorderColor||color);
  border.setAttribute('stroke-width', String(settings.gridBorderWidth));
  border.setAttribute('opacity', opacity);
  border.removeAttribute('vector-effect');
  border.setAttribute('stroke-linecap', 'butt');

  const lines=[hLeft,hRight,vTop,vBottom];
  for(let i=0;i<lines.length;i++){
    const ln=lines[i];
    ln.setAttribute('stroke', color);
    ln.setAttribute('stroke-width', String(settings.gridWidth));
    ln.setAttribute('stroke-linecap', 'butt');
    ln.setAttribute('stroke-dasharray', dash||'none');
    ln.setAttribute('stroke-dashoffset', '0');
    ln.setAttribute('opacity', opacity);
    ln.removeAttribute('vector-effect');
  }

  hLeft.setAttribute('x1', String(leftEnd));
  hLeft.setAttribute('y1', String(cy));
  hLeft.setAttribute('x2', String(xMin));
  hLeft.setAttribute('y2', String(cy));

  hRight.setAttribute('x1', String(rightStart));
  hRight.setAttribute('y1', String(cy));
  hRight.setAttribute('x2', String(xMax));
  hRight.setAttribute('y2', String(cy));

  vTop.setAttribute('x1', String(cx));
  vTop.setAttribute('y1', String(topEnd));
  vTop.setAttribute('x2', String(cx));
  vTop.setAttribute('y2', String(yMin));

  vBottom.setAttribute('x1', String(cx));
  vBottom.setAttribute('y1', String(bottomStart));
  vBottom.setAttribute('x2', String(cx));
  vBottom.setAttribute('y2', String(yMax));
}

function prepareDoc(doc, settings){
  doc.querySelectorAll('g[id*="StrokeNumbers"], [id*="StrokeNumbers"], text').forEach(function(el){ el.style.display='none'; });
  doc.querySelectorAll('rect').forEach(function(rect){
    const fill=(rect.getAttribute('fill')||'').trim().toLowerCase();
    if(fill==='white'||fill==='#fff'||fill==='#ffffff') rect.setAttribute('fill','none');
  });
  const root=doc.querySelector('svg');
  if(root){
    root.style.setProperty('display','block','important');
    root.style.setProperty('overflow','hidden','important');
    root.style.setProperty('background',settings.bg,'important');
    root.style.setProperty('width','100%','important');
    root.style.setProperty('height','100%','important');
  }
  let style=doc.getElementById('anki-stroke-force-style');
  if(!style){
    style=doc.createElement('style');
    style.id='anki-stroke-force-style';
    style.textContent='svg{display:block;overflow:hidden;background:transparent !important;} path[id*="-s"]{fill:none !important;stroke-linecap:round;stroke-linejoin:round;vector-effect:non-scaling-stroke;}';
    (doc.head||doc.documentElement).appendChild(style);
  }
  ensureGrid(doc, settings);
}

function ensureBrush(doc, settings){
  const ns='http://www.w3.org/2000/svg';
  const root=doc.querySelector('svg');
  if(!root) return null;
  let brush=doc.getElementById('anki-stroke-brush');
  if(!brush){
    brush=doc.createElementNS(ns,'circle');
    brush.setAttribute('id','anki-stroke-brush');
    brush.setAttribute('r','3.2');
    brush.setAttribute('stroke-width','1');
    brush.style.display='none';
    root.appendChild(brush);
  }
  brush.setAttribute('fill',settings.brushFill);
  brush.setAttribute('stroke',settings.brushStroke);
  return brush;
}

function primeStrokes(strokes, settings){
  for(let i=0;i<strokes.length;i++){
    const p=strokes[i].path;
    const len=pathLength(p);
    p.dataset.strokeLength=String(len);
    p.style.fill='none';
    p.style.setProperty('stroke',settings.line,'important');
    p.style.strokeDasharray=String(len);
    p.style.transition='none';
    p.style.strokeDashoffset=settings.backward?'0':String(len);
  }
}
function finishStrokes(strokes, settings){
  for(let i=0;i<strokes.length;i++){
    const p=strokes[i].path;
    p.style.setProperty('stroke',settings.line,'important');
    p.style.transition='none';
    p.style.strokeDashoffset='0';
  }
}

function animateBrush(path, brush, durationSec, backward){
  if(!brush) return Promise.resolve();
  return new Promise(function(resolve){
    const total=pathLength(path), start=performance.now(), dur=Math.max(1,durationSec*1000);
    brush.style.display='';
    function tick(now){
      const t=Math.min(1,(now-start)/dur);
      const ratio=backward?(1-t):t;
      const pt=path.getPointAtLength(total*ratio);
      brush.setAttribute('cx',String(pt.x));
      brush.setAttribute('cy',String(pt.y));
      if(t<1) requestAnimationFrame(tick);
      else { brush.style.display='none'; resolve(); }
    }
    requestAnimationFrame(tick);
  });
}

async function runObject(obj, conf, force){
  const doc=obj.contentDocument;
  if(!doc){
    obj.style.visibility='hidden';
    obj.style.opacity='0';
    return;
  }
  const settings=readSettings(obj, conf||obj.__strokeConf||{});
  obj.__strokeConf=Object.assign({}, obj.__strokeConf||{}, conf||{});

  prepareDoc(doc, settings);
  const strokes=orderedStrokes(doc);
  if(!strokes.length){
    obj.style.visibility='hidden';
    obj.style.opacity='0';
    const tries=(parseInt(obj.dataset.strokeInitRetries||'0',10)||0)+1;
    obj.dataset.strokeInitRetries=String(tries);
    if(tries<=8){
      setTimeout(function(){ runObject(obj, obj.__strokeConf||{}, force).catch(function(){}); }, Math.min(320, 40*tries));
    }
    return;
  }
  obj.dataset.strokeInitRetries='0';

  if(!force && settings.autoplay && obj.dataset.strokeAnimated==='1'){
    obj.style.visibility='visible';
    obj.style.opacity='1';
    return;
  }

  const token=bumpToken(obj);
  primeStrokes(strokes, settings);
  obj.style.visibility='visible';
  obj.style.opacity='1';

  if(!settings.autoplay && !force){
    obj.dataset.strokeAnimated='0';
    if(settings.idleFinal) finishStrokes(strokes, settings);
    return;
  }

  obj.dataset.strokeAnimated='1';
  const brush=settings.brush?ensureBrush(doc,settings):null;
  for(let i=0;i<strokes.length;i++){
    if(obj.dataset.strokeToken!==token) return;
    const path=strokes[i].path;
    const len=num(path.dataset.strokeLength,100);
    const duration=Math.max(0.15, Math.sqrt(len)/10)/settings.speed;
    path.style.setProperty('stroke',settings.line,'important');
    path.style.transition='none';
    path.style.strokeDashoffset=settings.backward?'0':String(len);
    path.getBoundingClientRect();
    path.style.transition='stroke-dashoffset '+duration+'s ease-in-out';
    path.style.strokeDashoffset=settings.backward?String(len):'0';
    const b=animateBrush(path,brush,duration,settings.backward);
    await sleep((duration+settings.gap)*1000);
    await b;
  }
}

function attachObject(obj, conf){
  obj.__strokeConf=Object.assign({}, obj.__strokeConf||{}, conf||{});
  if(obj.dataset.strokeBound==='1'){
    runObject(obj, obj.__strokeConf, false).catch(function(){});
    return;
  }
  obj.dataset.strokeBound='1';
  obj.style.visibility='hidden';
  obj.style.opacity='0';
  obj.style.display='block';
  obj.style.border='0';
  obj.style.outline='0';
  obj.style.background='var(--stroke-bg,#000)';
  obj.style.overflow='hidden';

  let lastTap=0;
  const replay=function(e){
    const now=Date.now();
    if(now-lastTap<220) return;
    lastTap=now;
    if(e){
      e.preventDefault();
      e.stopPropagation();
      if(e.stopImmediatePropagation) e.stopImmediatePropagation();
    }
    if(!obj.contentDocument){
      obj.addEventListener('load', function(){
        runObject(obj, obj.__strokeConf||{}, true).catch(function(){});
      }, {once:true});
      return;
    }
    runObject(obj, obj.__strokeConf||{}, true).catch(function(){});
  };
  obj.__strokeReplay=function(){ replay(); };

  const bindInner=function(doc){
    if(!doc||doc.__strokeInnerBound) return;
    doc.__strokeInnerBound=1;
    doc.addEventListener('pointerdown', replay, true);
    doc.addEventListener('touchstart', replay, true);
    doc.addEventListener('click', replay, true);
  };

  const start=function(){
    runObject(obj, obj.__strokeConf||{}, false).catch(function(){
      obj.style.visibility='hidden';
      obj.style.opacity='0';
    });
    bindInner(obj.contentDocument);
  };

  if(obj.contentDocument) start();
  obj.addEventListener('load', start, {once:true});
  obj.addEventListener('pointerdown', replay, false);
  obj.addEventListener('touchstart', replay, false);
  obj.addEventListener('click', replay, false);
}

function getExistingRow(kanjiEl){
  let next=kanjiEl.nextElementSibling;
  while(next){
    if(next.classList&&next.classList.contains('stroke-order-row')) return next;
    if(next.tagName&&next.tagName.toLowerCase()==='br'){ next=next.nextElementSibling; continue; }
    break;
  }
  return null;
}

function setRowCount(row, n){ row.setAttribute('data-stroke-count', String(Math.max(0,n))); }
function expectedName(prefix, ch){ return (prefix||DEFAULT_PREFIX)+toHex5(ch)+'.svg'; }

function ensureToggleStyle(){
  if(document.getElementById('anki-stroke-toggle-style')) return;
  const style=document.createElement('style');
  style.id='anki-stroke-toggle-style';
  style.textContent=
    '.kanji .stroke-order-toggle{display:inline-block;vertical-align:middle;box-sizing:border-box;width:var(--stroke-toggle-size,0.68em);height:var(--stroke-toggle-size,0.68em);min-width:var(--stroke-toggle-size,0.68em);min-height:var(--stroke-toggle-size,0.68em);margin-left:var(--stroke-toggle-gap,0.22em);transform:translate(var(--stroke-toggle-offset-x,0),var(--stroke-toggle-offset-y,0));border:var(--stroke-toggle-border,1px solid currentColor);border-radius:var(--stroke-toggle-radius,0);background:var(--stroke-toggle-bg,transparent);color:var(--stroke-toggle-color,currentColor);padding:0;line-height:1;cursor:pointer;-webkit-appearance:none;appearance:none;-webkit-tap-highlight-color:transparent;}' +
    '.kanji .stroke-order-toggle:focus-visible{outline:2px solid var(--stroke-toggle-focus,#6aa9ff);outline-offset:1px;}' +
    '.stroke-order-row.stroke-order-row-hidden{max-height:0 !important;opacity:0 !important;overflow:hidden !important;margin-top:0 !important;margin-bottom:0 !important;pointer-events:none !important;}';
  (document.head||document.documentElement).appendChild(style);
}

function ensureToggleButton(kanjiEl){
  let btn=kanjiEl.querySelector('.stroke-order-toggle');
  if(!btn){
    btn=document.createElement('button');
    btn.type='button';
    btn.className='stroke-order-toggle';
    btn.setAttribute('aria-label','Show stroke order');
    btn.setAttribute('title','Show stroke order');
    btn.textContent='';
    kanjiEl.appendChild(btn);
  }
  return btn;
}

function replayRow(row){
  if(!row) return;
  row.classList.remove('stroke-order-row-hidden');
  row.setAttribute('data-stroke-visible','1');
  row.querySelectorAll('object.stroke-order-media').forEach(function(obj){
    if(typeof obj.__strokeReplay==='function'){
      obj.__strokeReplay();
      return;
    }
    runObject(obj, obj.__strokeConf||{}, true).catch(function(){});
  });
}

function bindToggleButton(btn, row){
  if(!btn||!row) return;
  btn.__strokeRow=row;
  btn.setAttribute('aria-expanded', row.classList.contains('stroke-order-row-hidden')?'false':'true');
  if(btn.dataset.strokeToggleBound==='1') return;
  btn.dataset.strokeToggleBound='1';
  let lastTap=0;
  const onToggle=function(e){
    const now=Date.now();
    if(e.type==='click' && now-lastTap<260) return;
    if(e.type!=='click') lastTap=now;
    e.preventDefault();
    e.stopPropagation();
    if(e.stopImmediatePropagation) e.stopImmediatePropagation();
    replayRow(btn.__strokeRow);
    btn.setAttribute('aria-expanded','true');
  };
  if(window.PointerEvent){
    btn.addEventListener('pointerdown', onToggle, true);
  }else{
    btn.addEventListener('touchstart', onToggle, true);
  }
  btn.addEventListener('click', onToggle, true);
}

function ensureRow(row, chars, conf){
  setRowCount(row, chars.length);
  const existing=Array.from(row.querySelectorAll('object.stroke-order-media'));
  const expected=chars.map(function(ch){ return expectedName(conf.prefix, ch); });

  let reuse = existing.length===expected.length;
  if(reuse){
    for(let i=0;i<existing.length;i++){
      const data=(existing[i].getAttribute('data')||'').trim();
      if(data!==expected[i]){ reuse=false; break; }
    }
  }

  if(!reuse){
    row.innerHTML='';
    for(let i=0;i<chars.length;i++){
      const obj=document.createElement('object');
      obj.type='image/svg+xml';
      obj.className='stroke-order-media stroke-svg-object';
      obj.style.visibility='hidden';
      obj.style.opacity='0';
      obj.style.display='block';
      obj.style.border='0';
      obj.style.outline='0';
      obj.style.background='var(--stroke-bg,#000)';
      obj.style.overflow='hidden';
      obj.setAttribute('data', expected[i]);
      row.appendChild(obj);
    }
  }

  row.querySelectorAll('object.stroke-order-media').forEach(function(obj){ attachObject(obj, conf); });
}

function setup(conf){
  const cfg=Object.assign({}, window.strokeAutoConfig||{}, conf||{});
  if(!cfg.prefix) cfg.prefix=DEFAULT_PREFIX;
  const toggleButton=boolish(cfg.toggleButton,false);
  const hideUntilToggle=boolish(cfg.hideUntilToggle,toggleButton);
  if(toggleButton) ensureToggleStyle();
  document.querySelectorAll('.kanji').forEach(function(kanjiEl){
    const raw=(kanjiEl.innerText||kanjiEl.textContent||'').replace(/\[[^\]]*]/g,'').replace(/\s+/g,'');
    const chars=uniqKanji(raw);
    if(!chars.length) return;
    let row=getExistingRow(kanjiEl);
    if(!row){
      row=document.createElement('div');
      row.className='stroke-order-row';
      kanjiEl.insertAdjacentElement('afterend', row);
    }
    ensureRow(row, chars, cfg);
    if(toggleButton){
      const btn=ensureToggleButton(kanjiEl);
      bindToggleButton(btn, row);
      if(hideUntilToggle && row.getAttribute('data-stroke-visible')!=='1'){
        row.classList.add('stroke-order-row-hidden');
      }else{
        row.classList.remove('stroke-order-row-hidden');
      }
    }else{
      row.classList.remove('stroke-order-row-hidden');
    }
  });
}

window.strokeAutoInit=setup;
})();
