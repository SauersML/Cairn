from pathlib import Path

p = Path('cairn.py')
s = p.read_text()


def rep(old, new, n=1):
    global s
    got = s.count(old)
    if got != n:
        raise SystemExit(f'expected {n} occurrence(s), found {got}: {old[:120]!r}')
    s = s.replace(old, new, n)


rep('.lk{stroke:var(--edge);stroke-width:1.5}\n', '''.lk{stroke:var(--edge);stroke-width:1.5}\n.lk.in{stroke-width:1.25;opacity:.72}\n.lk.out{stroke:var(--ink);stroke-width:1.9;opacity:.88}\n.foldbox{display:none}\n.compact g.n.foldrep .foldbox{display:block}\n.compact g.n.foldrep circle{display:none}\n.compact g.labels text.foldrep{display:none}\n.foldbox rect{fill:var(--paper);stroke:var(--est);stroke-width:1.5;rx:3}\n.foldbox text{stroke:none;fill:var(--est);font:700 10px __MONO__;\nletter-spacing:.04em;text-anchor:middle}\n.foldbox text.foldsub{fill:var(--mut2);font-size:8.5px;font-weight:400;\nletter-spacing:.02em}\n.compact g.n.foldrep.hot .foldbox rect{stroke-width:2.4}\nbutton.foldopen{border:1px solid var(--rule);background:var(--paper);color:var(--ink);\nfont:11px __MONO__;padding:.5em .7em;cursor:pointer;margin:.7em 0 .2em}\nbutton.foldopen:hover{border-color:var(--est);color:var(--est)}\n''')

rep('<label><input type="checkbox" id="showdead" checked> failed routes</label>\n',
    '<label><input type="checkbox" id="showdead" checked> failed routes</label>\n'
    '<label title="Fold established interior proof regions; goals and open-work boundaries stay visible"><input type="checkbox" id="compact" checked> compact proven</label>\n')

rep('multi-premise route (&and;)</span>\n<span><svg width="27" height="16"><line x1="1" y1="8" x2="20" y2="8" stroke="#17171459" stroke-width="1.5"/><path d="M19,4.5L25,8L19,11.5z" fill="#17171459"/></svg>premises &#10230; target</span>',
    'all-premises gate (&and;)</span>\n<span><svg width="47" height="16"><line x1="1" y1="4" x2="17" y2="8" stroke="#17171499" stroke-width="1.2"/><path d="M13,5.2L18,8L12.5,9.2" fill="none" stroke="#17171499" stroke-width="1.2"/><rect x="20" y="2" width="12" height="12" fill="#fff" stroke="#171714" stroke-width="1.1"/><path d="M23,10.5L26,5.5L29,10.5" fill="none" stroke="#171714" stroke-width="1.4"/><line x1="32" y1="8" x2="42" y2="8" stroke="#171714" stroke-width="1.8"/><path d="M40,5L46,8L40,11z" fill="#171714"/></svg>premises &#8594; &and; &#8658; target</span>')

rep(" links.push({source:jn.id,target:j.target,kind:'arrow',\n",
    " links.push({source:jn.id,target:j.target,kind:'out',\n")

