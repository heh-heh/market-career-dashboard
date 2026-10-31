/* Offline DOM fixture: neither credentials nor network requests are made. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../admin-login.js'),'utf8');
const HOST='https://heh-heh.tail39e546.ts.net:8444/';

function render(url){
  let replacement=null;
  const handlers={};
  const form={addEventListener:(name,callback)=>{handlers[name]=callback;},
    replaceWith:(el)=>{replacement=el;}};
  const button={disabled:false};
  const status={textContent:''};
  const nodes={login:form,submit:button,loginStatus:status};
  const document={
    getElementById:(id)=>nodes[id]||null,
    createElement:(tag)=>({tag,children:[],append(...children){this.children.push(...children);}})
  };
  vm.runInNewContext(source,{window:{location:{href:url}},document,URL});
  return {replacement,handlers,status,button};
}
function findAnchor(node){
  return node?.children?.find(x=>x.tag==='a');
}
for(const url of [
  'https://heh-heh.tail39e546.ts.net/',
  'https://heh-heh.tail39e546.ts.net/admin.html',
  'https://heh-heh.tail39e546.ts.net:10000/',
  'http://127.0.0.1:18086/',
  'http://localhost:18086/'
]){
  const {replacement,handlers,status}=render(url);
  assert(replacement,'home page must provide a private Tailnet login link: '+url);
  assert.equal(findAnchor(replacement).href,HOST);
  assert(!handlers.submit,'do not post public-site credentials to private admin: '+url);
  assert(status.textContent.includes('Tailscale'),'display private admin requirement');
}
for(const url of [
  'https://heh-heh.github.io/market-career-dashboard/',
  'https://heh-heh.github.io/market-career-dashboard/admin.html'
]){
  const {replacement,handlers}=render(url);
  assert.equal(replacement,null,'legacy Pages page should keep its existing login');
  assert.equal(typeof handlers.submit,'function');
}
const ng=fs.readFileSync(path.join(__dirname,'../deploy/public-web/nginx.conf'),'utf8');
assert(ng.includes('location = /admin.html'));
assert(ng.includes('return 302 '+HOST.slice(0,-1)+'/;'));
assert(ng.includes('location /api/ { return 404; }'));
console.log('HOME_ADMIN_ENTRY_OFFLINE_TEST=PASS');
