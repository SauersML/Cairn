// Exercise the scope builder shipped in Cairn's generated page.
// Run: node tests/test_site.js cairn.py
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {test} = require('node:test');
const source = fs.readFileSync(process.argv[2], 'utf8');
const start = source.indexOf('const refutes={};');
const end = source.indexOf('window.focusProof=id=>', start);
assert(start >= 0 && end > start);
const scopeSource = source.slice(start, end);
function fixture({anchor='goal-a', mode='focus', route=null, dead=true}={}) {
  const claims = [
    {id:'goal-a', goal:true, refuters:['refuter'], refuted_by:['refuter']},
    {id:'goal-b', goal:true}, {id:'refuter', status:'ESTABLISHED', kills:['failed']},
    {id:'premise'}, {id:'other-premise'}, {id:'unrelated'},
    ...Array.from({length:8000}, (_, i)=>({id:'noise-'+i})),
  ];
  const routes = {
    first:{target:'goal-a', requires:['premise'], dead:false},
    second:{target:'goal-a', requires:['other-premise'], dead:false},
    cycle:{target:'premise', requires:['goal-a'], dead:false},
    failed:{target:'goal-b', requires:['other-premise'], dead:true, killers:['refuter']},
    unrelated:{target:'unrelated', requires:[], dead:false},
  };
  const into={};
  for (const [id,r] of Object.entries(routes)) (into[r.target] ||= []).push([id,r]);
  const context=vm.createContext({DATA:{claims,routes}, byId:Object.fromEntries(claims.map(c=>[c.id,c])),
    focusInto:into, focusAnchor:anchor, focusRoute:route,
    viewMode:{value:mode}, focusDepth:{value:3}, routeCap:{value:1}});
  vm.runInContext(scopeSource,context);
  return vm.runInContext(`currentScope(${dead})`,context);
}
test('focus excludes unrelated corpus and respects the route limit',()=>{
  const s=fixture();
  assert(s.claims.has('goal-a')); assert(s.claims.has('premise'));
  assert(!s.claims.has('other-premise')); assert(!s.claims.has('unrelated'));
  assert(s.claims.size < 10);
});
test('focusing a refuter keeps the claim it refutes and the routes it invalidates',()=>{
  const s=fixture({anchor:'refuter'});
  for(const id of ['refuter','goal-a','goal-b','other-premise']) assert(s.claims.has(id),id);
  assert(s.routes.has('failed'));
});
test('a refuted claim retains its established refuter with failed routes hidden',()=>{
  const s=fixture({dead:false});
  assert(s.claims.has('refuter')); assert(!s.routes.has('failed'));
});
test('whole goal follows only the selected goal and terminates on cycles',()=>{
  const s=fixture({mode:'goal'});
  assert(s.routes.has('first')); assert(s.routes.has('second')); assert(s.routes.has('cycle'));
  assert(!s.claims.has('goal-b')); assert(!s.claims.has('unrelated'));
});
test('an explicitly selected route survives a competing route cap',()=>{
  const s=fixture({route:'second'});
  assert(s.routes.has('second')); assert(s.claims.has('other-premise'));
});
test('everything includes all claims while respecting failed-route visibility',()=>{
  const s=fixture({mode:'all',dead:false});
  assert.equal(s.claims.size,8006); assert(!s.routes.has('failed')); assert(s.routes.has('unrelated'));
});