fold_code = r'''// Compact view is presentation only: fold proved INTERIORS, never the boundary
// of unfinished work. Established claims touching a live open route, goals,
// roots, frontier claims, and claims that invalidate routes remain explicit.
const FOLD_MIN=6;
(function markProvenRegions(){
 const touch={};
 for(const r of Object.values(DATA.routes||{})){
  if(r.dead)continue;
  const ms=[r.target,...(r.requires||[])].filter(id=>byId[id]&&byId[id].type==='claim');
  for(const id of ms)(touch[id]=touch[id]||[]).push(r);
 }
 const eligible=new Set(DATA.claims.filter(c=>
  c.status==='ESTABLISHED'&&!c.goal&&!c.root&&!c.frontier&&!(c.kills||[]).length&&
  (touch[c.id]||[]).every(r=>r.status==='COMPLETE')).map(c=>c.id));
 const adj={};for(const id of eligible)adj[id]=new Set();
 for(const r of Object.values(DATA.routes||{})){
  if(r.dead||r.status!=='COMPLETE')continue;
  const ms=[r.target,...(r.requires||[])].filter(id=>eligible.has(id));
  for(let i=1;i<ms.length;i++){adj[ms[0]].add(ms[i]);adj[ms[i]].add(ms[0])}
 }
 const seen=new Set();
 for(const start of eligible){
  if(seen.has(start))continue;
  const comp=[],todo=[start];seen.add(start);
  while(todo.length){
   const id=todo.pop();comp.push(id);
   for(const q of adj[id])if(!seen.has(q)){seen.add(q);todo.push(q)}
  }
  if(comp.length<FOLD_MIN)continue;
  comp.sort((a,b)=>{
   const A=byId[a],B=byId[b],ad=A.depth==null?1e9:A.depth,bd=B.depth==null?1e9:B.depth;
   return ad-bd||(B.impact||0)-(A.impact||0)||a.localeCompare(b);
  });
  const representative=comp[0];
  for(const id of comp)byId[id].foldRep=representative;
  byId[representative].foldMembers=comp.slice();
 }
 // A conjunction is interior only when every endpoint belongs to one fold.
 for(const n of nodes)if(n.type==='junction'){
  const ids=[n.tgt,...(n.requires||[])],reps=ids.map(id=>byId[id]&&byId[id].foldRep);
  if(reps.length&&reps[0]&&reps.every(x=>x===reps[0]))n.foldRep=reps[0];
 }
})();
'''
rep("for(const a of DATA.affinity)links.push({source:a.a,target:a.b,kind:'aff',w:a.w});\n",
    fold_code + "for(const a of DATA.affinity)links.push({source:a.a,target:a.b,kind:'aff',w:a.w});\n")

rep("const REAL=links.filter(real);\n",
    "const REAL=links.filter(real);\n"
    "for(const l of links){l.sid=l.source;l.tid=l.target}\n"
    "const compactBox=document.getElementById('compact');\n"
    "try{const v=localStorage.getItem('cairnCompactProven');if(v!==null)compactBox.checked=v==='1'}catch(_){}\n"
    "let compactMode=compactBox.checked;\n"
    "const mappedId=(id,l)=>compactMode&&real(l)&&byId[id]&&byId[id].foldRep?byId[id].foldRep:id;\n"
    "function remapLinks(){\n"
    " for(const l of links){l.source=byId[mappedId(l.sid,l)];l.target=byId[mappedId(l.tid,l)]}\n"
    "}\n"
    "remapLinks();\n")

rep("svg.append('defs').html('<marker id=\"m\" viewBox=\"0 0 8 8\" refX=\"7.5\" refY=\"4\" markerWidth=\"7.5\" markerHeight=\"7.5\" orient=\"auto\"><path d=\"M0,0L8,4L0,8z\" fill=\"#17171459\"/></marker><marker id=\"mr\" viewBox=\"0 0 8 8\" refX=\"7.5\" refY=\"4\" markerWidth=\"7.5\" markerHeight=\"7.5\" orient=\"auto\"><path d=\"M0,0L8,4L0,8z\" fill=\"#c43c2e\"/></marker>');",
    "svg.append('defs').html('<marker id=\"m\" viewBox=\"0 0 8 8\" refX=\"7.5\" refY=\"4\" markerWidth=\"7.5\" markerHeight=\"7.5\" orient=\"auto\"><path d=\"M0,0L8,4L0,8z\" fill=\"#17171459\"/></marker><marker id=\"mi\" viewBox=\"0 0 9 8\" refX=\"8\" refY=\"4\" markerWidth=\"7\" markerHeight=\"7\" orient=\"auto\"><path d=\"M1,1L8,4L1,7\" fill=\"none\" stroke=\"#17171499\" stroke-width=\"1.4\"/></marker><marker id=\"mo\" viewBox=\"0 0 8 8\" refX=\"7.5\" refY=\"4\" markerWidth=\"8.5\" markerHeight=\"8.5\" orient=\"auto\"><path d=\"M0,0L8,4L0,8z\" fill=\"#171714\"/></marker><marker id=\"mr\" viewBox=\"0 0 8 8\" refX=\"7.5\" refY=\"4\" markerWidth=\"7.5\" markerHeight=\"7.5\" orient=\"auto\"><path d=\"M0,0L8,4L0,8z\" fill=\"#c43c2e\"/></marker>');")

