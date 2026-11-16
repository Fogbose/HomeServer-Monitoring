'use strict';
function chartModel(rows){
  const points=rows.map(r=>({...r,time:Number(r.observed||r.received)*1000})).filter(r=>Number.isFinite(r.time)&&r.time>0).sort((a,b)=>a.time-b.time);
  const start=points[0]?.time,end=points.at(-1)?.time;
  return {points,start,end,position:time=>end>start?(time-start)/(end-start):0.5};
}
function parseLines(value){if(typeof value!=='string')return [];return value.split('\n').filter(Boolean).flatMap(line=>{try{return [JSON.parse(line)];}catch{return [];}});}
if(typeof module!=='undefined')module.exports={chartModel,parseLines};
if(typeof document!=='undefined'){
let token=location.hash.slice(1)||sessionStorage.getItem('monitor-token');
if(token){sessionStorage.setItem('monitor-token',token);history.replaceState(null,'','/');}
const feedback=document.getElementById('feedback');
const $=id=>document.getElementById(id);
const clockFormat=new Intl.DateTimeFormat('fr-BE',{hour:'2-digit',minute:'2-digit',hourCycle:'h23'});
const fullFormat=new Intl.DateTimeFormat('fr-BE',{day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'});
const timezone=Intl.DateTimeFormat().resolvedOptions().timeZone;
const date=value=>Number.isFinite(value)&&value>0?fullFormat.format(new Date(value)):'Non disponible';
let lastRows=[],lastStatus={};
function node(tag,text,classes){const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(classes)el.className=classes;return el;}
function badge(text,level='neutral'){return node('span',text,'badge '+level);}
async function api(path,body){const response=await fetch(path,{method:body?'POST':'GET',headers:{Authorization:'Bearer '+token,...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined});const data=await response.json();if(!response.ok)throw new Error(data.error||'Accès refusé');return data;}
function draw(rows){
 lastRows=rows;const model=chartModel(rows);const c=$('chart');const ratio=devicePixelRatio||1;const width=c.clientWidth;
 c.width=width*ratio;c.height=240*ratio;const x=c.getContext('2d');x.scale(ratio,ratio);const css=getComputedStyle(document.documentElement);
 x.strokeStyle=css.getPropertyValue('--line');x.fillStyle=css.getPropertyValue('--muted');x.font='10px system-ui';
 for(let n=0;n<=100;n+=25){const y=215-n*1.9;x.beginPath();x.moveTo(32,y);x.lineTo(width-5,y);x.stroke();x.fillText(n+'%',0,y+4);}
 for(const [key,color] of [['cpu_percent','--accent'],['memory_percent','--green'],['disk_percent','--amber']]){
  x.strokeStyle=css.getPropertyValue(color);x.lineWidth=2;x.beginPath();let started=false,previousTime=0;
  for(const row of model.points){const value=row[key];if(!Number.isFinite(value)||row.connection!=='online'){started=false;continue;}
   const px=32+model.position(row.time)*(width-40),py=215-value*1.9;
   if(started&&row.time-previousTime<=180000)x.lineTo(px,py);else x.moveTo(px,py);started=true;previousTime=row.time;
  }x.stroke();
 }
 const labels=$('chart-time-labels');labels.replaceChildren();
 if(model.points.length){const count=width<450?3:5;for(let i=0;i<count;i++){const stamp=model.start+(model.end-model.start)*i/(count-1);const span=node('span',clockFormat.format(stamp));span.title=date(stamp);labels.append(span);}
  $('chart-period').textContent=date(model.start)+' → '+date(model.end)+' · '+timezone;
 }else $('chart-period').textContent='Les heures apparaîtront dès la première mesure.';
}
$('chart').addEventListener('pointermove',event=>{const model=chartModel(lastRows);if(!model.points.length)return;const rect=event.currentTarget.getBoundingClientRect();const fraction=Math.max(0,Math.min(1,(event.clientX-rect.left-32)/(rect.width-40)));const stamp=model.start+fraction*(model.end-model.start);const row=model.points.reduce((a,b)=>Math.abs(b.time-stamp)<Math.abs(a.time-stamp)?b:a);$('chart-hover').textContent=date(row.time)+' · CPU '+(row.cpu_percent??'—')+' % · Mémoire '+(row.memory_percent??'—')+' % · Disque '+(row.disk_percent??'—')+' %';});
function renderServices(data,ready){
 const rows=$('service-rows');rows.replaceChildren();const allServices=parseLines(data.containers),stats=parseLines(data.container_stats),filter=$('service-filter').value;const services=allServices.filter(s=>filter==='all'||(filter==='running'?s.State==='running':s.State!=='running'));let running=0;
 const friendly={'homeserver-nextcloud-app-1':'Nextcloud','homeserver-nextcloud-cron-1':'Tâches de fond Nextcloud','homeserver-nextcloud-db-1':'PostgreSQL','homeserver-nextcloud-redis-1':'Redis','homeserver-talk-turn-1':'Relais TURN','homeserver-talk-hpb-hpb-1':'Talk · moteur de communication','homeserver-talk-hpb-tls-1':'Talk · passerelle TLS'};
 for(const service of services){const name=service.Names||service.Name||service.ID;const stat=stats.find(s=>s.Name===name||s.ID===service.ID)||{};
  const state=service.State;const health=service.Status||'';const ok=ready&&state==='running'&&!health.includes('unhealthy');if(ok)running++;
  const tr=node('tr'),cell=node('td',friendly[name]||name);cell.append(node('span',name,'service-secondary'));tr.append(cell);
  const stateCell=node('td');stateCell.append(badge(!ready?'Périmé':ok?(health.includes('healthy')?'Sain':'En cours'):state||'Inconnu',!ready?'neutral':ok?'success':'warning'));tr.append(stateCell,node('td',ready?(stat.CPUPerc||'—'):'—'),node('td',ready?(stat.MemUsage||'—'):'—'),node('td',ready?health:'Dernier état connu'));rows.append(tr);
 }
 if(!services.length){const tr=node('tr');const cell=node('td','Les données de conteneurs ne sont pas disponibles.','empty');cell.colSpan=5;tr.append(cell);rows.append(tr);}
 $('service-count').textContent=allServices.length?(allServices.filter(s=>s.State==='running').length+' actifs · '+allServices.filter(s=>s.State!=='running').length+' arrêtés'):'Données indisponibles';
}
function renderBackups(data){
 const cards=$('backup-cards');cards.replaceChildren();const copy=data.windows_backup||{};
 const definitions=[['Capture serveur',data.capture_epoch?data.capture_epoch*1000:null,!!data.capture_epoch,'Fichiers et base Nextcloud · copie locale'],['Copie externe Windows',Date.parse(copy.CompletedUtc),copy.Result==='Success','Sauvegarde chiffrée · portable Windows']];
 for(const [title,stamp,success,description] of definitions){const recent=success&&Number.isFinite(stamp)&&Date.now()-stamp<=36*3600000;const card=node('article',undefined,'backup-card');const top=node('div',undefined,'backup-top');top.append(node('h3',title),badge(recent?'Récente':stamp?'À vérifier':'Non vérifiée',recent?'success':'warning'));card.append(top,node('p',date(stamp),'backup-time'),node('p',description),node('p','Statut de copie distinct d’une restauration testée.'));cards.append(card);}
}
async function renderTools(){try{const tools=await api('/api/tools');for(const name of ['beszel','olivetin','cockpit']){const link=$(name+'-link'),tool=tools[name];if(!tool)continue;link.querySelector('.integration-state').textContent=tool.status;link.classList.toggle('unavailable',!tool.ready);link.setAttribute('aria-disabled',String(!tool.ready));if(tool.ready&&tool.url)link.href=tool.url;}}catch{}}
async function refresh(){
 try{const data=await api('/api/status');lastStatus=data;const ready=data.connection==='online'&&Date.now()/1000-(data.observed||0)<=180;
  $('subtitle').textContent=(data.hostname||'HomeServer')+' · dernière mesure '+date((data.observed||0)*1000);
  const banner=$('connection');banner.className='banner '+(ready?'online':'unknown');banner.textContent=data.demo?'Démonstration · données factices · actions désactivées':ready?'● Serveur joignable · connexion SSH vérifiée · '+(data.capabilities?.privileged?'collecteur privilégié actif':'collecte limitée'):'État indéterminé · vérifier alimentation, VPN et réseau';
  $('server-name').textContent=data.hostname||'HomeServer';const facts=$('system-facts');facts.replaceChildren();const hours=Math.floor((data.uptime_seconds||0)/3600);
  for(const [title,value] of [['Nextcloud',ready?data.nextcloud?.versionstring||'Inconnu':'Inconnu'],['Disponibilité',ready?(Math.floor(hours/24)+' j '+hours%24+' h'):'Inconnue'],['Charge système',ready?data.load?.[0]?.toFixed(2)||'—':'—'],['Swap',ready&&data.swap_percent!==undefined?data.swap_percent+' %':'—'],['Inodes',ready&&data.inode_percent!==undefined?data.inode_percent+' %':'—']]){const row=node('div');row.append(node('dt',title),node('dd',value));facts.append(row);}
  const cards=$('cards');cards.replaceChildren();
  for(const [name,key,unit,kind,note] of [['Processeur','cpu_percent','%','cpu','Utilisation sur la période mesurée'],['Mémoire','memory_percent','%','memory','Mémoire utilisée, hors cache récupérable'],['Stockage /srv','disk_percent','%','disk','Occupation du système de fichiers'],['Espace disponible','disk_free_gib','Gio','free','Capacité disponible pour les données']]){const card=node('div',undefined,'card '+kind);card.append(node('span',name,'card-label'),node('strong',ready&&Number.isFinite(data[key])?data[key]+' '+unit:'Inconnu'),node('small',note));const meter=node('div',undefined,'meter'),fill=node('span');fill.style.width=Math.max(0,Math.min(100,ready?(kind==='free'?100-(data.disk_percent||0):data[key]||0):0))+'%';meter.append(fill);card.append(meter);cards.append(card);}
  const alerts=$('alerts');alerts.replaceChildren();for(const item of data.alerts||[])alerts.append(node('p',item.text,'alert '+item.level));if(!alerts.children.length)alerts.append(node('p','✓ Aucun point d’attention détecté par les contrôles disponibles.','all-clear'));
  $('alert-count').textContent=(data.alerts||[]).length+' point(s)';$('nav-alert-count').textContent=(data.alerts||[]).length;
  renderServices(data,ready);renderBackups(data);
  const presence=$('power-presence');presence.replaceChildren();
  for(const [name,state,description] of [['HomeServer',ready?(data.power_prepared?'Préparé pour l’arrêt':'Allumé et joignable'):'Indéterminé','En cas de perte réseau : veille, extinction ou panne non distinguables sans observateur indépendant.'],['Ce PC Windows','Console active','La veille reste autorisée. Après suspension ou arrêt du PC, cette console ne peut plus confirmer son propre état.']]){const card=node('div');card.append(node('strong',name),node('p',state,'presence-value'),node('p',description,'muted'));presence.append(card);}
  $('details').textContent=JSON.stringify({units:data.units,nextcloud:data.nextcloud,failed_units:data.failed_units,windows_backup:data.windows_backup,capture_epoch:data.capture_epoch,capabilities:data.capabilities,raid:data.raid},null,2);
  document.querySelectorAll('[data-action]').forEach(b=>{const power=['server-prepare','server-resume','server-check','server-shutdown','server-reboot'].includes(b.dataset.action);b.disabled=!!data.demo||data.actions_enabled!==true||(b.classList.contains('remote')&&(!ready||data.power_version!=='2'))||(power&&data.sessions_version!=='2');});
  $('prepare-state').textContent=data.power_prepared?'Préparation enregistrée : accès suspendus. Reprise automatique prévue à '+date(data.power_resume_at*1000)+'. Vous pouvez reprendre ou demander l’arrêt explicite.':data.actions_enabled!==true?'Actions désactivées par défaut. Configurez et vérifiez votre déploiement avant de les activer.':data.sessions_version==='2'?'Préparation réversible : les services seront repris automatiquement après 15 minutes si l’arrêt n’est pas terminé.':'Actions d’alimentation bloquées : module de reprise non vérifié. Les applications restent disponibles.';
  $('last-update').textContent='Actualisé à '+clockFormat.format(new Date());draw(await api('/api/history'));await renderTools();
 }catch(error){feedback.textContent='Console : '+error.message;document.querySelectorAll('.remote').forEach(b=>b.disabled=true);}
}
window.refresh=refresh;
$('service-filter').addEventListener('change',()=>renderServices(lastStatus,lastStatus.connection==='online'&&Date.now()/1000-(lastStatus.observed||0)<=180));
$('refresh').addEventListener('click',async()=>{try{await api('/api/refresh',{});feedback.textContent='Actualisation demandée. Les données apparaîtront à la fin du contrôle.';setTimeout(refresh,2000);}catch(e){feedback.textContent=e.message;}});
document.querySelectorAll('[data-action]').forEach(button=>button.addEventListener('click',async()=>{const label=button.dataset.confirm;const explanation=button.dataset.action==='server-prepare'?'Nextcloud et Talk seront INDISPONIBLES jusqu’à la reprise ou l’extinction. Les autres sessions seront fermées. Enregistrez les travaux et terminez les appels. Sans extinction, une reprise automatique est prévue après 15 minutes.\n\n':'';const confirmation=prompt(explanation+'Pour confirmer, tapez exactement :\n\n'+label);if(confirmation===null)return;try{const result=await api('/api/action',{action:button.dataset.action,confirmation});feedback.textContent=result.message;}catch(e){feedback.textContent=e.message;}}));
const theme=localStorage.getItem('homeserver-theme');if(theme)document.documentElement.dataset.theme=theme;
$('theme-toggle').addEventListener('click',()=>{const selected=document.documentElement.dataset.theme==='light'?'dark':'light';document.documentElement.dataset.theme=selected;localStorage.setItem('homeserver-theme',selected);draw(lastRows);});
const closeButton=node('button','Fermer la console','quiet');closeButton.addEventListener('click',async()=>{if(!confirm('Arrêter la collecte locale ? Les sessions SSH et outils restent indépendants.'))return;try{await api('/api/quit',{});document.body.replaceChildren(node('p','Console arrêtée. Vous pouvez fermer cet onglet.'));}catch(e){feedback.textContent=e.message;}});document.querySelector('.header-actions').append(closeButton);
document.querySelectorAll('a[aria-disabled]').forEach(a=>a.addEventListener('click',e=>{if(a.getAttribute('aria-disabled')==='true')e.preventDefault();}));
const observer=new IntersectionObserver(entries=>{for(const entry of entries)if(entry.isIntersecting){document.querySelectorAll('nav a').forEach(a=>a.classList.toggle('active',a.getAttribute('href')==='#'+entry.target.id));}},{rootMargin:'-10% 0px -65% 0px'});document.querySelectorAll('section[id],header[id]').forEach(section=>observer.observe(section));
function clock(){$('clock').textContent=clockFormat.format(new Date())+' · '+timezone;}clock();setInterval(clock,1000);refresh();setInterval(refresh,10000);window.addEventListener('resize',()=>draw(lastRows));
}
