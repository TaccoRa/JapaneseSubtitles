const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const context={URL};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../browser-extension/episode-navigation.js'),'utf8'),context);
const navigate=context.subtitlePlayerEpisodeNavigation;
const node=(href,disabled=false)=>({disabled,getAttribute:key=>key==='href'?href:null});
const documentFor=(links=[],selectors={})=>({querySelectorAll:()=>links,querySelector:key=>selectors[key]});

test('Miruro uses actual adjacent links and preserves the selected season',()=>{
  const href='https://www.miruro.tv/watch/series/title?ep=7&season=1';
  const wrong=node('?ep=8&season=2'),next=node('?ep=8&season=1'),previous=node('?ep=6&season=1');
  assert.equal(navigate('next',documentFor([wrong,next,previous]),href),next);
  assert.equal(navigate('previous',documentFor([wrong,next,previous]),href),previous);
  assert.throws(()=>navigate('next',documentFor([]),href),/unavailable/);
});
test('Miruro refuses external and different-series links',()=>{
  assert.throws(()=>navigate('next',documentFor([node('https://other.test/watch/a?ep=8'),node('/watch/b?ep=8')]),
    'https://miruro.tv/watch/a?ep=7'),/unavailable/);
});
test('Netflix next episode uses the real player control',()=>{
  const button=node('');
  assert.equal(navigate('next',documentFor([],{'[data-uia="next-episode-seamless-button"]':button}),
    'https://www.netflix.com/watch/81234567'),button);
});
test('Netflix previous episode follows the episode list rather than opaque IDs',()=>{
  const previous=node('/watch/89999999'),current=node('/watch/81234567'),next=node('/watch/80000001');
  const doc=documentFor([previous,current,next]);
  assert.equal(navigate('previous',doc,'https://www.netflix.com/watch/81234567'),previous);
  assert.equal(navigate('next',doc,'https://www.netflix.com/watch/81234567'),next);
});
test('Netflix modern episode-pane options support previous episode',()=>{
  const previous=node(''),current=node(''),next=node('');
  current.getAttribute=key=>key==='aria-selected'?'true':null;
  const doc={querySelector:()=>null,querySelectorAll:selector=>selector.includes('episode-pane-item')?[previous,current,next]:[]};
  assert.equal(navigate('previous',doc,'https://netflix.com/watch/80045950'),previous);
});
test('intro skip matches actual intro controls but never generic skip/ad controls',()=>{
  const skipAd={disabled:false,textContent:'Skip Ad',getAttribute:()=>null};
  const skipIntro={disabled:false,textContent:'Intro überspringen',getAttribute:()=>null};
  assert.equal(context.subtitlePlayerIntroTarget({querySelectorAll:()=>[skipAd,skipIntro]}),skipIntro);
  assert.equal(context.subtitlePlayerIntroTarget({querySelectorAll:()=>[skipAd]}),null);
});
test('Netflix refuses disabled controls, missing lists and episode boundaries',()=>{
  assert.throws(()=>navigate('next',documentFor([],{'[data-uia="control-next"]':node('',true)}),
    'https://netflix.com/watch/123'),/unavailable/);
  assert.throws(()=>navigate('previous',documentFor([node('/watch/123')]),'https://netflix.com/watch/123'),/unavailable/);
});
test('unsupported sites and invalid directions are explicit failures',()=>{
  assert.throws(()=>navigate('next',documentFor([]),'https://example.com/watch/123'),/unsupported/);
  assert.throws(()=>navigate('sideways',documentFor([]),'https://miruro.tv/watch/a?ep=7'),/Invalid/);
});