rep(" const r=[[-rad,-rad,rad,rad]];\n",
    " const r=[[-rad,-rad,rad,rad]];\n"
    " if(compactMode&&d.foldMembers&&d.foldMembers.length)r.push([-48,-20,48,20]);\n")
rep(" if(L){\n  const hw=L.w/2+3,t=L.top+L.dy-3;\n",
    " if(L&&!(compactMode&&d.foldMembers&&d.foldMembers.length)){\n  const hw=L.w/2+3,t=L.top+L.dy-3;\n")
rep("  for(const d of ns){\n   q.visit((quad,x0,y0,x1,y1)=>{\n",
    "  for(const d of ns){\n   if(d.gone)continue;\n   q.visit((quad,x0,y0,x1,y1)=>{\n")
rep("      if(o&&o!==d&&o.index>d.index){\n",
    "      if(o&&!o.gone&&o!==d&&o.index>d.index){\n")

rep(" .attr('class',l=>'lk'+(l.kind==='kill'?' kill':'')+(l.dead?' dead':''))\n .attr('marker-end',l=>l.kind==='in'?null:(l.dead||l.kind==='kill'?'url(#mr)':'url(#m)'))\n",
    " .attr('class',l=>'lk'+(l.kind==='in'?' in':'')+(l.kind==='out'?' out':'')+(l.kind==='kill'?' kill':'')+(l.dead?' dead':''))\n .attr('marker-end',l=>l.dead||l.kind==='kill'?'url(#mr)':(l.kind==='in'?'url(#mi)':(l.kind==='out'?'url(#mo)':'url(#m)')))\n")

rep("node.filter(d=>d.type==='claim').append('circle')\n .attr('r',d=>d.goal?15:10+Math.min(d.impact*1.5,4))\n .attr('fill',d=>d.status==='ESTABLISHED'?'var(--est)':'#fff')\n .attr('stroke',d=>d.status==='ESTABLISHED'?'#0f6b47':'var(--open)')\n .attr('stroke-width',2.2);\n",
    "node.filter(d=>d.type==='claim').append('circle')\n .attr('r',d=>d.goal?15:10+Math.min(d.impact*1.5,4))\n .attr('fill',d=>d.status==='ESTABLISHED'?'var(--est)':'#fff')\n .attr('stroke',d=>d.status==='ESTABLISHED'?'#0f6b47':'var(--open)')\n .attr('stroke-width',2.2);\n"
    "const foldn=node.filter(d=>d.type==='claim'&&d.foldMembers&&d.foldMembers.length);\n"
    "const foldbox=foldn.append('g').attr('class','foldbox');\n"
    "foldbox.append('rect').attr('x',-48).attr('y',-20).attr('width',96).attr('height',40);\n"
    "foldbox.append('text').attr('y',-2).text(d=>`${d.foldMembers.length} proven`);\n"
    "foldbox.append('text').attr('class','foldsub').attr('y',11).text('collapsed proof region');\n")

rep(" .attr('text-anchor','middle')\n .style('cursor','pointer')\n",
    " .attr('text-anchor','middle')\n .classed('foldrep',d=>!!(d.foldMembers&&d.foldMembers.length))\n .style('cursor','pointer')\n")

