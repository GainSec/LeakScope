const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');const {JSDOM}=require('jsdom');
const group=(key,name,links,open=true)=>`<details class="nav-group" data-nav-group="${key}"${open?' open':''}><summary>${name}</summary><nav>${links.map(([nav,href,label])=>`<a href="${href}" data-nav="${nav}"><span class="nav-icon"></span><span class="nav-title">${label}</span></a>`).join('')}</nav></details>`;
const SHELL=`<button id="motion-toggle"></button><span id="breadcrumb-group"></span>
<details id="status-menu" class="status-menu"><summary>Status</summary><button id="status-inner" type="button">Inner</button></details>
<aside id="sidebar"><button id="command-toggle"></button>
<nav><a href="/" data-nav="overview"><span class="nav-icon"></span><span class="nav-title">Home</span></a></nav>
${group('find','Find',[['discover','/discover/','Discover']])}
${group('evidence','Evidence',[['assets','/assets/','Observations'],['findings','/findings/','Findings']])}
${group('tools','Tools',[['preview','/preview/','Inspect a target'],['analysis','/analysis/','Analysis']],false)}
<div id="command-extra" hidden><a href="/explore/">Explore</a><a href="/technologies/#technology-signatures">Technology signatures</a></div>
<a href="/settings/" data-nav="settings"><span class="nav-title">Settings</span></a><button id="nav-compact-toggle"></button></aside>
<main id="page-root"></main><dialog id="workspace-commands"><input id="command-search"><div id="command-results"></div><button data-close-command>Close</button></dialog>`;
function load({reduced=false,stored={},page='discover',blockStorage=false}={}){
  const dom=new JSDOM(`<body data-page="${page}">${SHELL}</body>`,{url:'http://localhost/discover/',runScripts:'outside-only'});const w=dom.window;
  w.matchMedia=()=>({matches:reduced,addEventListener(){}});w.HTMLDialogElement.prototype.showModal=function(){this.open=true};w.HTMLDialogElement.prototype.close=function(){this.open=false};
  for(const [k,v]of Object.entries(stored))w.localStorage.setItem(k,v);
  if(blockStorage){const fail=()=>{throw Error('blocked')};w.Storage.prototype.getItem=fail;w.Storage.prototype.setItem=fail}
  w.fetch=()=>{throw Error('No requests allowed')};
  w.eval(fs.readFileSync(path.resolve('leakscope_app/static/js/leakscope/workspace.js'),'utf8'));return dom}

