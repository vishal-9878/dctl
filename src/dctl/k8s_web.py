"""Frontend (CSS + JS) of the Kubernetes page. Injected into dctl.ui.HTML."""

K8S_CSS = r"""
#k8v{display:flex;flex-direction:column;min-height:0}
#k8v.view{overflow:hidden}
.k8home{padding:34px clamp(16px,4vw,48px) 60px;overflow:auto;flex:1}
.kcard{min-height:170px;gap:10px}
.kcard .sc-name{font-size:15px}
.kcard .ksub{font:11.5px var(--mono);color:var(--dim);word-break:break-all;line-height:1.4}
.kcard .kst{font-size:12.5px;color:var(--mut);display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:auto}
.kcard .kst b{color:var(--fg)}
.kcard .kerr{color:#ff8aa0;font-size:12px;word-break:break-word;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.kbadge{font:600 10px var(--sans);letter-spacing:.5px;text-transform:uppercase;padding:3px 7px;border-radius:6px;background:rgba(255,255,255,.06);color:var(--mut)}
.kbadge.cur{background:rgba(34,227,161,.12);color:var(--ok)}.kbadge.ssh{background:rgba(95,168,255,.12);color:#8ec5ff}
.k8wrap{flex:1;display:flex;min-height:0}
.k8nav{width:228px;flex:none;border-right:1px solid var(--line);overflow:auto;padding:14px 10px 30px;background:rgba(8,10,18,.35)}
.k8nav .kcl{display:flex;gap:10px;align-items:center;padding:6px 8px 14px;border-bottom:1px solid var(--line);margin-bottom:8px}
.k8nav .kcl b{font-size:13.5px;word-break:break-all;line-height:1.25}
.k8nav h5{margin:12px 8px 4px;font:600 10.5px var(--sans);letter-spacing:.8px;text-transform:uppercase;color:var(--dim)}
.k8nav a{display:flex;align-items:center;gap:9px;padding:6px 10px;border-radius:8px;cursor:pointer;color:var(--mut);font-weight:500;font-size:13px}
.k8nav a:hover{color:var(--fg);background:rgba(255,255,255,.04)}
.k8nav a.on{color:var(--fg);background:rgba(124,92,255,.16);box-shadow:inset 2px 0 0 #9d8bff}
.k8main{flex:1;display:flex;flex-direction:column;min-width:0}
.k8head{display:flex;align-items:center;gap:10px;padding:16px clamp(14px,2vw,24px) 10px;flex-wrap:wrap}
.k8head h2{margin:0;font-size:21px;font-weight:800;letter-spacing:-.4px;display:flex;align-items:center;gap:10px}
.k8head h2 span{font:500 12px var(--mono);color:var(--mut);padding:2px 8px;border-radius:7px;background:rgba(255,255,255,.06)}
.k8head .search{min-width:200px;max-width:360px}
.k8head select{max-width:220px}
.k8body{flex:1;overflow:auto;padding:0 clamp(14px,2vw,24px) 60px}
table.kt{width:100%;border-collapse:separate;border-spacing:0;font-size:12.8px;background:var(--glass);border:1px solid var(--line);border-radius:var(--r);overflow:hidden}
.kt th{position:sticky;top:0;z-index:1;background:rgba(18,20,32,.98);text-align:left;font-weight:600;color:var(--mut);font-size:11.5px;letter-spacing:.3px;padding:9px 10px;border-bottom:1px solid var(--line2);white-space:nowrap;cursor:pointer;user-select:none}
.kt th:hover{color:var(--fg)}.kt th.srt::after{content:" ↓";color:#9d8bff}.kt th.srt.up::after{content:" ↑"}
.kt td{padding:7px 10px;border-bottom:1px solid var(--line);vertical-align:middle;max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.kt tr:last-child td{border-bottom:0}
.kt tbody tr{cursor:pointer;transition:background .1s}
.kt tbody tr:hover{background:rgba(124,92,255,.07)}
.kt tbody tr.on{background:rgba(124,92,255,.15)}
.kt tbody tr.pk{background:rgba(157,139,255,.07)}
.kt td.kn{font-weight:600;color:var(--fg)}
.kt td.kn i{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:8px;background:var(--dim);vertical-align:1px}
.kt td.kn i.ok{background:var(--ok);box-shadow:0 0 8px rgba(34,227,161,.6)}.kt td.kn i.warn{background:var(--warn)}.kt td.kn i.bad{background:var(--bad);box-shadow:0 0 8px rgba(255,77,109,.6)}
.kt td.msg{white-space:normal;max-width:520px;min-width:240px;line-height:1.4}
.kt .w0{width:28px;padding-right:0}
.kt td.ka{text-align:right;white-space:nowrap;padding:4px 8px}
.kt td.ka .icon-btn{opacity:0;transition:opacity .1s}.kt tr:hover td.ka .icon-btn{opacity:1}
.kc-ok{color:var(--ok)}.kc-warn{color:var(--warn)}.kc-bad{color:#ff7a93}.kc-dim{color:var(--dim)}
.kbar{display:inline-flex;align-items:center;gap:8px}.kbar i{display:inline-block;width:46px;height:5px;border-radius:4px;background:rgba(255,255,255,.08);overflow:hidden}
.kbar i b{display:block;height:100%;border-radius:4px}
#k8d{position:fixed;top:0;right:0;bottom:0;width:min(900px,100vw);z-index:20;background:rgba(12,14,22,.94);backdrop-filter:blur(22px);border-left:1px solid var(--line2);
  box-shadow:-30px 0 80px -20px rgba(0,0,0,.8);display:flex;flex-direction:column;animation:slide .28s cubic-bezier(.2,.8,.2,1)}
#k8d .dr-head{padding:20px 22px 8px}
#k8d .kdbody{flex:1;overflow:auto;padding:12px 22px 30px;min-height:0;display:flex;flex-direction:column}
#k8d pre.kpre{margin:0;font:12px/1.55 var(--mono);white-space:pre-wrap;word-break:break-word;color:#cfd3e4;background:rgba(5,6,10,.6);border:1px solid var(--line);border-radius:12px;padding:14px;flex:1;overflow:auto}
#k8d pre.kpre .k{color:#9d8bff}
#k8d .kyaml{flex:1;min-height:300px;border:1px solid var(--line);border-radius:12px;overflow:hidden}
#k8d .kyaml .CodeMirror{height:100%;font:12.5px/1.5 var(--mono)}
.klogbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px}
.klogbar .search{min-width:160px;flex:1}
.kdata{display:flex;flex-direction:column;gap:10px}
.kdata .ksv{border:1px solid var(--line);border-radius:12px;background:rgba(5,6,10,.5);overflow:hidden}
.kdata .ksv .kh{display:flex;gap:8px;align-items:center;padding:8px 12px;border-bottom:1px solid var(--line);font:600 12px var(--mono)}
.kdata .ksv pre{margin:0;padding:10px 12px;font:12px/1.5 var(--mono);white-space:pre-wrap;word-break:break-all;max-height:300px;overflow:auto;color:#cfd3e4}
.kov{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:14px;margin-bottom:16px}
.kov .kbox{background:var(--glass);border:1px solid var(--line);border-radius:var(--r);padding:16px}
.kov .kbox h4{margin:0 0 10px;font-size:12px;color:var(--mut);font-weight:600;letter-spacing:.4px;text-transform:uppercase;display:flex;gap:8px;align-items:center}
.kov .big{font-size:30px;font-weight:800;letter-spacing:-1px}
.kov .big small{font-size:14px;color:var(--mut);font-weight:500;letter-spacing:0}
.kres{display:flex;flex-direction:column;gap:9px}
.kres .rl{display:flex;justify-content:space-between;font-size:12px;color:var(--mut)}.kres .rl b{color:var(--fg);font-weight:600}
.kres .rb{height:7px;border-radius:5px;background:rgba(255,255,255,.07);overflow:hidden}.kres .rb i{display:block;height:100%;border-radius:5px}
.kdots{display:flex;gap:14px;flex-wrap:wrap;font-size:12.5px;color:var(--mut)}.kdots span{display:inline-flex;gap:6px;align-items:center}.kdots i{width:8px;height:8px;border-radius:50%;display:inline-block}
#k8bulk{position:fixed;left:50%;bottom:22px;transform:translateX(-50%);z-index:15;display:flex;gap:10px;align-items:center;padding:10px 14px;border-radius:14px;background:rgba(16,18,28,.97);border:1px solid var(--line2);box-shadow:var(--shadow)}
.kpf{display:flex;gap:12px;align-items:center;padding:12px 14px;border:1px solid var(--line);border-radius:12px;background:var(--glass);margin-bottom:10px}
.ktpl{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px}
.kcm{height:52vh;border:1px solid var(--line);border-radius:12px;overflow:hidden}.kcm .CodeMirror{height:100%;font:12.5px/1.5 var(--mono)}
.kcm textarea{width:100%;height:100%;background:#05060a;color:#d7dbea;border:0;font:12.5px/1.5 var(--mono);padding:10px;resize:none}
@media (max-width:820px){.k8nav{display:none}}
"""