rep("  const r=(n.type==='claim'?(n.goal?23:12):9)+2;\n",
    "  const r=(compactMode&&n.foldMembers&&n.foldMembers.length)?50:(n.type==='claim'?(n.goal?23:12):9)+2;\n")
rep("  if(d.gone||!isFinite(d.x))continue;\n  const w=o.w+LPAD*2,h=o.h+LPAD*2;\n",
    "  if(d.gone||!isFinite(d.x)||(compactMode&&d.foldMembers&&d.foldMembers.length))continue;\n  const w=o.w+LPAD*2,h=o.h+LPAD*2;\n")

rep("node.append('title').text(d=>d.type==='claim'?`${d.id} [${d.status}]`:(d.rtitle||d.route));\n",
    "node.append('title').text(d=>d.foldMembers&&d.foldMembers.length?`${d.foldMembers.length} established claims — click to inspect`:(d.type==='claim'?`${d.id} [${d.status}]`:(d.rtitle||d.route)));\n")

rep("   const hub=byId['j:'+rid]||byId['x:'+rid];\n   if(hub){selected=hub;highlight(hub)}\n",
    "   const hub=byId['j:'+rid]||byId['x:'+rid];\n   if(hub){if(compactMode&&hub.foldRep&&hub.id!==hub.foldRep)setCompact(false);selected=hub;highlight(hub)}\n")

showfold = r'''function showFold(d){
 const members=(d.foldMembers||[]).map(id=>claimById[id]).filter(Boolean)
  .sort((a,b)=>(a.depth==null?1e9:a.depth)-(b.depth==null?1e9:b.depth)||a.title.localeCompare(b.title));
 let h=`<span class="chip ESTABLISHED">PROVEN REGION</span>
  <h2>${members.length} established claims</h2>
  <p class="hint">Solved interior detail is folded here. Claims touching open routes, goals, roots, or invalidations stay outside the block.</p>
  <button class="foldopen" id="expandfold">expand proven regions</button>
  <h3 class="sec">Inside<span class="ct">${members.length}</span></h3><ul class="fr ctx">`;
 for(const c of members.slice(0,40))h+=`<li>${clink(c.id)}</li>`;
 if(members.length>40)h+=`<li class="hint">…and ${members.length-40} more</li>`;
 h+='</ul>';
 pbody.innerHTML=h;afterPanel();
 const b=document.getElementById('expandfold');
 if(b)b.onclick=()=>{setCompact(false);selectById(d.id)};
}
'''
rep("function show(d){\n if(d.type==='claim'){\n",
    showfold + "function show(d){\n if(d.type==='claim'){\n  if(compactMode&&d.foldMembers&&d.foldMembers.length){showFold(d);return}\n")

rep("selectById=id=>{const d=byId[id];if(d){selected=d;highlight(d);show(d);pbody.scrollTop=0}};\n",
    "selectById=id=>{const d=byId[id];if(d){if(compactMode&&d.foldRep&&d.id!==d.foldRep)setCompact(false);selected=d;highlight(d);show(d);pbody.scrollTop=0}};\n")