test('navigation starts labelled and only uses the rail when the person chose it',()=>{
  let dom=load(),d=dom.window.document;
  assert.equal(d.body.classList.contains('nav-compact'),false);
  assert.equal(d.querySelector('#nav-compact-toggle').getAttribute('aria-expanded'),'true');
  assert.equal(dom.window.localStorage.getItem('leakscope.nav.mode'),null);
  d.querySelector('#nav-compact-toggle').click();
  assert.equal(d.body.classList.contains('nav-compact'),true);
  assert.equal(d.querySelector('#nav-compact-toggle').getAttribute('aria-expanded'),'false');
  assert.equal(d.querySelector('#nav-compact-toggle').getAttribute('aria-label'),'Show labels');
  assert.equal(dom.window.localStorage.getItem('leakscope.nav.mode'),'rail');
  dom.window.close();
  dom=load({stored:{'leakscope.nav.mode':'rail'}});
  assert.equal(dom.window.document.body.classList.contains('nav-compact'),true);
  dom.window.close();
});
test('tools group is closed by default and opens for the page it holds',()=>{
  let dom=load({page:'assets'});
  assert.equal(dom.window.document.querySelector('[data-nav-group=tools]').open,false);
  assert.equal(dom.window.document.querySelector('[data-nav-group=evidence]').open,true);
  dom.window.close();
  dom=load({page:'explore',stored:{'leakscope.nav.group.tools':'false'}});
  assert.equal(dom.window.document.querySelector('[data-nav-group=tools]').open,true);
  assert.equal(dom.window.LeakScopeShell.navKey('explore'),'preview');
  assert.equal(dom.window.LeakScopeShell.navKey('detail'),'findings');
  assert.equal(dom.window.LeakScopeShell.navKey('assets'),'assets');
  dom.window.close();
  dom=load({page:'detail',stored:{'leakscope.nav.group.evidence':'false'}});
  assert.equal(dom.window.document.querySelector('[data-nav-group=evidence]').open,true);
  dom.window.close();
  dom=load({page:'assets',stored:{'leakscope.nav.group.tools':'true','leakscope.nav.group.find':'false'}});
  assert.equal(dom.window.document.querySelector('[data-nav-group=tools]').open,true);
  assert.equal(dom.window.document.querySelector('[data-nav-group=find]').open,false);
  dom.window.close();
});
test('blocked storage still renders the labelled sidebar',()=>{
  const dom=load({blockStorage:true}),d=dom.window.document;
  assert.equal(d.body.classList.contains('nav-compact'),false);
  d.querySelector('#nav-compact-toggle').click();
  assert.equal(d.body.classList.contains('nav-compact'),true);
  dom.window.close();
});
test('jump-to search lists pages that have no sidebar entry of their own',()=>{
  const dom=load(),d=dom.window.document;
  d.querySelector('#command-toggle').click();
  const names=[...d.querySelectorAll('#command-results a')].map(a=>a.textContent.trim());
  assert.ok(names.includes('Explore'));assert.ok(names.includes('Technology signatures'));assert.ok(names.includes('Inspect a target'));
  dom.window.close();
});
test('command search filters local navigation and restores focus when closed',()=>{const dom=load(),w=dom.window,d=w.document;d.querySelector('#command-toggle').focus();d.dispatchEvent(new w.KeyboardEvent('keydown',{key:'k',ctrlKey:true,bubbles:true}));assert.equal(d.querySelector('#workspace-commands').open,true);const search=d.querySelector('#command-search');search.value='analysis';search.dispatchEvent(new w.Event('input'));assert.equal(d.querySelectorAll('#command-results a:not([hidden])').length,1);assert.equal(d.querySelector('#command-results a:not([hidden])').textContent,'Analysis');d.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));assert.equal(d.querySelector('#workspace-commands').open,false);assert.equal(d.activeElement.id,'command-toggle');dom.window.close()});
test('motion respects reduced preference and persists explicit pause without network activity',()=>{const reduced=load({reduced:true});assert.equal(reduced.window.document.body.classList.contains('motion-paused'),true);assert.equal(reduced.window.document.querySelector('#motion-toggle').disabled,true);reduced.window.close();const dom=load(),w=dom.window,d=w.document;assert.equal(d.querySelector('#motion-toggle').getAttribute('aria-pressed'),'true');d.querySelector('#motion-toggle').click();assert.equal(d.body.classList.contains('motion-paused'),true);assert.equal(w.localStorage.getItem('leakscope.motion'),'false');dom.window.close()});
test('compact navigation exposes previously collapsed groups and restores them on expand',async()=>{const dom=load(),d=dom.window.document;const tools=d.querySelector('[data-nav-group=tools]');assert.equal(tools.open,false);d.querySelector('#nav-compact-toggle').click();assert.equal(tools.open,true);d.querySelector('#nav-compact-toggle').click();assert.equal(tools.open,false);dom.window.close()});
test('ArrowUp from command search starts at the last visible result',()=>{const dom=load(),w=dom.window,d=w.document;d.querySelector('#command-toggle').click();d.querySelector('#command-search').focus();d.dispatchEvent(new w.KeyboardEvent('keydown',{key:'ArrowUp',bubbles:true}));assert.equal(d.activeElement.textContent,'Settings');dom.window.close()});
test('status menu closes on Escape and outside click and restores focus',()=>{
  const dom=load(),w=dom.window,d=w.document,menu=d.querySelector('#status-menu');
  menu.open=true;
  d.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
  assert.equal(menu.open,false);assert.equal(d.activeElement,menu.querySelector('summary'));
  menu.open=true;d.body.click();assert.equal(menu.open,false);
  menu.open=true;menu.querySelector('#status-inner').click();assert.equal(menu.open,true);
  dom.window.close();
});
test('breadcrumb names the group that holds the current page',()=>{
  let dom=load({page:'assets'});assert.equal(dom.window.document.querySelector('#breadcrumb-group').textContent,'Evidence');dom.window.close();
  dom=load({page:'explore'});assert.equal(dom.window.document.querySelector('#breadcrumb-group').textContent,'Tools');dom.window.close();
  dom=load({page:'settings'});assert.equal(dom.window.document.querySelector('#breadcrumb-group').textContent,'Workspace');dom.window.close();
});
test('a group opened for the current page is not remembered as the person\'s choice',async()=>{
  let dom=load({page:'explore'}),d=dom.window.document;
  await new Promise(r=>setTimeout(r,30));
  assert.equal(d.querySelector('[data-nav-group=tools]').open,true);
  assert.equal(dom.window.localStorage.getItem('leakscope.nav.group.tools'),null);
  dom.window.close();
  dom=load({page:'assets'});d=dom.window.document;
  const tools=d.querySelector('[data-nav-group=tools]');
  tools.querySelector('summary').click();await new Promise(r=>setTimeout(r,30));
  assert.equal(tools.open,true);assert.equal(dom.window.localStorage.getItem('leakscope.nav.group.tools'),'true');
  dom.window.close();
});