K8S_JS = r"""
// ---------- Kubernetes (Lens-like) ----------
const K={clusters:[],info:{},cid:null,kind:'overview',ns:'',q:'',data:null,sel:null,pick:new Set(),timer:null,nsList:[],sort:null,live:true,
  showHidden:false,kubectl:true,helm:false,known:[],cfilter:'',seq:0,busy:false,cr:null,cm:null,yorig:'',logT:null};
const KNAV=[['Cluster',[['overview','Overview','globe'],['nodes','Nodes','server'],['namespaces','Namespaces','folder'],['events','Events','alert']]],
 ['Workloads',[['pods','Pods','box'],['deployments','Deployments','layers'],['daemonsets','DaemonSets','layers'],['statefulsets','StatefulSets','layers'],
   ['replicasets','ReplicaSets','layers'],['jobs','Jobs','play'],['cronjobs','CronJobs','clock']]],
 ['Config',[['configmaps','ConfigMaps','file'],['secrets','Secrets','lock'],['resourcequotas','Resource Quotas','disk'],['limitranges','Limit Ranges','disk'],
   ['hpa','HPA','up'],['pdb','Pod Disruption Budgets','shield'],['priorityclasses','Priority Classes','list']]],
 ['Network',[['services','Services','globe'],['endpoints','Endpoints','globe'],['ingresses','Ingresses','globe'],['ingressclasses','Ingress Classes','globe'],['networkpolicies','Network Policies','shield']]],
 ['Storage',[['pvc','Persistent Volume Claims','disk'],['pv','Persistent Volumes','disk'],['storageclasses','Storage Classes','disk']]],
 ['Access control',[['serviceaccounts','Service Accounts','lock'],['roles','Roles','lock'],['clusterroles','Cluster Roles','lock'],['rolebindings','Role Bindings','lock'],['clusterrolebindings','Cluster Role Bindings','lock']]],
 ['Helm',[['helm','Releases','k8s']]],['Custom resources',[['crds','Definitions','code']]],['Port forwarding',[['pf','Port forwards','globe']]]];
const KT=Object.fromEntries(KNAV.flatMap(([,a])=>a.map(([k,t,i])=>[k,{t,i}])));
const KOBJ={pods:'Pod',deployments:'Deployment',daemonsets:'DaemonSet',statefulsets:'StatefulSet',replicasets:'ReplicaSet',jobs:'Job',cronjobs:'CronJob',
  configmaps:'ConfigMap',secrets:'Secret',services:'Service',ingresses:'Ingress',nodes:'Node',namespaces:'Namespace',pvc:'PersistentVolumeClaim',pv:'PersistentVolume',
  hpa:'HorizontalPodAutoscaler',endpoints:'Endpoints',networkpolicies:'NetworkPolicy',serviceaccounts:'ServiceAccount',storageclasses:'StorageClass'};
const KIND_OF={Pod:'pods',Deployment:'deployments',DaemonSet:'daemonsets',StatefulSet:'statefulsets',ReplicaSet:'replicasets',Job:'jobs',CronJob:'cronjobs',
  ConfigMap:'configmaps',Secret:'secrets',Service:'services',Ingress:'ingresses',Node:'nodes',Namespace:'namespaces',PersistentVolumeClaim:'pvc',PersistentVolume:'pv',
  HorizontalPodAutoscaler:'hpa',Endpoints:'endpoints',NetworkPolicy:'networkpolicies',ServiceAccount:'serviceaccounts',StorageClass:'storageclasses'};
const lsg=k=>{try{return localStorage.getItem(k)}catch(_){return null}},lss=(k,v)=>{try{localStorage.setItem(k,v)}catch(_){}};
const kShort=n=>n.startsWith('arn:')?n.split('/').pop():n.startsWith('gke_')?n.split('_').pop():n;
const kCl=()=>K.clusters.find(c=>c.id===K.cid)||{name:'',id:''};
function kAgo(t){if(!t)return'';let s=Math.max(0,Date.now()/1000-t);return kDur(s)}
function kDur(s){s=Math.round(s);if(s<60)return s+'s';const m=Math.floor(s/60);if(m<60)return m+'m';const h=Math.floor(m/60);if(h<48)return h+'h'+(h<10&&m%60?` ${m%60}m`:'');return Math.floor(h/24)+'d'}
function kCell(c){if(c==null||c==='')return'';if(typeof c!=='object')return esc(c);
  if(c.t!==undefined)return c.t?kAgo(c.t):'';if(c.dur!==undefined)return kDur(c.dur);
  if(c.bar!==undefined){const p=c.bar;return p==null?esc(c.v):`<span class="kbar"><i><b style="width:${clamp(p)}%;background:${pgrad(p)}"></b></i>${esc(c.v)}</span>`}
  return `<span class="kc-${c.c||''}">${esc(c.v)}</span>`}
function kSortVal(c){if(c==null)return'';if(typeof c==='object'){if(c.t!==undefined)return -(c.t||0);if(c.dur!==undefined)return c.dur;if(c.bar!==undefined)return c.bar??-1;c=c.v}
  const n=parseFloat(c);return isNaN(n)?String(c).toLowerCase():n}
function kStop(){clearInterval(K.timer);K.timer=null;clearInterval(K.logT);K.logT=null}
window.k8Leave=()=>{kStop();$('#k8d')&&$('#k8d').classList.add('hidden');$('#k8bulk').classList.add('hidden')};

// ---- cluster list ----
async function k8Home(){setView('k8s');renderPTabs();kStop();K.cid=null;K.sel=null;K.pick.clear();$('#k8d').classList.add('hidden');$('#k8bulk').classList.add('hidden');
  $('#k8v').innerHTML=`<div class="k8home"><div class="hero" style="margin-bottom:22px"><div><h1 style="font-size:24px;margin:0">Kubernetes clusters</h1><p class="mut small" style="margin-top:4px"><span class="spin"></span> reading kubeconfig…</p></div></div><div class="grid">${'<div class="sk" style="height:170px"></div>'.repeat(3)}</div></div>`;
  let j;try{j=await api('/api/k8s_clusters')}catch(e){$('#k8v').innerHTML=`<div class="k8home"><div class="note bad">${esc(e.message)}</div></div>`;return}
  if(S.view!=='k8s')return;
  Object.assign(K,{clusters:j.clusters,kubectl:j.kubectl,helm:j.helm,known:j.known});renderK8Home();k8Infos()}
function renderK8Home(){
  const all=K.clusters,hid=all.filter(c=>c.hidden).length,f=K.cfilter.toLowerCase();
  const vis=all.filter(c=>(K.showHidden||!c.hidden)&&(!f||(c.name+' '+c.sub).toLowerCase().includes(f)));
  $('#k8v').innerHTML=`<div class="k8home"><div class="hero" style="margin-bottom:18px"><div><h1 style="font-size:24px;margin:0">Kubernetes clusters</h1>
    <p class="mut small" style="margin-top:4px">${all.length} cluster${all.length===1?'':'s'} · from your kubeconfig${K.clusters.some(c=>c.type==='ssh')?' and servers over SSH':''}${K.kubectl?'':' · <span class="kc-warn">kubectl not found on this computer</span>'}</p></div>
    <div class="hero-actions"><button class="btn" id="k8add">${ic('plus',14)} Add cluster from a server</button></div></div>
    ${!K.kubectl?`<div class="note warn" style="margin-bottom:14px">kubectl is not installed on this computer, so clusters from your kubeconfig can't be shown. Install kubectl, or add a server that runs kubectl / k3s / microk8s with <b>Add cluster from a server</b>.</div>`:''}
    <div class="toolbar" style="padding:0 0 14px"><div class="search"><span>${ic('search',15)}</span><input id="k8cq" placeholder="Filter clusters…" value="${esc(K.cfilter)}" autocomplete="off"></div>
      ${hid?`<label class="toggle"><input type="checkbox" id="k8sh" ${K.showHidden?'checked':''}><span></span>Show hidden (${hid})</label>`:''}
      <button class="btn ghost" id="k8rel">${ic('restart',15)} Refresh</button></div>
    <div class="grid">${vis.map((c,i)=>kCard(c,i)).join('')||`<div class="empty">No clusters${f?' match':''}. ${K.kubectl?'Your kubeconfig has no contexts.':''}</div>`}</div></div>`;
  $('#k8add').onclick=k8AddSsh;$('#k8rel').onclick=()=>{K.info={};k8Home()};
  $('#k8cq').oninput=e=>{K.cfilter=e.target.value;const p=e.target.selectionStart;renderK8Home();const i=$('#k8cq');i.focus();i.setSelectionRange(p,p)};
  $('#k8sh')&&($('#k8sh').onchange=e=>{K.showHidden=e.target.checked;renderK8Home();k8Infos()});
  $$('#k8v .kcard').forEach(el=>{const c=K.clusters.find(x=>x.id===el.dataset.cid);
    el.onclick=e=>{if(e.target.closest('[data-kmenu]'))return k8CMenu(c);if(e.target.closest('[data-kunlock]'))return k8Unlock(c);k8Open(c.id)}})}
function kCard(c,i){const inf=K.info[c.id];
  let st;if(!inf)st=`<span class="spin"></span> connecting…`;
  else if(inf.locked)st=`<span class="kc-warn">${ic('lock',13)} sudo password needed</span> <button class="btn sm" data-kunlock>Unlock</button>`;
  else if(inf.error)st=`<div class="kerr">${esc(inf.error)}</div>`;
  else st=`<span><b>${esc(inf.version)}</b></span><span>·</span><span><b class="${inf.ready<inf.nodes?'kc-warn':''}">${inf.ready}/${inf.nodes}</b> nodes ready</span>`;
  const off=inf&&(inf.error||inf.locked);
  return `<div class="scard kcard ${off?'off':''}" data-cid="${esc(c.id)}" style="--d:${Math.min(i,10)*35}ms">
   <div class="sc-top"><div class="pulse ${!inf?'st-unknown':off?'st-error':'st-ok'}"></div><div style="min-width:0;flex:1"><div class="sc-name" title="${esc(c.name)}">${c.protected?`<span style="color:var(--warn)">${ic('shield',14)}</span> `:''}${esc(kShort(c.name))}</div>
   <div class="ksub" title="${esc(c.sub)}">${esc(c.sub)}</div></div><button class="icon-btn sm" data-kmenu title="Options">${ic('more',15)}</button></div>
   <div class="row" style="gap:6px">${c.type==='ssh'?'<span class="kbadge ssh">SSH</span>':'<span class="kbadge">kubeconfig</span>'}${c.current?'<span class="kbadge cur">current</span>':''}${c.hidden?'<span class="kbadge">hidden</span>':''}${c.namespace?`<span class="kbadge">ns ${esc(c.namespace)}</span>`:''}</div>
   <div class="kst">${st}</div></div>`}
async function k8Infos(){const list=K.clusters.filter(c=>(K.showHidden||!c.hidden)&&!K.info[c.id]);let i=0;
  const work=async()=>{while(i<list.length){const c=list[i++];let r;
      try{const j=await raw('/api/k8s_info',{cid:c.id});r=j.need_sudo?{locked:true}:j.ok?j:{error:j.error}}catch(e){r={error:e.message}}
      K.info[c.id]=r;if(S.view==='k8s'&&!K.cid){const el=$(`#k8v .kcard[data-cid="${CSS.escape(c.id)}"]`);if(el){const idx=[...el.parentNode.children].indexOf(el);el.outerHTML=kCard(c,idx);renderK8HomeWire()}}}};
  await Promise.all([work(),work(),work(),work()])}
function renderK8HomeWire(){$$('#k8v .kcard').forEach(el=>{const c=K.clusters.find(x=>x.id===el.dataset.cid);
    el.onclick=e=>{if(e.target.closest('[data-kmenu]'))return k8CMenu(c);if(e.target.closest('[data-kunlock]'))return k8Unlock(c);k8Open(c.id)}})}
async function k8Unlock(c){if(await askSudo(c.name)){delete K.info[c.id];k8Infos()}}
function k8CMenu(c){
  modal(`<div class="form"><h3><span class="bigic" style="width:36px;height:36px;border-radius:11px">${ic('k8s',18)}</span> ${esc(kShort(c.name))}</h3>
    <p class="mono small" style="margin-top:-4px;word-break:break-all">${esc(c.name)}<br><span class="dim">${esc(c.sub)}</span></p>
    <div class="row" style="gap:10px;margin-top:8px;align-items:center"><span class="shield">${ic('shield',18)}</span>
      <div class="grow small">${c.protected?'<b>Protected.</b> Delete, scale, restart, edit YAML… ask you to type the name first.':'<b>Protect this cluster</b> (e.g. production): every change asks you to type its name first.'}</div>
      <button class="btn sm" id="kmprot">${c.protected?'Remove protection':'Protect'}</button></div>
    <div class="msep"></div>
    <div class="row end" style="gap:8px">${c.type==='ctx'?`<button class="btn" id="kmhide">${c.hidden?'Show this context again':'Hide from this page'}</button>`:`<button class="btn danger" id="kmrm">${ic('x',14)} Remove cluster</button>`}
    <button class="btn ghost" id="mno">Close</button></div></div>`);
  $('#mno').onclick=closeModal;
  $('#kmprot').onclick=async()=>{try{await api('/api/k8s_cfg',{op:'protect',cid:c.id,on:!c.protected});c.protected=!c.protected;closeModal();
    toast(c.protected?`${ic('shield',14)} <b>${esc(kShort(c.name))}</b> is now protected`:`Protection removed from <b>${esc(kShort(c.name))}</b>`,'ok');S.view==='k8s'&&(K.cid?renderK8Nav():renderK8Home())}catch(e){toast(esc(e.message),'bad')}};
  $('#kmhide')&&($('#kmhide').onclick=async()=>{await api('/api/k8s_cfg',{op:c.hidden?'unhide':'hide',cid:c.id}).catch(e=>toast(esc(e.message),'bad'));c.hidden=!c.hidden;closeModal();renderK8Home();k8Infos()});
  $('#kmrm')&&($('#kmrm').onclick=async()=>{closeModal();if(!await confirmBox(`Remove the cluster <b>${esc(c.name)}</b> from this page? Nothing changes on the server or the cluster.`,'Remove',true))return;
    await api('/api/k8s_cfg',{op:'remove_ssh',server:c.name}).catch(e=>toast(esc(e.message),'bad'));k8Home()})}
function k8AddSsh(){
  modal(`<div class="form"><h3><span class="bigic" style="width:36px;height:36px;border-radius:11px">${ic('k8s',18)}</span> Add cluster from a server</h3>
   <p>Pick a server that runs <b>kubectl</b>, <b>k3s</b> or <b>microk8s</b>. dctl runs kubectl there over SSH — nothing is installed. Clusters in your local kubeconfig show up automatically.</p>
   ${K.known.length?`<div class="pick" id="k8pick">${K.known.map(k=>`<label class="pk"><input type="radio" name="k8s" value="${esc(k.name)}" style="width:16px!important;accent-color:#7c5cff"><div><b>${esc(k.name)}</b><span class="mono">${esc(k.host)}</span></div></label>`).join('')}</div>`
     :'<div class="note">No servers yet. Add one on the Docker or PM2 page first.</div>'}
   <div id="k8aerr" class="err" style="margin-top:10px"></div>
   <div class="row end"><button class="btn ghost" id="mno">Cancel</button><button class="btn primary" id="myes">${ic('check',15)} Check &amp; add</button></div></div>`,true);
  $('#mno').onclick=closeModal;
  $('#myes').onclick=async()=>{const s=$('#k8pick input:checked')?.value;if(!s){$('#k8aerr').textContent='Pick a server';return}
    $('#myes').disabled=true;$('#k8aerr').innerHTML='<span class="spin"></span> <span class="mut">looking for kubectl…</span>';
    try{await api('/api/k8s_cfg',{op:'add_ssh',server:s});closeModal();toast(`<b>${esc(s)}</b> added as a cluster`,'ok');k8Home()}
    catch(e){if($('#k8aerr')){$('#k8aerr').textContent=e.message;$('#myes').disabled=false}}}}

// ---- cluster view ----
async function k8Open(cid,kind){K.cid=cid;K.kind=kind||lsg('k8kind:'+cid)||'overview';K.ns=lsg('k8ns:'+cid)||'';K.sel=null;K.pick.clear();K.q='';K.data=null;K.sort=null;K.cr=null;
  if(K.kind.startsWith('cr:'))K.kind='crds';
  $('#k8v').innerHTML=`<div class="k8wrap"><aside class="k8nav" id="k8nav"></aside><div class="k8main"><div class="k8head" id="k8head"></div><div class="k8body" id="k8body"></div></div></div>`;
  renderK8Nav();renderK8Head();k8Load();kStart();
  try{const j=await api('/api/k8s_ns',{cid});if(K.cid!==cid)return;K.nsList=j.namespaces;if(K.ns&&!K.nsList.includes(K.ns))K.ns='';renderK8Head()}catch(_){K.nsList=[]}}
function kStart(){clearInterval(K.timer);K.timer=setInterval(()=>{if(S.view!=='k8s'||!K.cid||!K.live||document.hidden||!$('#modal').classList.contains('hidden'))return;
  if(['helm','pf','crds'].includes(K.kind)&&!K.cr)return;k8Load(true)},K.kind==='overview'?15000:5000)}
function renderK8Nav(){const c=kCl();
  $('#k8nav').innerHTML=`<div class="kcl"><span class="bigic" style="width:32px;height:32px;border-radius:10px;flex:none">${ic('k8s',16)}</span><div style="min-width:0"><b title="${esc(c.name)}">${c.protected?`<span style="color:var(--warn)">${ic('shield',13)}</span> `:''}${esc(kShort(c.name))}</b><div class="dim small">${c.type==='ssh'?'via SSH':'kubeconfig'}</div></div></div>
   ${KNAV.map(([h,items])=>`<h5>${h}</h5>`+items.map(([k,t,i])=>`<a data-kk="${k}" class="${K.kind===k?'on':''}">${ic(i,14)}${t}</a>`).join('')).join('')}`;
  $('#k8nav').onclick=async e=>{const a=e.target.closest('[data-kk]');if(!a)return;if(!await k8CloseD())return;K.kind=a.dataset.kk;K.cr=null;lss('k8kind:'+K.cid,K.kind);K.data=null;K.sort=null;K.pick.clear();
    renderK8Nav();renderK8Head();k8Load();kStart()}}
function kTitle(){return K.cr?K.cr.kind:(KT[K.kind]||{}).t||K.kind}
function kNamespaced(){if(K.cr)return K.cr.namespaced;if(['nodes','namespaces','pv','storageclasses','clusterroles','clusterrolebindings','crds','priorityclasses','ingressclasses','pf'].includes(K.kind))return false;return true}
function renderK8Head(){const c=kCl(),nsd=kNamespaced()&&K.kind!=='helm';
  $('#k8head').innerHTML=`<button class="icon-btn" id="k8back" title="All clusters">${ic('back',18)}</button>
   <h2>${K.cr?`<a class="dim" id="k8crb" style="cursor:pointer;font-weight:600">CRDs /</a> `:''}${esc(kTitle())} <span id="k8cnt">·</span></h2><span class="grow"></span>
   ${K.kind!=='pf'?`<select id="k8ns" ${nsd||K.kind==='overview'?'':'disabled'} title="Namespace"><option value="">All namespaces</option>${K.nsList.map(n=>`<option ${n===K.ns?'selected':''}>${esc(n)}</option>`).join('')}</select>`:''}
   ${!['overview','pf'].includes(K.kind)?`<div class="search"><span>${ic('search',15)}</span><input id="k8q" placeholder="Filter…" value="${esc(K.q)}" autocomplete="off"><kbd>/</kbd></div>`:''}
   <label class="toggle" title="Refresh automatically"><input type="checkbox" id="k8live" ${K.live?'checked':''}><span></span>Live</label>
   <button class="btn ghost" id="k8ref" title="Refresh">${ic('restart',15)}</button>
   <button class="btn" id="k8new" title="Create a resource from YAML">${ic('plus',14)} Create</button>
   <button class="icon-btn" id="k8cm" title="Cluster options">${ic('more',16)}</button>`;
  $('#k8back').onclick=async()=>{if(await k8CloseD())k8Home()};$('#k8ref').onclick=()=>k8Load();$('#k8new').onclick=k8Create;$('#k8cm').onclick=()=>k8CMenu(c);
  $('#k8live').onchange=e=>{K.live=e.target.checked};
  $('#k8crb')&&($('#k8crb').onclick=()=>{K.cr=null;K.data=null;renderK8Head();k8Load()});
  $('#k8ns')&&($('#k8ns').onchange=e=>{K.ns=e.target.value;lss('k8ns:'+K.cid,K.ns);K.data=null;K.pick.clear();k8Load()});
  $('#k8q')&&($('#k8q').oninput=e=>{K.q=e.target.value;renderK8Table()})}
async function k8Load(quiet){if(!K.cid)return;const seq=++K.seq,cid=K.cid,kind=K.kind;
  if(!quiet&&!K.data)$('#k8body').innerHTML=`<div class="mut" style="padding:40px;text-align:center"><span class="spin"></span> loading…</div>`;
  try{
    if(kind==='overview'){const j=await api('/api/k8s_overview',{cid,ns:K.ns});if(seq!==K.seq)return;renderK8Over(j);return}
    if(kind==='pf'){const j=await api('/api/k8s_pf',{op:'list'});if(seq!==K.seq)return;renderK8Pf(j.forwards);return}
    if(kind==='helm'){const j=await api('/api/k8s_helm',{cid,op:'list'});if(seq!==K.seq)return;
      const rel=j.releases.filter(r=>!K.ns||r.namespace===K.ns);
      K.data={namespaced:true,helm:true,cols:['Revision','Status','Chart','App version','Updated'],rows:rel.map(r=>({name:r.name,ns:r.namespace,created:null,
        cells:[String(r.revision),{v:r.status,c:r.status==='deployed'?'ok':/fail/.test(r.status)?'bad':'warn'},r.chart,r.app_version,r.updated?String(r.updated).slice(0,19).replace('T',' '):''],st:r.status==='deployed'?'ok':/fail/.test(r.status)?'bad':'warn',x:r}))};
      renderK8Table();return}
    const kk=K.cr?'cr:'+K.cr.resource:kind;
    const j=await api('/api/k8s_list',{cid,kind:kk,ns:K.ns,namespaced:K.cr?K.cr.namespaced:undefined});if(seq!==K.seq||K.cid!==cid)return;
    K.data=j;renderK8Table();
    if(K.sel&&K.sel.kind===kk){const r=j.rows.find(x=>x.name===K.sel.name&&x.ns===K.sel.ns);if(r){K.sel.row=r;kDHead()}}
  }catch(e){if(seq!==K.seq)return;if(!quiet)$('#k8body').innerHTML=`<div class="note bad" style="margin-top:10px;white-space:pre-wrap">${esc(e.message)}</div>`;
    else $('#k8cnt')&&($('#k8cnt').textContent='!')}}
function kRows(){const d=K.data;if(!d)return[];const q=K.q.trim().toLowerCase();
  let rows=d.rows.filter(r=>!q||(r.name+' '+r.ns+' '+r.cells.map(c=>c&&typeof c==='object'?(c.v??''):c).join(' ')).toLowerCase().includes(q));
  if(K.sort){const {k,dir}=K.sort;const v=r=>k==='name'?r.name.toLowerCase():k==='ns'?r.ns:k==='age'?-(r.created||0):kSortVal(r.cells[k]);
    rows=rows.slice().sort((a,b)=>{const x=v(a),y=v(b);return (x>y?1:x<y?-1:0)*dir})}
  return rows}
function kQuick(r){const k=K.kind;let h='';
  if(k==='pods'){h+=`<button class="icon-btn sm" data-qa="logs" title="Logs">${ic('logs',14)}</button>`;if(!NOPTY)h+=`<button class="icon-btn sm" data-qa="shell" title="Shell">${ic('term',14)}</button>`}
  if(['deployments','statefulsets','replicasets'].includes(k))h+=`<button class="icon-btn sm" data-qa="scale" title="Scale">${ic('up',14)}</button>`;
  if(['deployments','statefulsets','daemonsets'].includes(k))h+=`<button class="icon-btn sm" data-qa="restart" title="Rollout restart">${ic('restart',14)}</button>`;
  if(k==='cronjobs')h+=`<button class="icon-btn sm" data-qa="trigger" title="Run now">${ic('play',13)}</button>`;
  if(!['events','helm','crds'].includes(k)||K.cr)h+=`<button class="icon-btn sm x2" data-qa="delete" title="Delete">${ic('trash',14)}</button>`;
  return h}
function renderK8Table(){const d=K.data;if(!d||!$('#k8body'))return;
  if(['overview','pf'].includes(K.kind)&&!d.helm)return;
  const rows=kRows(),showNs=d.namespaced&&!K.ns,ev=K.kind==='events'&&!K.cr,canPick=!['events','crds','helm'].includes(K.kind)||K.cr;
  $('#k8cnt').textContent=rows.length+(rows.length!==d.rows.length?' / '+d.rows.length:'');
  const lim=rows.slice(0,1500),sk=K.sort||{};
  const th=(k,t)=>`<th data-s="${k}" class="${sk.k===k?'srt'+(sk.dir<0?' up':''):''}">${t}</th>`;
  const metNote=d.metrics===false&&['pods','nodes'].includes(K.kind)?`<div class="dim small" style="margin:0 0 8px">CPU / memory usage needs <b>metrics-server</b> in the cluster.</div>`:'';
  $('#k8body').innerHTML=metNote+(rows.length?`<table class="kt"><thead><tr>${canPick?`<th class="w0"><input type="checkbox" id="kall"></th>`:''}${th('name','Name')}${showNs?th('ns','Namespace'):''}
    ${d.cols.map((c,i)=>th(i,esc(c))).join('')}${d.helm?'':th('age','Age')}<th></th></tr></thead><tbody>
    ${lim.map((r,i)=>{const key=r.ns+'/'+r.name,on=K.sel&&K.sel.name===r.name&&K.sel.ns===r.ns;
      return `<tr data-i="${i}" class="${on?'on':''} ${K.pick.has(key)?'pk':''}">${canPick?`<td class="w0"><input type="checkbox" data-pk="${esc(key)}" ${K.pick.has(key)?'checked':''}></td>`:''}
      <td class="kn" title="${esc(r.name)}"><i class="${r.st||''}"></i>${esc(r.name)}</td>${showNs?`<td>${esc(r.ns)}</td>`:''}
      ${r.cells.map((c,ci)=>`<td class="${ev&&ci===3?'msg':''}" ${typeof c==='string'&&c.length>40?`title="${esc(c)}"`:''}>${kCell(c)}</td>`).join('')}${d.helm?'':`<td>${kAgo(r.created)}</td>`}<td class="ka">${kQuick(r)}</td></tr>`}).join('')}
    </tbody></table>${rows.length>lim.length?`<div class="dim small" style="padding:10px">Showing 1500 of ${rows.length} — use the filter.</div>`:''}`
    :`<div class="empty">${d.rows.length?'Nothing matches the filter.':`No ${esc(kTitle())}${K.ns?` in namespace <b>${esc(K.ns)}</b>`:''}.`}</div>`);
  K.view=lim;kBulk();
  const tb=$('#k8body tbody');if(!tb)return;
  $$('#k8body th[data-s]').forEach(t=>t.onclick=()=>{const k=t.dataset.s,kk=isNaN(+k)?k:+k;K.sort=K.sort&&K.sort.k===kk?{k:kk,dir:-K.sort.dir}:{k:kk,dir:1};renderK8Table()});
  $('#kall')&&($('#kall').onchange=e=>{lim.forEach(r=>{const k=r.ns+'/'+r.name;e.target.checked?K.pick.add(k):K.pick.delete(k)});renderK8Table()});
  tb.onclick=e=>{const tr=e.target.closest('tr');if(!tr)return;const r=K.view[+tr.dataset.i];if(!r)return;
    const pk=e.target.closest('[data-pk]');if(pk){pk.checked?K.pick.add(pk.dataset.pk):K.pick.delete(pk.dataset.pk);tr.classList.toggle('pk',pk.checked);kBulk();return}
    if(e.target.closest('.w0'))return;
    const qa=e.target.closest('[data-qa]');if(qa){e.stopPropagation();return kAct(qa.dataset.qa,r)}
    if(ev&&r.x&&r.x.obj&&KIND_OF[r.x.obj.kind]){return k8Detail({...r,name:r.x.obj.name,ns:r.x.obj.ns||''},KIND_OF[r.x.obj.kind])}
    k8Detail(r)}}
function kBulk(){const n=K.pick.size,b=$('#k8bulk');if(!n||S.view!=='k8s'){b.classList.add('hidden');return}
  b.classList.remove('hidden');b.innerHTML=`<b>${n}</b> selected<button class="btn sm danger" id="kbdel">${ic('trash',13)} Delete</button><button class="btn sm ghost" id="kbclr">Clear</button>`;
  $('#kbclr').onclick=()=>{K.pick.clear();renderK8Table()};
  $('#kbdel').onclick=async()=>{const items=[...K.pick].map(k=>{const i=k.indexOf('/');return{ns:k.slice(0,i),name:k.slice(i+1)}});
    if(!await confirmBox(`Delete <b>${items.length}</b> ${esc(kTitle())}?<br><span class="mono small">${items.slice(0,8).map(i=>esc(i.name)).join('<br>')}${items.length>8?'<br>…':''}</span>`,'Delete',true))return;
    try{await api('/api/k8s_action',{cid:K.cid,kind:K.cr?'cr:'+K.cr.resource:K.kind,namespaced:K.cr?K.cr.namespaced:undefined,op:'delete',items});K.pick.clear();toast(`Deleted ${items.length}`,'ok');k8Load()}catch(e){toast(esc(e.message),'bad')}}}

// ---- overview ----
function renderK8Over(o){if(!$('#k8body'))return;$('#k8cnt').textContent=K.ns||'cluster';
  const res=(t,r,fmt)=>{const a=r.alloc||1,row=(l,v,c)=>v==null?'':`<div><div class="rl"><span>${l}</span><b>${fmt(v)} <span class="dim">/ ${fmt(r.alloc)} · ${Math.round(v/a*100)}%</span></b></div><div class="rb"><i style="width:${clamp(v/a*100)}%;background:${c}"></i></div></div>`;
    return `<div class="kbox"><h4>${ic('cpu',14)} ${t}</h4><div class="kres">${row('Usage',r.use,'var(--grad)')}${row('Requests',r.req,'linear-gradient(90deg,#5fa8ff,#7c5cff)')}${row('Limits',r.lim,'linear-gradient(90deg,#ffb020,#ff7a5c)')}
      ${r.use==null?'<div class="dim small">Live usage needs metrics-server.</div>':''}</div></div>`};
  const fc=m=>m>=1000?(m/1000).toFixed(m>=10000?0:1)+' cores':Math.round(m)+'m',fm=b=>fmtB(b);
  const ph=o.phase,wk=Object.entries(o.workloads);
  $('#k8body').innerHTML=`<div class="kov">
    <div class="kbox"><h4>${ic('server',14)} Nodes</h4><div class="big ${o.ready<o.nodes?'kc-warn':''}">${o.ready}<small> / ${o.nodes} ready</small></div></div>
    <div class="kbox"><h4>${ic('box',14)} Pods${K.ns?' · '+esc(K.ns):''}</h4><div class="big">${o.pods}<small>${!K.ns&&o.podcap?` / ${o.podcap} capacity`:''}</small></div>
      <div class="kdots" style="margin-top:8px"><span><i style="background:var(--ok)"></i>${ph.ok} running</span><span><i style="background:var(--warn)"></i>${ph.warn} pending</span><span><i style="background:var(--bad)"></i>${ph.bad} failing</span><span><i style="background:var(--dim)"></i>${ph.dim} completed</span></div></div>
    ${res('CPU',o.cpu,fc)}${res('Memory',o.mem,fm)}</div>
   ${wk.length?`<div class="kov">${wk.map(([k,v])=>`<div class="kbox" style="cursor:pointer" data-wk="${esc(KIND_OF[k]||'')}"><h4>${ic('layers',14)} ${esc(k)}s</h4><div class="big">${v.total}</div>
     <div class="kdots" style="margin-top:6px">${v.ok?`<span><i style="background:var(--ok)"></i>${v.ok} ok</span>`:''}${v.warn?`<span><i style="background:var(--warn)"></i>${v.warn} not ready</span>`:''}${v.bad?`<span><i style="background:var(--bad)"></i>${v.bad} failing</span>`:''}</div></div>`).join('')}</div>`:''}
   <h3 style="margin:18px 0 10px;font-size:15px;display:flex;gap:8px;align-items:center">${ic('alert',16)} Warnings <span class="pill">${o.warnings.length}</span></h3>
   ${o.warnings.length?`<table class="kt"><thead><tr><th>Object</th>${K.ns?'':'<th>Namespace</th>'}<th>Reason</th><th>Message</th><th>Count</th><th>Last seen</th></tr></thead><tbody>
     ${o.warnings.map(w=>`<tr><td class="kn"><i class="warn"></i>${esc(w.obj)}</td>${K.ns?'':`<td>${esc(w.ns)}</td>`}<td class="kc-warn">${esc(w.reason)}</td><td class="msg">${esc(w.msg)}</td><td>${esc(w.count)}</td><td>${kAgo(w.t)}</td></tr>`).join('')}</tbody></table>`
     :`<div class="note good">No warning events${K.ns?' in this namespace':''}. 🎉</div>`}`;
  $$('#k8body [data-wk]').forEach(b=>b.onclick=()=>{const k=b.dataset.wk;if(!k)return;$(`#k8nav [data-kk="${k}"]`)?.click()})}

// ---- port forwards ----
function renderK8Pf(list){if(!$('#k8body'))return;$('#k8cnt').textContent=list.length;
  $('#k8body').innerHTML=`<p class="mut small" style="margin-top:0">Open a forward from a Pod, Service or Deployment (detail panel → <b>Port forward</b>). Forwards run on this computer while dctl is open.</p>`+
   (list.length?list.map(f=>{const c=K.clusters.find(x=>x.id===f.cid);return `<div class="kpf"><span class="pulse ${f.dead?'st-error':'st-ok'}" style="margin:0"></span>
     <div class="grow" style="min-width:0"><b>${esc(f.kind.replace(/s$/,''))}/${esc(f.name)}</b> <span class="dim">· ${esc(f.ns)} · ${esc(c?kShort(c.name):f.cid)}</span><div class="mono small">127.0.0.1:${f.local} → ${f.port}${f.dead?' <span class="kc-bad">stopped</span>':''}</div></div>
     ${f.dead?'':`<a class="btn sm" href="http://127.0.0.1:${f.local}/" target="_blank" rel="noopener">${ic('globe',13)} Open</a><button class="btn sm ghost" data-cp="127.0.0.1:${f.local}">${ic('copy',13)}</button>`}
     <button class="btn sm danger" data-stop="${esc(f.id)}">${f.dead?'Remove':'Stop'}</button></div>`}).join(''):`<div class="empty">No port forwards running.</div>`);
  $$('#k8body [data-stop]').forEach(b=>b.onclick=async()=>{const j=await api('/api/k8s_pf',{op:'stop',id:b.dataset.stop}).catch(e=>toast(esc(e.message),'bad'));if(j)renderK8Pf(j.forwards)});
  $$('#k8body [data-cp]').forEach(b=>b.onclick=()=>{navigator.clipboard?.writeText(b.dataset.cp);toast('Copied','ok',1500)})}

// ---- actions ----
function kSel(r,kind){return{kind:kind||(K.cr?'cr:'+K.cr.resource:K.kind),namespaced:K.cr?K.cr.namespaced:undefined,name:r.name,ns:r.ns,row:r}}
async function kAct(op,r,kind){const o=kSel(r,kind),base={cid:K.cid,kind:o.kind,namespaced:o.namespaced,ns:o.ns,name:o.name};
  const done=(msg)=>{toast(msg,'ok');setTimeout(()=>k8Load(true),600)};
  try{
    if(op==='logs'){await k8Detail(r,o.kind);kTab('logs');return}
    if(op==='shell')return kShell(o);
    if(op==='delete'){if(!await confirmBox(`Delete ${esc((KOBJ[o.kind]||o.kind).toLowerCase())} <b>${esc(o.name)}</b>${o.ns?` in <b>${esc(o.ns)}</b>`:''}?${o.kind==='pods'&&r.x&&r.x.owner?'<br><span class="dim small">It belongs to '+esc(r.x.owner)+', which will start a new pod.</span>':''}`,'Delete',true))return;
      await api('/api/k8s_action',{...base,op:'delete'});if(K.sel&&K.sel.name===o.name&&K.sel.ns===o.ns)k8CloseD(true);done(`<b>${esc(o.name)}</b> deleted`);return}
    if(op==='scale'){const cur=(r.x&&r.x.replicas)??1;const n=await kPrompt(`Scale ${esc(o.name)}`,'Replicas',String(cur),'number');if(n==null)return;
      await api('/api/k8s_action',{...base,op:'scale',replicas:+n});done(`<b>${esc(o.name)}</b> scaled to ${+n}`);return}
    if(op==='restart'){if(!await confirmBox(`Rollout restart <b>${esc(o.name)}</b>? Pods are replaced one by one.`,'Restart'))return;
      await api('/api/k8s_action',{...base,op:'restart'});done(`<b>${esc(o.name)}</b> restarting`);return}
    if(op==='trigger'){const j=await api('/api/k8s_action',{...base,op:'trigger'});done(esc(j.output||'job created'));return}
    if(op==='suspend'||op==='resume'){await api('/api/k8s_action',{...base,op});done(`<b>${esc(o.name)}</b> ${op==='suspend'?'suspended':'resumed'}`);return}
    if(op==='cordon'||op==='uncordon'){await api('/api/k8s_action',{...base,op});done(`<b>${esc(o.name)}</b> ${op}ed`);return}
    if(op==='drain'){if(!await confirmBox(`Drain node <b>${esc(o.name)}</b>? It is cordoned and its pods are evicted (DaemonSets stay). This can take a while.`,'Drain',true))return;
      toast('Draining… (up to 2 min)','info');const j=await api('/api/k8s_action',{...base,op:'drain'});done(`<b>${esc(o.name)}</b> drained`);return}
    if(op==='pf')return kPf(o,r);
  }catch(e){toast(esc(e.message),'bad',7000)}}
function kPrompt(title,label,val,type){return new Promise(res=>{
  dlg2(`<h3>${title}</h3><div class="form"><label>${label}</label><input id="d2v" type="${type||'text'}" value="${esc(val||'')}" min="0"></div>
   <div class="row end" style="margin-top:12px"><button class="btn ghost" id="d2no">Cancel</button><button class="btn primary" id="d2yes">OK</button></div>`);
  const ok=()=>{const v=$('#d2v').value.trim();closeDlg2();res(v===''?null:v)};
  $('#d2yes').onclick=ok;$('#d2v').onkeydown=e=>{if(e.key==='Enter')ok()};$('#d2no').onclick=()=>{closeDlg2();res(null)};setTimeout(()=>{$('#d2v').focus();$('#d2v').select()},30)})}
async function kShell(o){const cs=(o.row.x&&o.row.x.containers)||[];let c=cs[0]||'';
  if(cs.length>1){c=await choose2(`Shell into ${esc(o.name)}`,'<p>Which container?</p>',cs.map(x=>[x,esc(x),'']));if(!c)return}
  openTerm(kShort(kCl().name),{container:o.name+(cs.length>1?'/'+c:''),k8s:{cid:K.cid,ns:o.ns,pod:o.name,c}})}
function kPf(o,r){const ports=((r.x&&r.x.ports)||[]).filter(Boolean);
  dlg2(`<h3>${ic('globe',18)} Port forward · ${esc(o.name)}</h3><div class="form">
   <div class="two" style="grid-template-columns:1fr 1fr"><div><label>Port in the cluster</label>${ports.length?`<select id="d2p" style="width:100%">${ports.map(p=>`<option>${p}</option>`).join('')}</select>`:'<input id="d2p" type="number" placeholder="8080">'}</div>
   <div><label>Local port <span class="dim">(empty = any free)</span></label><input id="d2l" type="number" placeholder="auto"></div></div></div>
   <div id="d2err" class="err" style="margin-top:8px"></div>
   <div class="row end" style="margin-top:8px"><button class="btn ghost" id="d2no">Cancel</button><button class="btn primary" id="d2yes">${ic('play',13)} Start</button></div>`);
  $('#d2no').onclick=closeDlg2;
  $('#d2yes').onclick=async()=>{$('#d2yes').disabled=true;$('#d2err').innerHTML='<span class="spin"></span>';
    try{const j=await api('/api/k8s_pf',{op:'start',cid:K.cid,kind:o.kind,ns:o.ns,name:o.name,port:+$('#d2p').value,local:+$('#d2l').value||0});closeDlg2();
      toast(`Forwarding <a href="http://127.0.0.1:${j.local}/" target="_blank" rel="noopener"><b>127.0.0.1:${j.local}</b></a> → ${esc(o.name)}:${esc($('#d2p')?.value||'')}`,'ok',9000)}
    catch(e){if($('#d2err')){$('#d2err').textContent=e.message;$('#d2yes').disabled=false}}}}

// ---- detail drawer ----
function kDirty(){return K.cm&&K.sel&&K.sel.tab==='yaml'&&(K.cm.getValue?K.cm.getValue():K.cm.value)!==K.yorig}
async function k8CloseD(force){if(!K.sel)return true;if(!force&&kDirty()&&!await confirmBox('Discard your YAML changes?','Discard',true))return false;
  clearInterval(K.logT);K.logT=null;K.sel=null;K.cm=null;$('#k8d').classList.add('hidden');$$('#k8body tr.on').forEach(t=>t.classList.remove('on'));return true}
async function k8Detail(r,kind){if(K.sel&&!await k8CloseD())return;
  K.sel={...kSel(r,kind),tab:'overview'};
  if(kind&&kind!==K.kind)K.sel.namespaced=undefined;
  $('#k8d').innerHTML=`<div id="kdh"></div><div class="kdbody" id="kdb"></div>`;$('#k8d').classList.remove('hidden');
  K.sel.tab=K.kind==='helm'&&!kind?'values':'overview';kDHead();kTab(K.sel.tab);
  $$('#k8body tbody tr').forEach(t=>{const x=K.view[+t.dataset.i];t.classList.toggle('on',!!x&&x.name===r.name&&x.ns===r.ns)})}
function kTabs(){const k=K.sel.kind;if(K.kind==='helm'&&k==='helm')return[['values','Values'],['history','History'],['manifest','Manifest'],['notes','Notes']];
  const t=[['overview','Overview'],['yaml','YAML'],['events','Events']];if(k==='pods')t.splice(1,0,['logs','Logs']);if(k==='secrets'||k==='configmaps')t.splice(1,0,['data','Data']);return t}
function kDHead(){const s=K.sel;if(!s)return;const r=s.row||{},k=s.kind,x=r.x||{};
  const b=(op,label,icon,cls)=>`<button class="btn sm ${cls||''}" data-da="${op}">${ic(icon,13)} ${label}</button>`;
  let acts='';
  if(K.kind==='helm'&&k==='helm')acts=b('rollback','Roll back…','restart')+b('uninstall','Uninstall','trash','danger');
  else{
    if(k==='pods'){if(!NOPTY)acts+=b('shell','Shell','term','primary');acts+=b('pf','Port forward','globe')}
    if(['deployments','statefulsets','replicasets'].includes(k))acts+=b('scale','Scale','up');
    if(['deployments','statefulsets','daemonsets'].includes(k))acts+=b('restart','Restart','restart');
    if(['deployments','services'].includes(k))acts+=b('pf','Port forward','globe');
    if(k==='cronjobs')acts+=b('trigger','Run now','play')+(x.suspend?b('resume','Resume','play'):b('suspend','Suspend','stop'));
    if(k==='nodes')acts+=(x.unschedulable?b('uncordon','Uncordon','check'):b('cordon','Cordon','stop'))+b('drain','Drain','alert');
    if(k==='crds'&&x.resource)acts+=b('crlist',`Show ${esc(x.kind||'resources')}`,'list','primary');
    if(k!=='events'&&k!=='crds')acts+=b('delete','Delete','trash','danger')}
  const ti=K.kind==='helm'?'k8s':(KT[k]||{}).i||'code';
  if(!$('#kdh'))return;
  $('#kdh').innerHTML=`<div class="dr-head"><div class="dr-ico run" style="${r.st==='bad'?'background:rgba(255,77,109,.12);color:var(--bad);box-shadow:none':r.st==='warn'?'background:rgba(255,176,32,.12);color:var(--warn);box-shadow:none':''}">${ic(ti,22)}</div>
    <div style="flex:1;min-width:0"><h2>${esc(s.name)}</h2><div class="dr-sub"><span>${esc(K.kind==='helm'&&k==='helm'?'Helm release':(KOBJ[k]||(K.cr?K.cr.kind:k)))}</span>${s.ns?`<span>${ic('folder',13)} ${esc(s.ns)}</span>`:''}${r.created?`<span>age ${kAgo(r.created)}</span>`:''}
    ${(r.cells||[]).slice(0,3).map((c,i)=>c&&K.data&&K.data.cols[i]?`<span class="dim">${esc(K.data.cols[i])}:</span>${kCell(c)}`:'').join(' ')}</div></div>
    <button class="icon-btn" id="kdx" title="Close (Esc)">${ic('x',16)}</button></div>
   <div class="dr-actions">${acts}</div>
   <nav class="dtabs" id="kdtabs">${kTabs().map(([t,l])=>`<a data-kt="${t}" class="${s.tab===t?'on':''}">${l}</a>`).join('')}</nav>`;
  $('#kdx').onclick=()=>k8CloseD();
  $('#kdtabs').onclick=async e=>{const a=e.target.closest('[data-kt]');if(!a||a.dataset.kt===K.sel.tab)return;if(kDirty()&&!await confirmBox('Discard your YAML changes?','Discard',true))return;kTab(a.dataset.kt)};
  $$('#k8d [data-da]').forEach(btn=>btn.onclick=()=>{const op=btn.dataset.da;
    if(op==='crlist'){K.cr={resource:x.resource,namespaced:x.namespaced,kind:x.kind};k8CloseD(true);K.data=null;K.pick.clear();renderK8Head();k8Load();return}
    if(op==='rollback'||op==='uninstall')return kHelmAct(op);
    kAct(op,s.row||{name:s.name,ns:s.ns,x:{}},s.kind)})}
async function kTab(t){const s=K.sel;if(!s)return;
  clearInterval(K.logT);K.logT=null;
  s.tab=t;K.cm=null;$$('#kdtabs a').forEach(a=>a.classList.toggle('on',a.dataset.kt===t));const body=$('#kdb');if(!body)return;body.dataset.tab=t;
  const base={cid:K.cid,kind:s.kind,namespaced:s.namespaced,ns:s.ns,name:s.name};
  const load=`<div class="mut" style="padding:30px;text-align:center"><span class="spin"></span></div>`;
  const fail=e=>{if(K.sel===s&&s.tab===t)body.innerHTML=`<div class="note bad" style="white-space:pre-wrap">${esc(e.message)}</div>`};
  if(t==='overview'){body.innerHTML=load;try{const j=await api('/api/k8s_describe',base);if(K.sel!==s||s.tab!==t)return;
      body.innerHTML=`<pre class="kpre">${esc(j.text).replace(/^([A-Z][\w ()/-]*:)/gm,'<span class="k">$1</span>')}</pre>`}catch(e){fail(e)}return}
  if(t==='yaml'){body.innerHTML=load;try{const j=await api('/api/k8s_yaml',base);if(K.sel!==s||s.tab!==t)return;K.yorig=j.yaml;
      body.innerHTML=`<div class="row" style="margin-bottom:10px"><span class="dim small grow">Edit and save — dctl runs <code>kubectl replace</code>. If someone changed it meanwhile, saving fails instead of overwriting.</span>
        <button class="btn sm ghost" id="kyrel">${ic('restart',13)} Reload</button><button class="btn sm primary" id="kysave">${ic('check',13)} Save</button></div><div class="kyaml" id="kyed"></div>`;
      if(window.CodeMirror)K.cm=CodeMirror($('#kyed'),{value:j.yaml,mode:'yaml',theme:'material-darker',lineNumbers:true,indentUnit:2,tabSize:2,
        extraKeys:{'Ctrl-S':()=>$('#kysave').click(),'Cmd-S':()=>$('#kysave').click(),Tab:cm=>cm.replaceSelection('  ')}});
      else{const ta=document.createElement('textarea');ta.value=j.yaml;ta.style.cssText='width:100%;height:100%;background:#05060a;color:#d7dbea;border:0;font:12.5px/1.5 var(--mono);padding:10px';$('#kyed').appendChild(ta);K.cm=ta}
      $('#kyrel').onclick=async()=>{if(kDirty()&&!await confirmBox('Discard your YAML changes?','Discard',true))return;kTab('yaml')};
      $('#kysave').onclick=()=>busy($('#kysave'),async()=>{const y=K.cm.getValue?K.cm.getValue():K.cm.value;if(y===K.yorig){toast('No changes','info',1500);return}
        try{const r=await api('/api/k8s_apply',{cid:K.cid,yaml:y,mode:'replace'});toast(esc(r.output||'saved'),'ok');K.yorig=y;setTimeout(()=>{k8Load(true);if(K.sel===s&&s.tab==='yaml')kTab('yaml')},700)}
        catch(e){toast(esc(e.message),'bad',9000)}})}catch(e){fail(e)}return}
  if(t==='events'){body.innerHTML=load;try{const j=await api('/api/k8s_events',{...base,objkind:KOBJ[s.kind]||(K.cr?K.cr.kind:'')});if(K.sel!==s||s.tab!==t)return;
      body.innerHTML=j.events.length?`<table class="kt"><thead><tr><th>Type</th><th>Reason</th><th>Message</th><th>Count</th><th>Source</th><th>Last seen</th></tr></thead><tbody>${j.events.map(e=>`<tr><td class="${e.type==='Warning'?'kc-warn':''}">${esc(e.type)}</td><td>${esc(e.reason)}</td><td class="msg">${esc(e.msg)}</td><td>${e.count}</td><td>${esc(e.src)}</td><td>${kAgo(e.t)}</td></tr>`).join('')}</tbody></table>`
        :`<div class="empty">No recent events. <span class="dim small">(Kubernetes keeps events for about an hour.)</span></div>`}catch(e){fail(e)}return}
  if(t==='data'){body.innerHTML=load;try{
      if(s.kind==='secrets'){const j=await api('/api/k8s_secret',base);if(K.sel!==s)return;const keys=Object.keys(j.data);
        const dec=v=>{try{const b=atob(v);try{return decodeURIComponent(escape(b))}catch(_){return b}}catch(_){return v}};
        body.innerHTML=`<div class="dim small" style="margin-bottom:10px">Type <code>${esc(j.type)}</code>. Values stay hidden until you press <b>Show</b>.</div><div class="kdata">${keys.map(k=>`<div class="ksv"><div class="kh">${ic('lock',13)} ${esc(k)}<span class="grow"></span><button class="btn sm ghost" data-rev="${esc(k)}">Show</button><button class="btn sm ghost" data-cpy="${esc(k)}">${ic('copy',13)}</button></div><pre data-val="${esc(k)}">••••••••</pre></div>`).join('')||'<div class="empty">No data</div>'}</div>`;
        $$('#kdb [data-rev]').forEach(b=>b.onclick=()=>{const k=b.dataset.rev,p=$(`#kdb pre[data-val="${CSS.escape(k)}"]`);const show=b.textContent==='Show';p.textContent=show?dec(j.data[k]):'••••••••';b.textContent=show?'Hide':'Show'});
        $$('#kdb [data-cpy]').forEach(b=>b.onclick=()=>{navigator.clipboard?.writeText(dec(j.data[b.dataset.cpy]));toast('Copied','ok',1500)})}
      else{const j=await api('/api/k8s_yaml',base);if(K.sel!==s)return;body.innerHTML=`<pre class="kpre">${esc(j.yaml.replace(/^[\s\S]*?\ndata:\n/,'data:\n').replace(/\nkind:[\s\S]*$/,''))}</pre>`}
    }catch(e){fail(e)}return}
  if(t==='logs')return kLogs(s,body);
  if(['values','history','manifest','notes'].includes(t)){const r=s.row||{};body.innerHTML=load;
    try{if(t==='history'){const j=await api('/api/k8s_helm',{cid:K.cid,op:'history',ns:s.ns,name:s.name});if(K.sel!==s)return;
        body.innerHTML=`<table class="kt"><thead><tr><th>Revision</th><th>Updated</th><th>Status</th><th>Chart</th><th>App version</th><th>Description</th><th></th></tr></thead><tbody>${j.history.slice().reverse().map(h=>`<tr><td><b>${h.revision}</b></td><td>${esc(String(h.updated||'').slice(0,19).replace('T',' '))}</td><td class="${h.status==='deployed'?'kc-ok':/fail/.test(h.status)?'kc-bad':''}">${esc(h.status)}</td><td>${esc(h.chart)}</td><td>${esc(h.app_version)}</td><td class="msg">${esc(h.description)}</td><td>${h.status!=='deployed'?`<button class="btn sm" data-rb="${h.revision}">Roll back</button>`:''}</td></tr>`).join('')}</tbody></table>`;
        $$('#kdb [data-rb]').forEach(b=>b.onclick=()=>kHelmAct('rollback',+b.dataset.rb))}
      else{const j=await api('/api/k8s_helm',{cid:K.cid,op:t,ns:s.ns,name:s.name,all:t==='values'&&s.allv});if(K.sel!==s)return;
        body.innerHTML=(t==='values'?`<div class="row" style="margin-bottom:10px"><span class="grow dim small">${s.allv?'All values (chart defaults + yours)':'Values you set (user-supplied)'}</span><button class="btn sm ghost" id="kallv">${s.allv?'Only mine':'Show all'}</button></div>`:'')+`<pre class="kpre">${esc(j.text||'(empty)')}</pre>`;
        $('#kallv')&&($('#kallv').onclick=()=>{s.allv=!s.allv;kTab('values')})}}catch(e){fail(e)}}}
async function kHelmAct(op,rev){const s=K.sel;if(!s)return;
  try{if(op==='uninstall'){if(!await confirmBox(`Uninstall Helm release <b>${esc(s.name)}</b> from <b>${esc(s.ns)}</b>? All its resources are deleted.`,'Uninstall',true))return;
      await api('/api/k8s_helm',{cid:K.cid,op,ns:s.ns,name:s.name});k8CloseD(true);toast(`<b>${esc(s.name)}</b> uninstalled`,'ok');k8Load();return}
    if(rev==null){const v=await kPrompt(`Roll back ${esc(s.name)}`,'Revision number',String(Math.max(1,(+((s.row||{}).x||{}).revision||2)-1)),'number');if(v==null)return;rev=+v}
    if(!await confirmBox(`Roll back <b>${esc(s.name)}</b> to revision <b>${rev}</b>?`,'Roll back'))return;
    await api('/api/k8s_helm',{cid:K.cid,op:'rollback',ns:s.ns,name:s.name,revision:rev});toast(`<b>${esc(s.name)}</b> rolled back to ${rev}`,'ok');k8Load();if(K.sel===s)kTab('history')}
  catch(e){toast(esc(e.message),'bad',8000)}}
function kLogs(s,body){const cs=((s.row||{}).x||{}).containers||[],ini=((s.row||{}).x||{}).init||[];
  const st=s.log||(s.log={c:cs[0]||'',tail:'500',prev:false,ts:false,follow:true,q:''});
  body.innerHTML=`<div class="klogbar">${cs.length+ini.length>1?`<select id="klc">${cs.map(c=>`<option value="${esc(c)}" ${c===st.c?'selected':''}>${esc(c)}</option>`).join('')}${ini.map(c=>`<option value="${esc(c)}" ${c===st.c?'selected':''}>init: ${esc(c)}</option>`).join('')}<option value="*" ${st.c==='*'?'selected':''}>All containers</option></select>`:''}
    <select id="klt">${['100','500','2000','10000'].map(n=>`<option value="${n}" ${n===st.tail?'selected':''}>Last ${n}</option>`).join('')}</select>
    <label class="toggle" title="Logs of the previous (crashed) container"><input type="checkbox" id="klp" ${st.prev?'checked':''}><span></span>Previous</label>
    <label class="toggle"><input type="checkbox" id="klts" ${st.ts?'checked':''}><span></span>Timestamps</label>
    <label class="toggle"><input type="checkbox" id="klf" ${st.follow?'checked':''}><span></span>Follow</label>
    <div class="search"><span>${ic('search',14)}</span><input id="klq" placeholder="Filter lines…" value="${esc(st.q)}"></div>
    <button class="btn sm ghost" id="klcp" title="Copy">${ic('copy',13)}</button><button class="btn sm ghost" id="kldl" title="Download">${ic('download',13)}</button></div>
    <pre class="kpre" id="klout"><span class="spin"></span></pre>`;
  let text='';
  const show=()=>{const o=$('#klout');if(!o)return;const q=st.q.toLowerCase();const atEnd=o.scrollHeight-o.scrollTop-o.clientHeight<60;
    const lines=q?text.split('\n').filter(l=>l.toLowerCase().includes(q)):null;o.textContent=(lines?lines.join('\n'):text)||'(no output)';if(atEnd||!o.dataset.done){o.scrollTop=1e9;o.dataset.done=1}};
  const load=async()=>{if(K.sel!==s||s.tab!=='logs')return;try{const j=await api('/api/k8s_logs',{cid:K.cid,ns:s.ns,name:s.name,container:st.c,tail:+st.tail,previous:st.prev,timestamps:st.ts});
      if(K.sel!==s||s.tab!=='logs')return;text=j.text;show()}catch(e){const o=$('#klout');if(o&&K.sel===s){o.textContent=e.message;clearInterval(K.logT);K.logT=null}}};
  const follow=()=>{clearInterval(K.logT);K.logT=st.follow&&!st.prev?setInterval(()=>{if(!document.hidden)load()},3000):null};
  $('#klc')&&($('#klc').onchange=e=>{st.c=e.target.value;load()});$('#klt').onchange=e=>{st.tail=e.target.value;load()};
  $('#klp').onchange=e=>{st.prev=e.target.checked;load();follow()};$('#klts').onchange=e=>{st.ts=e.target.checked;load()};
  $('#klf').onchange=e=>{st.follow=e.target.checked;follow()};$('#klq').oninput=e=>{st.q=e.target.value;show()};
  $('#klcp').onclick=()=>{navigator.clipboard?.writeText($('#klout').textContent);toast('Copied','ok',1500)};
  $('#kldl').onclick=()=>{const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([text],{type:'text/plain'}));a.download=`${s.name}${st.c&&st.c!=='*'?'-'+st.c:''}.log`;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),5000)};
  load();follow()}

// ---- create from YAML ----
const KTPL={Deployment:`apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-app
spec:
  replicas: 1
  selector:
    matchLabels:
      app: my-app
  template:
    metadata:
      labels:
        app: my-app
    spec:
      containers:
        - name: app
          image: nginx:1.27
          ports:
            - containerPort: 80
`,Service:`apiVersion: v1
kind: Service
metadata:
  name: my-app
spec:
  selector:
    app: my-app
  ports:
    - port: 80
      targetPort: 80
`,ConfigMap:`apiVersion: v1
kind: ConfigMap
metadata:
  name: my-config
data:
  KEY: value
`,Secret:`apiVersion: v1
kind: Secret
metadata:
  name: my-secret
type: Opaque
stringData:
  PASSWORD: change-me
`,Job:`apiVersion: batch/v1
kind: Job
metadata:
  name: my-job
spec:
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: job
          image: busybox:1.36
          command: ["sh", "-c", "echo hello"]
`,CronJob:`apiVersion: batch/v1
kind: CronJob
metadata:
  name: my-cron
spec:
  schedule: "*/15 * * * *"
  jobTemplate:
    spec:
      template:
        spec:
          restartPolicy: OnFailure
          containers:
            - name: job
              image: busybox:1.36
              command: ["sh", "-c", "date"]
`,Ingress:`apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: my-app
spec:
  rules:
    - host: app.example.com
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: my-app
                port:
                  number: 80
`,Namespace:`apiVersion: v1
kind: Namespace
metadata:
  name: my-namespace
`};
function k8Create(){
  modal(`<h3><span class="bigic" style="width:36px;height:36px;border-radius:11px">${ic('plus',18)}</span> Create resource · ${esc(kShort(kCl().name))}</h3>
   <p>Paste YAML (several documents with <code>---</code> are fine) and apply. ${K.ns?`Objects without a namespace go to <b>${esc(K.ns)}</b>.`:'Objects without a namespace go to the default namespace.'}</p>
   <div class="ktpl"><span class="dim small" style="align-self:center">Templates:</span>${Object.keys(KTPL).map(k=>`<button class="btn sm ghost" data-tpl="${k}">${k}</button>`).join('')}</div>
   <div class="kcm" id="kcm"></div><div id="kcerr" class="err" style="margin-top:8px;white-space:pre-wrap"></div>
   <div class="row end" style="margin-top:8px"><button class="btn ghost" id="mno">Cancel</button><button class="btn primary" id="myes">${ic('check',15)} Apply</button></div>`,'xl');
  let ed;if(window.CodeMirror)ed=CodeMirror($('#kcm'),{value:'',mode:'yaml',theme:'material-darker',lineNumbers:true,indentUnit:2,tabSize:2,extraKeys:{Tab:cm=>cm.replaceSelection('  ')}});
  else{ed=document.createElement('textarea');$('#kcm').appendChild(ed)}
  const get=()=>ed.getValue?ed.getValue():ed.value,set=v=>ed.setValue?ed.setValue(v):(ed.value=v);
  $$('#mbox [data-tpl]').forEach(b=>b.onclick=()=>set(KTPL[b.dataset.tpl]));setTimeout(()=>ed.focus&&ed.focus(),50);
  $('#mno').onclick=async()=>{if(get().trim()&&!await choose2('Discard this YAML?','',[['y','Discard','danger']]))return;closeModal()};
  $('#myes').onclick=()=>busy($('#myes'),async()=>{if(!get().trim()){$('#kcerr').textContent='Nothing to apply';return}
    try{const j=await api('/api/k8s_apply',{cid:K.cid,yaml:get(),mode:'apply',ns:K.ns});closeModal();toast(esc(j.output||'applied').replace(/\n/g,'<br>'),'ok',6000);k8Load()}
    catch(e){if($('#kcerr'))$('#kcerr').textContent=e.message}})}

document.addEventListener('keydown',e=>{if(S.view!=='k8s')return;
  const free=$('#modal').classList.contains('hidden')&&$('#modal2').classList.contains('hidden')&&$('#pal').classList.contains('hidden');
  if(e.key==='Escape'&&free&&K.sel&&!document.activeElement.closest?.('.CodeMirror')){e.stopImmediatePropagation();k8CloseD()}
  const typing=/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)||document.activeElement.closest?.('.CodeMirror');
  if(e.key==='/'&&!typing&&free&&$('#k8q')){e.preventDefault();$('#k8q').focus()}},true);
window.addEventListener('beforeunload',e=>{if(kDirty()){e.preventDefault();e.returnValue=''}});
"""
