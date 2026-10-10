'use strict';
// Test the real page handlers with a minimal DOM; no network or script execution.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {webcrypto} = require('node:crypto');
class Element {
  constructor() { this.children = []; this.handlers = {}; this.value = ''; this.textContent = ''; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  addEventListener(event, handler) { this.handlers[event] = handler; }
  setAttribute(key,value) { this[key] = value; }
  click() { this.clicked = true; }
}
const ids = Object.fromEntries(['search','plugins','count','catalog-status','submit-form','prepared','form-status','file','source','manifest','preview','github-submit','download-manifest'].map(id => [id,new Element()]));
let copied;
const context = vm.createContext({document:{getElementById:id=>ids[id],createElement:()=>new Element()},
  fetch:async()=>({ok:true,json:async()=>[{id:'coords',version:'1.0.0',name:'Coordinates',description:'Read position',owner:'alice',permissions:['player'],dependencies:[],source:'https://github.com/alice/repo/releases/download/v1/coords.umc'}]}),
  navigator:{clipboard:{writeText:async value=>{copied=value;}}},crypto:webcrypto,URL,URLSearchParams,TextDecoder,Blob,setTimeout});
vm.runInContext(fs.readFileSync('site/app.js','utf8'),context);
(async()=>{
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(ids.plugins.children.length,1);
  ids.search.value='missing';ids.search.handlers.input();assert.equal(ids.plugins.children.length,0);
  ids.search.value='alice';ids.search.handlers.input();assert.equal(ids.plugins.children.length,1);
  const card=ids.plugins.children[0],install=card.children.find(e=>e.className==='install');
  await install.children[1].handlers.click(); assert.equal(copied,'umc install coords@1.0.0');
  const source='// test\nplugin "example"; version "2.0.0"; state text = "version";';
  const bytes=new TextEncoder().encode(source);
  ids.file.files=[{name:'example.umc',size:bytes.length,arrayBuffer:async()=>bytes.buffer}];
  ids.source.value='https://raw.githubusercontent.com/alice/repo/main/example.umc';
  await ids['submit-form'].handlers.submit({preventDefault(){}});
  assert.equal(ids.prepared.hidden,false);
  const data=JSON.parse(ids.manifest.value);
  assert.equal(data.id,'example');assert.equal(data.version,'2.0.0');assert.equal(data.sha256.length,64);
  const link=new URL(ids['github-submit'].href);
  assert.equal(link.searchParams.get('filename'),'plugins/example/2.0.0.json');
  assert.deepEqual(JSON.parse(link.searchParams.get('value')),data);
  ids.source.value='https://evil.example/example.umc';
  await ids['submit-form'].handlers.submit({preventDefault(){}});assert.equal(ids.prepared.hidden,true);assert.equal(ids['form-status'].className,'error');
  console.log('all registry page tests passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