old_refresh = r'''function refreshVis(){
 const sd=document.getElementById('showdead').checked;
 const deg={};
 links.forEach(l=>{if(real(l)&&(!l.dead||sd)){
  const a=l.source.id||l.source,b=l.target.id||l.target;
  deg[a]=(deg[a]||0)+1;deg[b]=(deg[b]||0)+1}});
 nodes.forEach(d=>{
  d.orphan=d.type==='claim'&&!d.root&&!d.goal&&!d.frontier&&!(deg[d.id]>0);
  d.gone=d.orphan;
 });
 node.classed('orphan',d=>d.gone);
 lab.classed('orphan',d=>d.gone);
 line.classed('gone',l=>{
  const a=byId[l.source.id||l.source],b=byId[l.target.id||l.target];
  return (a&&a.gone)||(b&&b.gone);
 });
 g.classed('showdead',sd);
 sim.force('charge',d3.forceManyBody().strength(d=>d.gone?-2:-430));
 linkForce.strength(l=>l.kind==='aff'
  ?((l.source.gone||l.target.gone)?0:.03+.1*l.w):.5);
 sim.alpha(.5).restart();
 relabel();
}
document.getElementById('showdead').onchange=refreshVis;
'''
new_refresh = r'''function refreshVis(){
 const sd=document.getElementById('showdead').checked;
 const deg={};
 links.forEach(l=>{if(real(l)&&(!l.dead||sd)){
  const a=l.source.id||l.source,b=l.target.id||l.target;
  if(a!==b){deg[a]=(deg[a]||0)+1;deg[b]=(deg[b]||0)+1}
 }});
 nodes.forEach(d=>{
  const folded=!!(compactMode&&d.foldRep&&d.id!==d.foldRep);
  const representative=!!(compactMode&&d.foldMembers&&d.foldMembers.length);
  d.orphan=!folded&&!representative&&d.type==='claim'&&!d.root&&!d.goal&&!d.frontier&&!(deg[d.id]>0);
  d.gone=folded||d.orphan;
 });
 node.classed('orphan',d=>d.gone).classed('foldrep',d=>!!(d.foldMembers&&d.foldMembers.length));
 lab.classed('orphan',d=>d.gone);
 line.classed('gone',l=>{
  const ai=l.source.id||l.source,bi=l.target.id||l.target,a=byId[ai],b=byId[bi];
  return ai===bi||(a&&a.gone)||(b&&b.gone);
 });
 g.classed('showdead',sd).classed('compact',compactMode);
 sim.force('charge',d3.forceManyBody().strength(d=>d.gone?-2:-430));
 linkForce.strength(l=>{
  if(l.source===l.target||l.source.gone||l.target.gone)return 0;
  return l.kind==='aff'?.03+.1*l.w:.5;
 });
 sim.alpha(.5).restart();
 relabel();
}
function setCompact(on){
 compactMode=!!on;compactBox.checked=compactMode;
 try{localStorage.setItem('cairnCompactProven',compactMode?'1':'0')}catch(_){}
 remapLinks();linkForce.links(links);
 for(const n of nodes)setRects(n);
 sim.force('collide',rectCollide());
 if(compactMode&&selected&&selected.foldRep&&selected.id!==selected.foldRep)
  selected=byId[selected.foldRep];
 refreshVis();
 if(selected){highlight(selected);show(selected)}
}
document.getElementById('showdead').onchange=refreshVis;
compactBox.onchange=()=>setCompact(compactBox.checked);
'''
rep(old_refresh, new_refresh)

edge_code = r'''function edgeRadius(n){
 if(compactMode&&n.foldMembers&&n.foldMembers.length)return 49;
 if(n.type==='claim')return n.goal?23:10+Math.min((n.impact||0)*1.5,4);
 return n.type==='junction'?11:8;
}
function edgeEnds(l){
 const a=l.source,b=l.target,dx=b.x-a.x,dy=b.y-a.y,L=Math.hypot(dx,dy)||1;
 if(a===b)return [a.x,a.y,b.x,b.y];
 const ra=edgeRadius(a),rb=edgeRadius(b),ux=dx/L,uy=dy/L;
 return [a.x+ux*ra,a.y+uy*ra,b.x-ux*rb,b.y-uy*rb];
}
'''
rep("sim.on('tick',()=>{\n line.attr('x1',l=>l.source.x).attr('y1',l=>l.source.y)\n     .attr('x2',l=>l.target.x).attr('y2',l=>l.target.y);\n",
    edge_code + "sim.on('tick',()=>{\n line.each(function(l){const e=edgeEnds(l);this.setAttribute('x1',e[0]);this.setAttribute('y1',e[1]);this.setAttribute('x2',e[2]);this.setAttribute('y2',e[3])});\n")

p.write_text(s)
