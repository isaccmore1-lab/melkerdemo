
const state = {
  meta:null,
  customers:[],
  articles:[],
  customerByNo:new Map(),
  articleByNo:new Map(),
  historyOffset:0,
  historyLimit:100,
  currentPage:"dashboard",
  selectedCustomer:null,
  rowSeq:0,
  currentLabelOrder:null,
  orderDirty:false,
  orderSaved:false,
  suppressDirty:false,
  recommendations:[],
  masterCustomers:[],
  masterArticles:[],
  auth:null,
  permissions:[],
  statsOptions:null,
  pricingTab:"STANDARD"
};

const $ = id => document.getElementById(id);

async function api(url, options={}){
  const res = await fetch(url, options);
  let data=null;
  try{ data=await res.json(); }catch{}
  if(!res.ok){
    if(res.status===401 && !url.startsWith("/api/auth/")) showAuthGate(false);
    throw new Error((data && (data.detail || data.message)) || `${res.status} ${res.statusText}`);
  }
  return data;
}

function esc(v){
  return String(v ?? "").replace(/[&<>"']/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"}[c]));
}
function fmt(v,d=2){
  if(v===null || v===undefined || v==="" || Number.isNaN(Number(v))) return "—";
  return Number(v).toLocaleString("sv-SE",{minimumFractionDigits:d,maximumFractionDigits:d});
}
function money(v){ return `${fmt(v,2)} kr`; }
function fmtDate(v){
  if(!v) return "—";
  const p=String(v).slice(0,10).split("-");
  return p.length===3 ? `${p[2]}/${p[1]}/${p[0]}` : v;
}
function weekdaySv(v){
  if(!v) return "";
  const d=new Date(v+"T12:00:00");
  return ["sön","mån","tis","ons","tors","fre","lör"][d.getDay()];
}
function debounce(fn,ms=250){
  let t; return (...args)=>{clearTimeout(t);t=setTimeout(()=>fn(...args),ms)};
}
function badge(text,kind="gray"){
  return `<span class="badge ${kind}"><span class="dot"></span>${esc(text)}</span>`;
}
function statusKind(s){
  const x=(s||"").toLowerCase();
  if(x.includes("fakturer")||x.includes("ready")||x.includes("inlever")) return "green";
  if(x.includes("fortnox") && !x.includes("ej")) return "blue";
  if(x.includes("orderbekr")) return "blue";
  if(x.includes("ej")||x.includes("ny")||x.includes("okänd")) return "amber";
  return "gray";
}
function showToast(message){
  const t=$("toast"); t.textContent=message; t.classList.add("show");
  clearTimeout(showToast._t); showToast._t=setTimeout(()=>t.classList.remove("show"),3200);
}
function setClock(){
  $("clock").textContent=new Date().toLocaleString("sv-SE",{weekday:"short",day:"numeric",month:"short",hour:"2-digit",minute:"2-digit"});
}
setInterval(setClock,30000);

function goPage(page){
  const pagePerm={dashboard:"dashboard",order:"order_edit",history:"history",customers:"customer_read",articles:"article_read",pricing:"pricing_read",purchasing:"purchasing_read",production:"production_read",transport:"transport_read",labels:"labels",fortnox:"fortnox_read",statistics:"statistics_read",users:"admin"};
  const needed=pagePerm[page];
  if(needed==="admin" && state.auth?.role!=="admin"){showToast("Endast admin har tillgång till sidan.");return;}
  if(needed && needed!=="admin" && !hasPermission(needed)){showToast("Du saknar behörighet till sidan.");return;}
  if(state.currentPage==="order" && page!=="order"){
    if(state.orderDirty && !state.orderSaved){
      const leave=confirm("Ordern innehåller osparade ändringar. Kassera utkastet och lämna Ny order?");
      if(!leave) return;
    }
    resetOrderDraft(false);
  }

  state.currentPage=page;
  document.querySelectorAll(".page").forEach(x=>x.classList.remove("active"));
  document.querySelectorAll(".nav-btn").forEach(x=>x.classList.toggle("active",x.dataset.page===page));
  $(`page-${page}`).classList.add("active");
  const names={dashboard:"Översikt",order:"Ny order",history:"Orderhistorik",customers:"Kunder",articles:"Artiklar",
    pricing:"Prissättning",purchasing:"Inköp",production:"Produktion",transport:"Transport",labels:"Etiketter",fortnox:"Fortnox",statistics:"Statistik",users:"Användare"};
  $("topTitle").textContent=names[page]||"ERP v2";
  window.scrollTo({top:0,behavior:"smooth"});

  if(page==="dashboard") loadDashboard();
  if(page==="history") loadHistory();
  if(page==="customers") loadCustomerMasterPage();
  if(page==="articles") loadArticleMasterPage();
  if(page==="pricing") loadPricingPage();
  if(page==="purchasing") loadPurchasingPage();
  if(page==="production") loadProduction();
  if(page==="transport") loadTransport();
  if(page==="labels") searchLabelOrders();
  if(page==="fortnox") searchFortnoxOrders();
  if(page==="statistics") loadStatisticsPage();
  if(page==="users") loadUsersPage();
}

document.querySelectorAll(".nav-btn").forEach(btn=>btn.addEventListener("click",()=>goPage(btn.dataset.page)));

async function init(){
  setClock();
  await checkAuthentication();
}

async function checkAuthentication(){
  try{
    const a=await api("/api/auth/me");
    if(a.setup_required){ showFirstAdminSetup(); return; }
    if(!a.authenticated){ showAuthGate(false); return; }
    await startAuthenticatedERP(a.user);
  }catch(err){
    showAuthGate(false,"Kunde inte kontrollera inloggning: "+err.message);
  }
}

function authError(message){
  const e=$("authError"); if(!e)return; e.textContent=message||""; e.classList.toggle("show",!!message);
}
function showFirstAdminSetup(){
  $("erpApp").style.display="none"; $("authGate").style.display="grid";
  $("authTitle").textContent="Första uppstart";
  $("authLead").textContent="Skapa det första admin-kontot. Inga standardlösenord skapas automatiskt.";
  $("authBody").innerHTML=`<form class="auth-form" id="firstAdminForm">
    <div class="field"><label>Ditt namn</label><input id="setupName" required autocomplete="name"></div>
    <div class="field"><label>Användarnamn</label><input id="setupUsername" required autocomplete="username"></div>
    <div class="field"><label>E-post (valfritt)</label><input id="setupEmail" type="email"></div>
    <div class="field"><label>Lösenord — minst 8 tecken</label><input id="setupPassword" type="password" required autocomplete="new-password"></div>
    <div class="auth-error" id="authError"></div><button class="btn primary" type="submit">SKAPA ADMIN & LOGGA IN</button></form>`;
  $("firstAdminForm").onsubmit=async e=>{
    e.preventDefault(); authError("");
    try{
      const d=await api("/api/auth/setup",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({username:$("setupUsername").value,display_name:$("setupName").value,email:$("setupEmail").value,password:$("setupPassword").value})});
      await startAuthenticatedERP(d.user);
    }catch(err){authError(err.message)}
  };
}
function showAuthGate(operator=false,message=""){
  $("erpApp").style.display="none"; $("authGate").style.display="grid";
  $("authTitle").textContent="ERP v2 · DEMO";
  $("authLead").textContent=operator?"Syntetisk testmiljö · operatörskonto är förifyllt.":"Syntetisk testmiljö · demo-kontot är förifyllt.";
  $("authBody").innerHTML=`<div class="auth-tabs"><button class="auth-tab ${operator?"":"active"}" id="officeLoginTab" type="button">Kontor</button><button class="auth-tab ${operator?"active":""}" id="operatorLoginTab" type="button">Produktion / PIN</button></div>
    <div class="notice amber" style="margin-bottom:10px"><strong>DEMO</strong> — all kund-, order-, pris- och leverantörsdata är påhittad.</div>
    <form class="auth-form" id="loginForm">
      <div class="field"><label>${operator?"Operatörskod":"Användarnamn"}</label><input id="loginUsername" autocomplete="username" required value="${operator?"pack1":"demo"}"></div>
      <div class="field"><label>${operator?"PIN":"Lösenord"}</label><input id="loginSecret" type="password" inputmode="${operator?"numeric":"text"}" autocomplete="current-password" required value="${operator?"2468":"Demo2026!"}"></div>
      <div class="auth-error ${message?"show":""}" id="authError">${esc(message)}</div>
      <button class="btn primary" type="submit">LOGGA IN</button></form>`;
  $("officeLoginTab").onclick=()=>showAuthGate(false); $("operatorLoginTab").onclick=()=>showAuthGate(true);
  $("loginForm").onsubmit=async e=>{
    e.preventDefault(); authError("");
    const endpoint=operator?"/api/auth/operator-login":"/api/auth/login";
    const payload=operator?{username:$("loginUsername").value,pin:$("loginSecret").value}:{username:$("loginUsername").value,password:$("loginSecret").value};
    try{const d=await api(endpoint,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});await startAuthenticatedERP(d.user)}catch(err){authError(err.message)}
  };
}
async function showLocalRecovery(){
  authError("");
  try{
    const r=await api("/api/auth/local-recovery");
    const offices=r.office_users||[];
    $("authTitle").textContent="Lokal kontoåterställning";
    $("authLead").textContent="Detta fungerar bara på datorn där ERP körs. Order, etiketter och historik påverkas inte.";
    if(offices.length){
      $("authBody").innerHTML=`<form class="auth-form" id="recoveryForm">
        <div class="field"><label>Konto i aktiv databas</label><select id="recoveryUser">${offices.map(u=>`<option value="${u.id}">${esc(u.display_name)} — ${esc(u.username)}${u.role==="admin"?" (Admin)":""}</option>`).join("")}</select></div>
        <div class="field"><label>Nytt lösenord — minst 8 tecken</label><input id="recoveryPassword" type="password" required autocomplete="new-password"></div>
        <div class="small muted">Aktiv databas: ${esc(r.database||"")}</div>
        <div class="auth-error" id="authError"></div>
        <button class="btn primary" type="submit">ÅTERSTÄLL & LOGGA IN</button>
        <button class="btn" type="button" id="backToLogin">TILLBAKA</button></form>`;
      $("backToLogin").onclick=()=>showAuthGate(false);
      $("recoveryForm").onsubmit=async e=>{
        e.preventDefault(); authError("");
        try{
          const d=await api("/api/auth/local-reset",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({user_id:Number($("recoveryUser").value),password:$("recoveryPassword").value})});
          await startAuthenticatedERP(d.user);
        }catch(err){authError(err.message)}
      };
    }else{
      $("authBody").innerHTML=`<form class="auth-form" id="recoveryForm">
        <div class="auth-error show">Den aktiva databasen innehåller inget kontorskonto. Skapa ett nytt lokalt admin-konto utan att radera övrig ERP-data.</div>
        <div class="field"><label>Namn</label><input id="recoveryName" value="Admin" required></div>
        <div class="field"><label>Användarnamn</label><input id="recoveryUsername" value="admin" required></div>
        <div class="field"><label>Nytt lösenord — minst 8 tecken</label><input id="recoveryPassword" type="password" required autocomplete="new-password"></div>
        <div class="small muted">Aktiv databas: ${esc(r.database||"")}</div>
        <button class="btn primary" type="submit">SKAPA ADMIN & LOGGA IN</button>
        <button class="btn" type="button" id="backToLogin">TILLBAKA</button></form>`;
      $("backToLogin").onclick=()=>showAuthGate(false);
      $("recoveryForm").onsubmit=async e=>{
        e.preventDefault();
        try{
          const d=await api("/api/auth/local-reset",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({user_id:null,username:$("recoveryUsername").value,display_name:$("recoveryName").value,password:$("recoveryPassword").value})});
          await startAuthenticatedERP(d.user);
        }catch(err){alert(err.message)}
      };
    }
  }catch(err){ authError("Kunde inte öppna återställning: "+err.message); }
}

function hasPermission(p){return state.permissions.includes("*")||state.permissions.includes(p)}
function applyPermissions(){
  document.querySelectorAll("[data-permission]").forEach(el=>{
    const p=el.dataset.permission; const allowed=p==="admin"?state.auth?.role==="admin":hasPermission(p); el.style.display=allowed?"":"none";
  });
}
async function startAuthenticatedERP(user){
  state.auth=user; state.permissions=user.permissions||[];
  $("authGate").style.display="none"; $("erpApp").style.display="grid";
  $("currentUserName").textContent=user.display_name; $("currentUserRole").textContent=user.role_label;
  $("userAvatar").textContent=(user.display_name||"?").split(/\s+/).slice(0,2).map(x=>x[0]).join("").toUpperCase();
  applyPermissions();
  try{
    const [meta,customers,articles]=await Promise.all([api("/api/meta"),api("/api/customers?limit=1000"),api("/api/articles?limit=2000")]);
    state.meta=meta; state.customers=customers; state.articles=articles;
    $("topSubtitle").textContent=meta.version||"ERP v2";
    state.customerByNo.clear(); state.articleByNo.clear();
    customers.forEach(c=>state.customerByNo.set(Number(c.customer_no),c)); articles.forEach(a=>state.articleByNo.set(Number(a.article_no),a));
    $("sideMeta").innerHTML=`<strong>${fmt(meta.counts.historical_orders,0)}</strong> historiska order<br>${fmt(meta.counts.customers,0)} kunder · ${fmt(meta.counts.articles,0)} artiklar<br>Excel snapshot ${esc(meta.workbook_snapshot)}<br><span class="muted">${esc(meta.source_workbook||"")}</span>`;
    $("dbStatus").textContent="● Databas OK";
    setupOrderEditor(); setupSearchBindings(); await loadDashboard();
  }catch(err){$("dbStatus").textContent="● Databasfel"; showToast("Kunde inte starta ERP v2: "+err.message); console.error(err)}
}
function toggleUserMenu(){$("userMenu").classList.toggle("open")}
async function logoutERP(){try{await api("/api/auth/logout",{method:"POST"})}catch{} state.auth=null;state.permissions=[];$("userMenu").classList.remove("open");showAuthGate(false)}

// ---------------- Dashboard ----------------
async function loadDashboard(){
  try{
    const d=await api("/api/dashboard");
    $("dashboardKpis").innerHTML=`
      <div class="kpi"><div class="kpi-label">Leveranser idag</div><div class="kpi-value">${fmt(d.today.orders,0)}</div><div class="kpi-sub">${fmt(d.today.kg,0)} kg · Excel + ERP v2</div></div>
      <div class="kpi"><div class="kpi-label">Denna vecka</div><div class="kpi-value">${fmt(d.week.orders,0)}</div><div class="kpi-sub">${fmt(d.week.kg,0)} kg · ${fmtDate(d.week.start)} – ${fmtDate(d.week.end)}</div></div>
      <div class="kpi"><div class="kpi-label">Historiska order</div><div class="kpi-value">${fmt(d.counts.history,0)}</div><div class="kpi-sub">Importerade från sparade</div></div>
      <div class="kpi"><div class="kpi-label">Masterdata</div><div class="kpi-value">${fmt(d.counts.articles,0)}</div><div class="kpi-sub">${fmt(d.counts.customers,0)} kunder · senaste legacy ${fmtDate(d.latest_legacy_date)}</div></div>`;

    $("recentOrders").innerHTML=orderTableHtml(d.recent_orders,true);

    $("activityFeed").innerHTML=d.activity.length?d.activity.map(a=>`
      <div class="activity">
        <div class="activity-icon">${a.event_type==="ORDER_READY"?"✓":a.event_type==="ORDER_CREATED"?"+":"•"}</div>
        <div><div class="strong">${a.order_no?`#${esc(a.order_no)} · `:""}${esc(a.customer_name||"System")}</div>
        <div class="small">${esc(a.message)}</div><div class="time">${esc(a.created_at)}</div></div>
      </div>`).join(""):`<div class="empty">Inga händelser ännu.</div>`;

    const max=Math.max(1,...d.volume.map(x=>Number(x.weight||0)));
    $("volumeBars").innerHTML=d.volume.length?d.volume.map(x=>`
      <div class="volume-row"><div title="${esc(x.type)}">${esc(x.type)}</div>
      <div class="bar"><span style="width:${Math.max(2,Number(x.weight||0)/max*100)}%"></span></div>
      <div class="t-right strong">${fmt(x.weight/1000,1)} t</div></div>`).join(""):`<div class="empty">Ingen volymdata.</div>`;
  }catch(err){ console.error(err); }
}

function orderTableHtml(rows, compact=false){
  if(!rows || !rows.length) return `<div class="empty">Inga order.</div>`;
  return `<table><thead><tr><th>Order</th><th>Kund</th><th>Leverans</th><th>Status</th><th class="t-right">Kg</th><th class="t-right">Värde</th></tr></thead>
  <tbody>${rows.map(o=>`<tr class="clickable" onclick="openOrder(${o.id})">
    <td><strong>${esc(o.order_no)}</strong><div class="small muted">${o.source==="legacy"?"Excel":"ERP v2"}</div></td>
    <td>${esc(o.customer_name||"")}<div class="small muted">${esc(o.customer_no||"")}</div></td>
    <td>${fmtDate(o.delivery_date)} <span class="small muted">${weekdaySv(o.delivery_date)}</span></td>
    <td>${badge(o.status,statusKind(o.status))}</td>
    <td class="t-right">${fmt(o.total_kg,0)}</td><td class="t-right">${money(o.total_value||0)}</td>
  </tr>`).join("")}</tbody></table>`;
}

// ---------------- Order editor ----------------
function updateDraftBadge(){
  const badge=$("draftStateBadge");
  if(!badge) return;
  badge.classList.remove("draft-dirty","draft-saved","gray");
  if(state.orderDirty){
    badge.textContent="● Osparad";
    badge.classList.add("draft-dirty");
  }else if(state.orderSaved){
    badge.textContent="✓ Sparad";
    badge.classList.add("draft-saved");
  }else{
    badge.textContent="Ny order";
    badge.classList.add("gray");
  }
}

function markOrderDirty(){
  if(state.suppressDirty) return;
  state.orderDirty=true;
  state.orderSaved=false;
  updateDraftBadge();
}

function setupOrderEditor(){
  setupCustomerCombo();

  ["orderDate","deliveryDate","orderNo","internalReference","customerReference","orderStatus","orderInfo"].forEach(id=>{
    const node=$(id);
    node.addEventListener(id==="orderInfo"?"input":"change",()=>{
      markOrderDirty();
      if(id==="deliveryDate"){
        recalcAllRows();
        if(state.selectedCustomer) loadCustomerRecommendations(state.selectedCustomer.customer_no);
      }
    });
    if(id!=="orderInfo") node.addEventListener("input",markOrderDirty);
  });

  resetOrderDraft(false);
}

function resetOrderDraft(ask=true){
  if(ask && state.orderDirty && !confirm("Rensa hela den osparade ordern?")) return false;

  state.suppressDirty=true;
  state.selectedCustomer=null;
  state.recommendations=[];
  state.rowSeq=0;

  const today=new Date();
  const tomorrow=new Date(today); tomorrow.setDate(today.getDate()+1);
  $("orderCustomerInput").value="";
  $("orderCustomerNo").value="";
  $("customerMenu").classList.remove("open");
  $("orderDate").value=today.toISOString().slice(0,10);
  $("deliveryDate").value=tomorrow.toISOString().slice(0,10);
  $("orderNo").value=state.meta?.next_order_no || "";
  $("draftOrderNumber").textContent=`Order ${state.meta?.next_order_no || "—"}`;
  $("internalReference").value="Isac";
  $("customerReference").value="";
  $("orderStatus").value="Ny";
  $("orderInfo").value="";
  $("orderLines").innerHTML="";
  $("customerInfo").innerHTML="Välj kund för att se standardinfo.";
  $("customerRecommendations").innerHTML='<div class="small muted">Välj kund så föreslår ERP artiklar som kunden brukar köpa.</div>';
  addOrderRow(false);
  recalcDraftTotals();

  state.orderDirty=false;
  state.orderSaved=false;
  state.suppressDirty=false;
  updateDraftBadge();
  return true;
}

function clearOrderDraft(ask=true){
  const ok=resetOrderDraft(ask);
  if(ok){
    showToast("Orderutkastet är rensat.");
    $("orderCustomerInput").focus();
  }
}

function setupCustomerCombo(){
  const input=$("orderCustomerInput"), menu=$("customerMenu");

  const matches=()=>{
    const q=input.value.trim().toLowerCase();
    return state.customers.filter(c=>
      !q || String(c.customer_no).includes(q) || c.name.toLowerCase().includes(q)
    ).slice(0,30);
  };

  const render=()=>{
    const list=matches();
    menu.innerHTML=list.map(c=>`<div class="combo-item" data-customer="${c.customer_no}">
      <strong>${c.customer_no} — ${esc(c.name)}</strong><span>${esc(c.address||"")} ${esc(c.postal_address||"")}</span></div>`).join("");
    menu.classList.toggle("open",list.length>0 && document.activeElement===input);
    menu.querySelectorAll(".combo-item").forEach(it=>it.addEventListener("mousedown",e=>{
      e.preventDefault(); selectCustomer(Number(it.dataset.customer),true);
    }));
  };

  input.addEventListener("focus",render);
  input.addEventListener("input",()=>{
    clearManualPriceOverrides();
    $("orderCustomerNo").value="";
    state.selectedCustomer=null;
    $("customerReference").value="";
    markOrderDirty();
    render();
    $("customerInfo").innerHTML="Välj kund för att se standardinfo.";
    $("customerRecommendations").innerHTML='<div class="small muted">Välj kund så föreslår ERP artiklar som kunden brukar köpa.</div>';
    recalcAllRows();
  });
  input.addEventListener("keydown",e=>{
    if(e.key!=="Enter" && e.key!=="Tab") return;
    if(e.shiftKey && e.key==="Tab") return;
    if($("orderCustomerNo").value) return;
    const list=matches();
    if(!list.length) return;
    e.preventDefault();
    selectCustomer(list[0].customer_no,false);
    $("orderDate").focus();
  });
  input.addEventListener("blur",()=>setTimeout(()=>menu.classList.remove("open"),120));
}

function clearManualPriceOverrides(){
  $("orderLines")?.querySelectorAll(".order-line").forEach(row=>{row.dataset.manualPrice="";row.dataset.manualReason="";});
}

function selectCustomer(no,focusNext=false){
  const c=state.customerByNo.get(Number(no));
  if(!c) return;
  const previousNo=Number($("orderCustomerNo").value||0);
  if(previousNo && previousNo!==Number(c.customer_no)) clearManualPriceOverrides();
  state.selectedCustomer=c;
  $("orderCustomerNo").value=c.customer_no;
  $("orderCustomerInput").value=`${c.customer_no} — ${c.name}`;
  $("customerMenu").classList.remove("open");
  if(!$("customerReference").value && c.default_reference) $("customerReference").value=c.default_reference;
  $("customerInfo").innerHTML=`<strong>${esc(c.name)}</strong><br>
    ${esc(c.address||"")} ${esc(c.postal_address||"")}<br>
    Åkeri: <strong>${esc(c.carrier||"—")}</strong> · Pall: <strong>${c.pallet_system===1?"SRS":c.pallet_system===2?"EUR":"—"}</strong>
    ${c.label_name?`<br>Etikettnamn: <strong>${esc(c.label_name)}</strong>`:""}`;
  markOrderDirty();
  loadCustomerRecommendations(c.customer_no);
  recalcAllRows();
  if(focusNext) $("orderDate").focus();
}

async function loadCustomerRecommendations(customerNo){
  const box=$("customerRecommendations");
  box.innerHTML='<div class="small muted">Analyserar kundens köphistorik…</div>';
  try{
    const p=new URLSearchParams({limit:8,pricing_date:$("deliveryDate").value});
    const list=await api(`/api/customer-recommendations/${customerNo}?${p}`);
    if(Number($("orderCustomerNo").value)!==Number(customerNo)) return;
    state.recommendations=list;
    box.innerHTML=list.length?`<div class="recommendation-list">${list.map(r=>`
      <div class="recommendation">
        <div>
          <div class="rec-title">${r.article_no} — ${esc(r.name)}</div>
          <div class="rec-meta">${fmt(r.order_count,0)} order · senast ${fmtDate(r.last_date)}${r.current_price!==null?` · ${fmt(r.current_price,2)} kr`:" · pris saknas"}</div>
        </div>
        <button class="rec-add" title="Lägg till artikel" onclick="addRecommendedArticle(${r.article_no})">＋</button>
      </div>`).join("")}</div>`:
      '<div class="small muted">Ingen tillräcklig orderhistorik för kunden ännu.</div>';
  }catch(err){
    box.innerHTML='<div class="small muted">Kunde inte läsa rekommendationer.</div>';
    console.error(err);
  }
}

function addRecommendedArticle(articleNo){
  const rows=[...$("orderLines").querySelectorAll(".order-line")];
  const existing=rows.find(r=>Number(r.querySelector(".article-no").value)===Number(articleNo));
  if(existing){
    const qty=existing.querySelector(".qty");
    qty.focus(); qty.select();
    showToast("Artikeln finns redan på ordern.");
    return;
  }
  let row=rows.find(rowIsBlank);
  if(!row) row=addOrderRow(false);
  selectArticle(row,articleNo,true);
}

function addOrderRow(focus=false){
  state.rowSeq++;
  const id=state.rowSeq;
  const row=document.createElement("div");
  row.className="order-line";
  row.dataset.rowId=id;
  row.innerHTML=`
    <div class="article-cell">
      <input class="article-search" type="text" autocomplete="off" placeholder="Artikel…">
      <div class="combo-menu article-menu"></div>
      <input class="article-no" type="hidden">
    </div>
    <div class="cell article-name"></div>
    <input class="qty" type="number" min="0" step="any" placeholder="Antal">
    <div class="cell price"></div><div class="cell kg"></div><div class="cell pallets"></div>
    <div class="cell ppl"></div><div class="cell goods"></div><div class="cell station t-center"></div>
    <button class="remove-line" type="button" title="Ta bort rad">×</button>`;
  $("orderLines").appendChild(row);
  wireOrderRow(row);
  if(focus) row.querySelector(".article-search").focus();
  return row;
}

function rowIsBlank(row){
  return !row.querySelector(".article-no").value && !row.querySelector(".qty").value;
}

function ensureTrailingRow(){
  const rows=[...$("orderLines").querySelectorAll(".order-line")];
  if(!rows.length){ addOrderRow(); return; }
  const last=rows[rows.length-1];
  if(!rowIsBlank(last)) addOrderRow();
}

function findArticleMatch(text){
  const q=String(text||"").trim().toLowerCase();
  if(!q) return null;
  return state.articles.find(x=>String(x.article_no)===q) ||
    state.articles.find(x=>x.name.toLowerCase()===q) ||
    state.articles.find(x=>String(x.article_no).startsWith(q)) ||
    state.articles.find(x=>x.name.toLowerCase().includes(q)) || null;
}

function wireOrderRow(row){
  const search=row.querySelector(".article-search");
  const menu=row.querySelector(".article-menu");
  const qty=row.querySelector(".qty");
  const remove=row.querySelector(".remove-line");

  const renderMenu=()=>{
    const q=search.value.trim().toLowerCase();
    const list=state.articles.filter(a=>!q || String(a.article_no).includes(q) || a.name.toLowerCase().includes(q)).slice(0,35);
    menu.innerHTML=list.map(a=>`<div class="combo-item" data-art="${a.article_no}">
      <strong>${a.article_no} — ${esc(a.name)}</strong><span>S${a.station_id||"—"} · ${fmt(a.kg_per_order_unit,2)} kg / orderenhet</span>
    </div>`).join("");
    menu.classList.toggle("open",list.length>0 && document.activeElement===search);
    menu.querySelectorAll(".combo-item").forEach(it=>it.addEventListener("mousedown",e=>{
      e.preventDefault(); selectArticle(row,Number(it.dataset.art),true);
    }));
  };

  search.addEventListener("focus",renderMenu);
  search.addEventListener("input",()=>{
    row.querySelector(".article-no").value="";
    row.dataset.calc="";
    clearRowCalc(row);
    markOrderDirty();
    renderMenu();
  });
  search.addEventListener("blur",()=>setTimeout(()=>menu.classList.remove("open"),120));
  search.addEventListener("keydown",e=>{
    if(e.key!=="Enter" && e.key!=="Tab") return;
    if(e.key==="Tab" && e.shiftKey) return;
    if(row.querySelector(".article-no").value) return;
    const a=findArticleMatch(search.value);
    if(!a) return;
    e.preventDefault();
    selectArticle(row,a.article_no,true);
  });

  qty.addEventListener("input",()=>{markOrderDirty();calcOrderRow(row)});
  qty.addEventListener("blur",()=>calcOrderRow(row));
  qty.addEventListener("keydown",async e=>{
    if(e.key==="Tab" && e.shiftKey){
      e.preventDefault(); search.focus(); search.select(); return;
    }
    if(e.key!=="Enter" && e.key!=="Tab") return;
    e.preventDefault();
    await calcOrderRow(row);
    ensureTrailingRow();
    const rows=[...$("orderLines").querySelectorAll(".order-line")];
    const idx=rows.indexOf(row);
    const next=rows[idx+1];
    if(next) next.querySelector(".article-search").focus();
  });

  remove.addEventListener("click",()=>{
    row.remove();
    markOrderDirty();
    ensureTrailingRow();
    recalcDraftTotals();
  });
}

async function selectArticle(row,no,focusQty=true){
  const a=state.articleByNo.get(Number(no)); if(!a) return;
  row.querySelector(".article-no").value=a.article_no;
  row.querySelector(".article-search").value=String(a.article_no);
  row.querySelector(".article-name").textContent=a.name;
  row.querySelector(".article-menu").classList.remove("open");
  markOrderDirty();
  ensureTrailingRow();
  if(focusQty) row.querySelector(".qty").focus();
  if(row.querySelector(".qty").value) await calcOrderRow(row);
}

function clearRowCalc(row){
  row.querySelector(".article-name").textContent="";
  ["price","kg","pallets","ppl","goods","station"].forEach(c=>{
    row.querySelector("."+c).textContent="";
    row.querySelector("."+c).classList.remove("price-missing","price-manual");
  });
  row.dataset.calc="";
  row.dataset.manualPrice=""; row.dataset.manualReason="";
  recalcDraftTotals();
}

async function calcOrderRow(row){
  const art=Number(row.querySelector(".article-no").value);
  const qty=Number(row.querySelector(".qty").value);
  const customer=Number($("orderCustomerNo").value);
  if(!art || !qty || qty<=0){
    row.dataset.calc="";
    recalcDraftTotals(); return;
  }
  const a=state.articleByNo.get(art);
  if(a) row.querySelector(".article-name").textContent=a.name;
  if(!customer){
    row.querySelector(".price").textContent="Välj kund";
    row.querySelector(".price").classList.add("price-missing");
    return;
  }
  try{
    const p=new URLSearchParams({customer_no:customer,article_no:art,qty,pricing_date:$("deliveryDate").value});
    if(row.dataset.manualPrice){ p.set("manual_price",row.dataset.manualPrice); p.set("manual_reason",row.dataset.manualReason||""); }
    const x=await api("/api/calculate-line?"+p);
    row.dataset.calc=JSON.stringify(x);
    row.querySelector(".article-name").textContent=x.name;
    const priceCell=row.querySelector(".price");
    priceCell.innerHTML=x.unit_price===null?"<strong>PRIS SAKNAS</strong>":`<strong>${fmt(x.unit_price,2)}</strong><small>${esc(x.price_source||"")}</small>`;
    priceCell.classList.toggle("price-missing",x.unit_price===null);
    priceCell.classList.toggle("price-manual",x.price_source==="Manuell");
    priceCell.title=x.unit_price===null?"Inget giltigt prisvillkor hittades":`${x.price_source||"Pris"}${x.price_condition_name?" · "+x.price_condition_name:""}${x.price_valid_from||x.price_valid_to?` · ${fmtDate(x.price_valid_from)}–${fmtDate(x.price_valid_to)}`:""}. Klicka för manuellt orderpris.`;
    priceCell.onclick=()=>openLinePriceOverride(row.dataset.rowId);
    row.querySelector(".kg").textContent=fmt(x.kg,0);
    row.querySelector(".pallets").textContent=fmt(x.pallets,2);
    row.querySelector(".ppl").textContent=fmt(x.ppl,2);
    row.querySelector(".goods").textContent=x.goods_value===null?"—":fmt(x.goods_value,2);
    row.querySelector(".station").textContent=x.station_id?`S${x.station_id}`:"—";
    recalcDraftTotals();
  }catch(err){
    row.querySelector(".price").textContent="FEL";
    row.querySelector(".price").classList.add("price-missing");
    console.error(err);
  }
}

async function recalcAllRows(){
  for(const row of $("orderLines").querySelectorAll(".order-line")){
    if(row.querySelector(".article-no").value && row.querySelector(".qty").value) await calcOrderRow(row);
  }
}

function recalcDraftTotals(){
  let goods=0,dep=0,kg=0,pall=0,ppl=0;
  const mats=new Map();
  $("orderLines").querySelectorAll(".order-line").forEach(row=>{
    if(!row.dataset.calc) return;
    const x=JSON.parse(row.dataset.calc);
    goods+=Number(x.goods_value||0); dep+=Number(x.deposit_amount||0); kg+=Number(x.kg||0);
    pall+=Number(x.pallets||0); ppl+=Number(x.ppl||0);
    (x.material_usage||[]).forEach(m=>{
      const k=m.code; const old=mats.get(k)||{...m,quantity:0}; old.quantity+=Number(m.quantity||0); mats.set(k,old);
    });
  });
  $("sumGoods").textContent=money(goods); $("sumDeposit").textContent=money(dep); $("sumKg").textContent=fmt(kg,0)+" kg";
  $("sumPallets").textContent=fmt(pall,2); $("sumPpl").textContent=fmt(ppl,2); $("sumTotal").textContent=money(goods+dep);
  const list=[...mats.values()].filter(x=>Math.abs(x.quantity)>1e-9).sort((a,b)=>a.category.localeCompare(b.category)||a.name.localeCompare(b.name));
  $("draftMaterials").innerHTML=list.length?`<table><tbody>${list.slice(0,30).map(m=>`<tr><td>${esc(m.name)}<div class="small muted">${esc(m.category)}</div></td><td class="t-right strong">${fmt(m.quantity,2)}</td></tr>`).join("")}</tbody></table>
    ${list.length>30?`<div class="small muted" style="margin-top:8px">+ ${list.length-30} fler material</div>`:""}`:
    `<div class="small muted">Beräknas från artikel-BOM när rader fylls i.</div>`;
}

async function saveOrder(){
  const customer=Number($("orderCustomerNo").value);
  if(!customer){ showToast("Välj kund först."); $("orderCustomerInput").focus(); return; }
  const lines=[];
  let missingPrice=false;
  $("orderLines").querySelectorAll(".order-line").forEach(row=>{
    const art=Number(row.querySelector(".article-no").value), qty=Number(row.querySelector(".qty").value);
    if(art && qty>0){
      lines.push({article_no:art,qty,manual_price:row.dataset.manualPrice?Number(row.dataset.manualPrice):null,manual_reason:row.dataset.manualReason||""});
      if(!row.dataset.calc || JSON.parse(row.dataset.calc).unit_price===null) missingPrice=true;
    }
  });
  if(!lines.length){ showToast("Lägg till minst en artikel."); return; }
  if(missingPrice){ showToast("Ordern kan inte sparas: minst en artikel saknar giltigt pris."); return; }

  const payload={
    order_no:Number($("orderNo").value)||null,customer_no:customer,order_date:$("orderDate").value,
    delivery_date:$("deliveryDate").value,internal_reference:$("internalReference").value,
    customer_reference:$("customerReference").value,info:$("orderInfo").value,status:$("orderStatus").value,lines
  };
  const btn=$("saveOrderBtn"); btn.disabled=true; btn.textContent="SPARAR…";
  try{
    const x=await api("/api/orders",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
    state.meta.next_order_no=x.order_no+1;
    state.orderDirty=false; state.orderSaved=true; updateDraftBadge();
    showToast(`Order ${x.order_no} sparad. Ny order är redo.`);
    resetOrderDraft(false);
    await loadDashboard();
  }catch(err){ showToast("Kunde inte spara: "+err.message); }
  finally{ btn.disabled=false; btn.textContent="SPARA ORDER"; }
}



function openLinePriceOverride(rowId){
  const row=[...$("orderLines").querySelectorAll(".order-line")].find(r=>String(r.dataset.rowId)===String(rowId));
  if(!row || !row.dataset.calc) return;
  const x=JSON.parse(row.dataset.calc);
  const current=row.dataset.manualPrice || x.unit_price || "";
  openModal("Manuellt orderpris",`${x.article_no} — ${esc(x.name)}`,`<div class="form-grid two">
    <div class="field"><label>Pris</label><input id="manualLinePrice" type="number" min="0.01" step="0.01" value="${esc(current)}"></div>
    <div class="field"><label>Prisdatum</label><input value="${esc($("deliveryDate").value)}" disabled></div>
    <div class="field span2"><label>Anledning</label><input id="manualLineReason" value="${esc(row.dataset.manualReason||"")}" placeholder="T.ex. avtalat med kund"></div>
    <div class="span2 notice blue">Ordinarie prisbestämning: <strong>${x.price_source||"Pris saknas"}</strong>${x.price_condition_name?` · ${esc(x.price_condition_name)}`:""}. Ett manuellt pris sparas som snapshot på just denna orderrad.</div>
    <div class="modal-actions span2"><button class="btn danger" type="button" onclick="clearLinePriceOverride('${rowId}')">Återgå till prisvillkor</button><button class="btn primary" type="button" onclick="saveLinePriceOverride('${rowId}')">ANVÄND MANUELLT PRIS</button></div>
  </div>`);
}
async function saveLinePriceOverride(rowId){
  const row=[...$("orderLines").querySelectorAll(".order-line")].find(r=>String(r.dataset.rowId)===String(rowId));
  if(!row)return;
  const price=Number($("manualLinePrice").value), reason=$("manualLineReason").value.trim();
  if(!price || price<=0){showToast("Ange ett pris större än 0.");return;}
  if(!reason){showToast("Ange varför priset ändras manuellt.");return;}
  row.dataset.manualPrice=String(price); row.dataset.manualReason=reason; closeModal(); markOrderDirty(); await calcOrderRow(row);
}
async function clearLinePriceOverride(rowId){
  const row=[...$("orderLines").querySelectorAll(".order-line")].find(r=>String(r.dataset.rowId)===String(rowId));
  if(!row)return;
  row.dataset.manualPrice=""; row.dataset.manualReason=""; closeModal(); markOrderDirty(); await calcOrderRow(row);
}

// ---------------- Pricing ----------------
function setPricingTab(tab){
  state.pricingTab=tab;
  document.querySelectorAll(".pricing-tab").forEach(b=>b.classList.toggle("active",b.dataset.pricingTab===tab));
  $("pricingToolbar").style.display=tab==="EVENTS"?"none":"flex";
  const articleMode=tab==="ARTICLE";
  if($("pricingSearch")){
    $("pricingSearch").placeholder=articleMode?"Sök artikel, t.ex. 120…":"Sök kund, artikel eller kampanj…";
  }
  if($("pricingStatus")) $("pricingStatus").style.display=articleMode?"none":"";
  if($("pricingDate")){
    $("pricingDate").style.display=articleMode?"":"none";
    if(articleMode && !$("pricingDate").value) $("pricingDate").value=new Date().toISOString().slice(0,10);
  }
  loadPricingPage(false);
}
async function loadPricingPage(refreshSummary=true){
  try{
    if(refreshSummary){
      const s=await api("/api/pricing/summary");
      $("pricingKpis").innerHTML=`<div class="kpi"><div class="kpi-label">Kundpriser</div><div class="kpi-value">${fmt(s.standard_prices,0)}</div><div class="kpi-sub">giltiga prisposter</div></div>
        <div class="kpi"><div class="kpi-label">Aktiva kampanjer</div><div class="kpi-value">${fmt(s.active_campaigns,0)}</div><div class="kpi-sub">idag ${fmtDate(s.today)}</div></div>
        <div class="kpi"><div class="kpi-label">Kommande kampanjer</div><div class="kpi-value">${fmt(s.future_campaigns,0)}</div><div class="kpi-sub">startar senare</div></div>
        <div class="kpi"><div class="kpi-label">Kunder med pris</div><div class="kpi-value">${fmt(s.customers_priced,0)}</div><div class="kpi-sub">standardvillkor</div></div>`;
    }
    if(state.pricingTab==="EVENTS"){
      const events=await api("/api/pricing/events?limit=250");
      $("pricingTable").innerHTML=events.length?`<table><thead><tr><th>Tid</th><th>Händelse</th><th>Kund</th><th>Artikel</th><th>Pris</th><th>Av</th></tr></thead><tbody>${events.map(e=>`<tr><td>${esc(e.created_at)}</td><td><strong>${esc(e.action)}</strong><div class="small muted">${esc(e.message)}</div></td><td>${e.customer_no} · ${esc(e.customer_name||"")}</td><td>${e.article_no} · ${esc(e.article_name||"")}</td><td>${e.old_price!==null?fmt(e.old_price,2)+" → ":""}${e.new_price!==null?fmt(e.new_price,2):"—"}</td><td>${esc(e.actor||"System")}</td></tr>`).join("")}</tbody></table>`:`<div class="empty">Ingen prislogg ännu. Importerade Excel-priser är startdata; nya ERP-ändringar loggas här.</div>`;
      return;
    }
    if(state.pricingTab==="ARTICLE"){
      const term=$("pricingSearch")?.value.trim()||"";
      if(!term){
        $("pricingTable").innerHTML=`<div class="empty"><strong>Pris per artikel</strong><br>Sök på ett artikelnummer, t.ex. <strong>120</strong>, för att se aktuellt pris för alla kunder som har ett pris på artikeln.</div>`;
        return;
      }
      const p=new URLSearchParams({search:term,pricing_date:$("pricingDate")?.value||"",limit:2000});
      const rows=await api("/api/pricing/article-overview?"+p);
      $("pricingTable").innerHTML=rows.length?`<table><thead><tr><th>Artikel</th><th>Kund</th><th>Ord. kundpris</th><th>Aktuellt pris</th><th>Prisorsak</th><th>Giltighet</th><th></th></tr></thead><tbody>${rows.map(r=>`<tr>
        <td><strong>${r.article_no}</strong> · ${esc(r.article_name)}</td>
        <td><strong>${r.customer_no}</strong> · ${esc(r.customer_name)}</td>
        <td>${r.standard_price!==null?`<strong>${fmt(r.standard_price,2)} kr</strong>`:"—"}</td>
        <td class="strong">${fmt(r.current_price,2)} kr</td>
        <td>${r.price_source==="Kampanj"?badge("Kampanj","green"):badge("Kundpris","blue")}${r.condition_name&&r.price_source==="Kampanj"?`<div class="small muted">${esc(r.condition_name)}</div>`:""}</td>
        <td>${r.valid_from||r.valid_to?`${fmtDate(r.valid_from)} – ${fmtDate(r.valid_to)}`:"Tills vidare"}<div class="small muted">Prisdatum ${fmtDate(r.pricing_date)}</div></td>
        <td class="t-right">${hasPermission("pricing_edit")&&r.condition_id?`<button class="btn ghost" onclick="openPricingConditionEditor(${r.condition_id})">Redigera</button>`:""}</td>
      </tr>`).join("")}</tbody></table>`:`<div class="empty">Ingen kund har ett giltigt pris för den artikeln på valt prisdatum.</div>`;
      return;
    }
    const p=new URLSearchParams({condition_type:state.pricingTab,search:$("pricingSearch")?.value||"",status:$("pricingStatus")?.value||"all",limit:1000});
    const rows=await api("/api/pricing/conditions?"+p);
    const isCampaign=state.pricingTab==="CAMPAIGN";
    $("pricingTable").innerHTML=rows.length?`<table><thead><tr><th>Kund</th><th>Artikel</th><th>Pris</th><th>Giltighet</th><th>Status</th><th>Källa</th><th></th></tr></thead><tbody>${rows.map(r=>`<tr>
      <td><strong>${r.customer_no}</strong> · ${esc(r.customer_name)}</td><td><strong>${r.article_no}</strong> · ${esc(r.article_name)}</td>
      <td class="strong">${fmt(r.price,2)} kr${isCampaign&&r.name?`<div class="small muted">${esc(r.name)}</div>`:""}</td>
      <td>${r.valid_from||r.valid_to?`${fmtDate(r.valid_from)} – ${fmtDate(r.valid_to)}`:"Tills vidare"}</td>
      <td>${pricingStatusBadge(r.status)}</td><td><span class="small">${r.source==="excel"?"Excel-import":"ERP v2"}</span></td>
      <td class="t-right">${hasPermission("pricing_edit")?`<button class="btn ghost" onclick="openPricingConditionEditor(${r.id})">Redigera</button>`:""}</td></tr>`).join("")}</tbody></table>`:`<div class="empty">Inga prisvillkor matchar filtret.</div>`;
  }catch(err){$("pricingTable").innerHTML=`<div class="empty">${esc(err.message)}</div>`}
}
function pricingStatusBadge(s){
  if(s==="current")return badge("Aktiv","green"); if(s==="future")return badge("Kommande","blue"); if(s==="expired")return badge("Utgången","gray"); return badge("Arkiverad","amber");
}
async function openPricingConditionEditor(id=null){
  if(!hasPermission("pricing_edit")){showToast("Du saknar behörighet att ändra priser.");return;}
  let r=null; if(id) r=await api(`/api/pricing/conditions/${id}`);
  const type=r?.condition_type || (state.pricingTab==="CAMPAIGN"?"CAMPAIGN":"STANDARD");
  const customerOptions=state.customers.map(c=>`<option value="${c.customer_no}" ${Number(r?.customer_no)===Number(c.customer_no)?"selected":""}>${c.customer_no} — ${esc(c.name)}</option>`).join("");
  const articleOptions=state.articles.map(a=>`<option value="${a.article_no}" ${Number(r?.article_no)===Number(a.article_no)?"selected":""}>${a.article_no} — ${esc(a.name)}</option>`).join("");
  openModal(id?"Redigera prisvillkor":"Nytt prisvillkor",id?`ID ${id}`:"SAP-inspirerad condition record",`<div class="form-grid two">
    <div class="field"><label>Typ</label><select id="pcType" onchange="togglePricingDates()"><option value="STANDARD" ${type==="STANDARD"?"selected":""}>Kundpris</option><option value="CAMPAIGN" ${type==="CAMPAIGN"?"selected":""}>Kampanj</option></select></div>
    <div class="field"><label>Pris</label><input id="pcPrice" type="number" min="0.01" step="0.01" value="${r?esc(r.price):""}" required></div>
    <div class="field span2"><label>Kund</label><select id="pcCustomer">${customerOptions}</select></div>
    <div class="field span2"><label>Artikel</label><select id="pcArticle">${articleOptions}</select></div>
    <div class="field pricing-date"><label>Gäller från</label><input id="pcFrom" type="date" value="${esc(r?.valid_from||"")}"></div>
    <div class="field pricing-date"><label>Gäller till</label><input id="pcTo" type="date" value="${esc(r?.valid_to||"")}"></div>
    <div class="field span2"><label>Namn / kampanj</label><input id="pcName" value="${esc(r?.name||"")}" placeholder="T.ex. Vecka 42 ICA"></div>
    <div class="field span2"><label>Anteckning</label><textarea id="pcNotes" placeholder="Valfritt">${esc(r?.notes||"")}</textarea></div>
    <div class="modal-actions span2">${id?`<button class="btn danger" type="button" onclick="archivePricingCondition(${id})">Arkivera</button>`:""}<button class="btn primary" type="button" onclick="savePricingCondition(${id||"null"})">SPARA</button></div>
  </div>`); togglePricingDates();
}
function togglePricingDates(){
  const camp=$("pcType")?.value==="CAMPAIGN";
  document.querySelectorAll(".pricing-date input").forEach(i=>i.required=camp);
}
async function savePricingCondition(id){
  const payload={condition_type:$("pcType").value,customer_no:Number($("pcCustomer").value),article_no:Number($("pcArticle").value),price:Number($("pcPrice").value),valid_from:$("pcFrom").value,valid_to:$("pcTo").value,name:$("pcName").value,notes:$("pcNotes").value,active:true};
  if(!payload.price||payload.price<=0){showToast("Pris måste vara större än 0.");return;}
  try{await api(id?`/api/pricing/conditions/${id}`:"/api/pricing/conditions",{method:id?"PUT":"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});closeModal();showToast(id?"Prisvillkor uppdaterat.":"Prisvillkor skapat.");await loadPricingPage(true);}
  catch(err){showToast(err.message)}
}
async function archivePricingCondition(id){
  if(!confirm("Arkivera prisvillkoret? Gamla order behåller sitt sparade pris."))return;
  try{await api(`/api/pricing/conditions/${id}`,{method:"DELETE"});closeModal();showToast("Prisvillkor arkiverat.");await loadPricingPage(true);}catch(err){showToast(err.message)}
}

// ---------------- History ----------------
function setupSearchBindings(){
  $("historySearch").addEventListener("input",debounce(()=>{state.historyOffset=0;loadHistory()}));
  ["historySource","historyStatus","historyWeekday"].forEach(id=>$(id).addEventListener("change",()=>{state.historyOffset=0;loadHistory()}));
  $("customerSearch").addEventListener("input",debounce(loadCustomerMasterPage));
  $("articleMasterSearch").addEventListener("input",debounce(loadArticleMasterPage));
  if($("pricingSearch")) $("pricingSearch").addEventListener("input",debounce(()=>loadPricingPage(false)));
  if($("pricingStatus")) $("pricingStatus").addEventListener("change",()=>loadPricingPage(false));
  if($("pricingDate")) $("pricingDate").addEventListener("change",()=>loadPricingPage(false));
  if($("materialMasterSearch")) $("materialMasterSearch").addEventListener("input",debounce(loadMaterialMasterPage));
  if($("supplierSearch")) $("supplierSearch").addEventListener("input",debounce(loadSuppliersPage));
  if($("infoRecordSearch")) $("infoRecordSearch").addEventListener("input",debounce(loadInfoRecordsPage));
  ["purchaseHistoryMaterialSearch","purchaseHistorySupplierSearch","purchaseHistoryReferenceSearch"].forEach(id=>{if($(id)) $(id).addEventListener("input",debounce(loadPurchaseHistoryPage));});
  ["purchaseHistoryFrom","purchaseHistoryTo"].forEach(id=>{if($(id)) $(id).addEventListener("change",loadPurchaseHistoryPage);});
  $("labelOrderSearch").addEventListener("input",debounce(searchLabelOrders));
  $("fortnoxSearch").addEventListener("input",debounce(searchFortnoxOrders));
}
function resetHistoryFilters(){
  $("historySearch").value=""; $("historySource").value="all"; $("historyStatus").value="all"; $("historyWeekday").value="all";
  state.historyOffset=0; loadHistory();
}
async function loadHistory(){
  try{
    const p=new URLSearchParams({
      search:$("historySearch").value,source:$("historySource").value,status:$("historyStatus").value,
      weekday:$("historyWeekday").value,limit:state.historyLimit,offset:state.historyOffset
    });
    const d=await api("/api/orders?"+p);
    if($("historyStatus").options.length<=1){
      d.statuses.forEach(s=>$("historyStatus").insertAdjacentHTML("beforeend",`<option value="${esc(s)}">${esc(s)}</option>`));
    }
    $("historyTable").innerHTML=orderTableHtml(d.rows);
    const from=d.total?state.historyOffset+1:0, to=Math.min(d.total,state.historyOffset+state.historyLimit);
    $("historyPagination").innerHTML=`<span>Visar ${from}–${to} av ${d.total}</span>
      <div class="row-actions"><button class="btn" ${state.historyOffset===0?"disabled":""} onclick="historyPrev()">← Föregående</button>
      <button class="btn" ${to>=d.total?"disabled":""} onclick="historyNext()">Nästa →</button></div>`;
  }catch(err){ $("historyTable").innerHTML=`<div class="empty">${esc(err.message)}</div>`; }
}
function historyPrev(){state.historyOffset=Math.max(0,state.historyOffset-state.historyLimit);loadHistory()}
function historyNext(){state.historyOffset+=state.historyLimit;loadHistory()}
function exportHistoryExcel(){
  const p=new URLSearchParams({
    search:$("historySearch").value,source:$("historySource").value,status:$("historyStatus").value,weekday:$("historyWeekday").value
  });
  showToast("Skapar Excel-export…");
  window.location.href="/api/orders/export.xlsx?"+p.toString();
}

// ---------------- Drawer / order detail ----------------
function closeDrawer(){ $("drawer").classList.remove("open"); $("drawerBackdrop").classList.remove("open"); }
function openDrawer(title,sub,html){
  $("drawerTitle").textContent=title; $("drawerSub").textContent=sub||""; $("drawerBody").innerHTML=html;
  $("drawer").classList.add("open"); $("drawerBackdrop").classList.add("open");
}
async function openOrder(id){
  try{
    const o=await api(`/api/orders/${id}`);
    const t=o.totals;
    const legacyStatus=o.legacy_line_data_status||null;
    const unverifiedLegacyPrice=o.source==="legacy" && legacyStatus && !legacyStatus.price_verified;
    const lines=o.lines.map(l=>`<tr><td>${l.line_no}</td><td><strong>${l.article_no}</strong></td><td>${esc(l.article_name||l.description_snapshot||"")}</td>
      <td class="t-right">${fmt(l.ordered_qty,2)}</td><td class="t-right">${l.unit_price===null||l.unit_price===undefined?"—":`${unverifiedLegacyPrice?"≈ ":""}${fmt(l.unit_price,2)}`}</td>
      <td class="t-right">${l.kg===null||l.kg===undefined?"—":fmt(l.kg,1)}</td></tr>`).join("");
    const legacyLineNote=o.source!=="legacy"?"":(!legacyStatus?"":
      legacyStatus.price_verified && legacyStatus.kg_verified
        ? `<div class="notice blue" style="margin-top:12px">Radpris och kg är återskapade från Excel-masterdata och verifierade mot den sparade ordersumman/vikten.</div>`
        : legacyStatus.kg_verified
          ? `<div class="notice amber" style="margin-top:12px">Kg är verifierat mot sparad ordervikt. Pris med ≈ är återskapat från tillgänglig prislista och kan avvika på äldre order eftersom Excel inte sparade ett permanent radpris.</div>`
          : `<div class="notice amber" style="margin-top:12px">Radpris/kg är återskapade från tillgänglig masterdata. Den sparade ordersumman ovan är fortfarande den historiska källan.</div>`);
    const materials=o.source==="legacy"
      ? Object.entries(o.legacy_components||{}).map(([k,v])=>`<tr><td>${esc(k)}</td><td class="t-right">${fmt(v,2)}</td></tr>`).join("")
      : o.materials.map(m=>`<tr><td>${esc(m.component_name)}<div class="small muted">${esc(m.category)}</div></td><td class="t-right">${fmt(m.quantity,2)}</td></tr>`).join("");
    const stationHtml=o.stations.length?o.stations.map(s=>`<span class="badge ${s.ready?"green":"gray"}">S${s.station_id} · ${fmt(s.required_pallets,2)} pall ${s.ready?"✓":""}</span>`).join(" "):`<span class="muted small">Ingen stationsdata.</span>`;

    openDrawer(`Order ${o.order_no}`,`${o.customer_no} — ${o.customer_name}`,`
      <div class="detail-grid">
        <div class="detail-item"><div class="k">Leverans</div><div class="v">${fmtDate(o.delivery_date)} · ${weekdaySv(o.delivery_date)}</div></div>
        <div class="detail-item"><div class="k">Status</div><div class="v">${badge(o.status,statusKind(o.status))}</div></div>
        <div class="detail-item"><div class="k">Källa</div><div class="v">${o.source==="legacy"?"Excel / sparade":"ERP v2"}</div></div>
        <div class="detail-item"><div class="k">Vikt</div><div class="v">${fmt(t.kg,0)} kg</div></div>
        <div class="detail-item"><div class="k">Pall / PPL</div><div class="v">${fmt(t.pallets,2)} / ${fmt(t.ppl,2)}</div></div>
        <div class="detail-item"><div class="k">Total</div><div class="v">${money(t.invoice_total)}</div></div>
      </div>
      <div style="margin-bottom:14px">${stationHtml}</div>
      ${o.source==="erp_v2"?`<div class="row-actions" style="margin-bottom:14px"><label style="margin:0">Status</label><select style="width:auto" onchange="setOrderStatus(${o.id},this.value)">${["Ny","Orderbekräftad","Fortnox","Ej Fortnox","Fakturerad","Inlevererad"].map(s=>`<option ${o.status===s?"selected":""}>${s}</option>`).join("")}</select></div>`:""}
      ${o.info?`<div class="notice blue" style="margin-bottom:14px">${esc(o.info)}</div>`:""}
      <div class="tabs">
        <button class="tab active" onclick="drawerTab(this,'d-lines')">Orderrader</button>
        <button class="tab" onclick="drawerTab(this,'d-materials')">Material</button>
        <button class="tab" onclick="drawerTab(this,'d-customer')">Kund / transport</button>
        <button class="tab" onclick="drawerTab(this,'d-activity')">Händelser</button>
      </div>
      <div class="tab-panel active" id="d-lines"><div class="table-wrap"><table><thead><tr><th>Rad</th><th>Art</th><th>Benämning</th><th class="t-right">Antal</th><th class="t-right">Pris</th><th class="t-right">Kg</th></tr></thead><tbody>${lines||`<tr><td colspan="6">Inga rader</td></tr>`}</tbody></table></div>${legacyLineNote}</div>
      <div class="tab-panel" id="d-materials"><div class="table-wrap"><table><tbody>${materials||`<tr><td>Ingen materialdata.</td></tr>`}</tbody></table></div></div>
      <div class="tab-panel" id="d-customer">
        <div class="detail-grid">
          <div class="detail-item"><div class="k">Adress</div><div class="v">${esc(o.address||"—")}<br>${esc(o.postal_address||"")}</div></div>
          <div class="detail-item"><div class="k">Åkeri</div><div class="v">${esc(o.carrier||"—")}</div></div>
          <div class="detail-item"><div class="k">Lasttid</div><div class="v">${esc(o.loading_time||"—")}</div></div>
        </div>
        <div class="small muted">Kundreferens: ${esc(o.customer_reference||"—")} · Vår ref: ${esc(o.internal_reference||"—")}</div>
      </div>
      <div class="tab-panel" id="d-activity">${o.activity.length?o.activity.map(a=>`<div class="activity"><div class="activity-icon">•</div><div><div>${esc(a.message)}</div><div class="time">${esc(a.created_at)}</div></div></div>`).join(""):`<div class="empty">Ingen aktivitet.</div>`}</div>
    `);
  }catch(err){ showToast(err.message); }
}

async function setOrderStatus(orderId,status){
  try{
    await api(`/api/orders/${orderId}/status`,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({status})});
    showToast("Orderstatus uppdaterad.");
    await loadDashboard();
    if(state.currentPage==="history") await loadHistory();
  }catch(err){showToast(err.message)}
}

function drawerTab(btn,id){
  const parent=btn.closest(".drawer-body");
  parent.querySelectorAll(".tab").forEach(x=>x.classList.remove("active"));
  parent.querySelectorAll(".tab-panel").forEach(x=>x.classList.remove("active"));
  btn.classList.add("active"); parent.querySelector("#"+id).classList.add("active");
}

// ---------------- Modal ----------------
function openModal(title,sub,html){
  $("modalTitle").textContent=title;
  $("modalSub").textContent=sub||"";
  $("modalBody").innerHTML=html;
  $("modalBackdrop").classList.add("open");
  $("modal").classList.add("open");
}
function closeModal(){
  $("modalBackdrop").classList.remove("open");
  $("modal").classList.remove("open");
}
function numberOrNull(v){
  if(v===null || v===undefined || String(v).trim()==="") return null;
  const n=Number(v); return Number.isFinite(n)?n:null;
}

async function reloadActiveMasterData(){
  const [customers,articles,meta]=await Promise.all([
    api("/api/customers?limit=2000"),
    api("/api/articles?limit=3000"),
    api("/api/meta")
  ]);
  state.customers=customers; state.articles=articles; state.meta=meta;
  state.customerByNo=new Map(); state.articleByNo=new Map();
  customers.forEach(c=>state.customerByNo.set(Number(c.customer_no),c));
  articles.forEach(a=>state.articleByNo.set(Number(a.article_no),a));
}

// ---------------- Customers ----------------
async function loadCustomerMasterPage(){
  const q=$("customerSearch")?.value||"";
  const inactive=$("showArchivedCustomers")?.checked?"true":"false";
  try{
    const list=await api(`/api/customers?limit=2000&include_inactive=${inactive}&search=${encodeURIComponent(q)}`);
    state.masterCustomers=list;
    renderCustomers(list);
  }catch(err){ $("customerList").innerHTML=`<div class="empty">${esc(err.message)}</div>`; }
}

function renderCustomers(list=state.masterCustomers){
  $("customerList").innerHTML=list.map(c=>`<div class="entity-card ${c.active?"":"archived"}" onclick="openCustomer(${c.customer_no})">
    <div class="entity-top"><div class="entity-title">${c.customer_no} — ${esc(c.name)}</div>${c.active?badge("Aktiv","green"):badge("Arkiverad","gray")}</div>
    <div class="entity-sub">${esc(c.address||"")} ${esc(c.postal_address||"")}</div>
    <div class="entity-sub">${esc(c.carrier||"Inget standardåkeri")} · ${c.pallet_system===1?"SRS":c.pallet_system===2?"EUR":"Pallsystem —"}${c.user_modified?" · ändrad i ERP":""}</div>
  </div>`).join("") || `<div class="empty">Ingen kund.</div>`;
}

async function openCustomer(no){
  const c=await api(`/api/customers/${no}`);
  openDrawer(`${c.customer_no} — ${c.name}`,c.active?"Kundkort":"Arkiverad kund",`
    <div class="detail-grid">
      <div class="detail-item"><div class="k">Adress</div><div class="v">${esc(c.address||"—")}<br>${esc(c.postal_address||"")}</div></div>
      <div class="detail-item"><div class="k">Åkeri</div><div class="v">${esc(c.carrier||"—")}</div></div>
      <div class="detail-item"><div class="k">Pallsystem</div><div class="v">${c.pallet_system===1?"SRS":c.pallet_system===2?"EUR":"—"}</div></div>
      <div class="detail-item"><div class="k">Faktura-mail</div><div class="v">${esc(c.invoice_email||"—")}</div></div>
      <div class="detail-item"><div class="k">Etikettnamn</div><div class="v">${esc(c.label_name||"—")}</div></div>
      <div class="detail-item"><div class="k">Lastningstid</div><div class="v">${esc(c.loading_time||"—")}</div></div>
    </div>
    ${c.notes?`<div class="notice blue">${esc(c.notes)}</div>`:""}
    <div style="margin-top:18px"><strong>${fmt(c.stats.orders,0)}</strong> historiska/nya order · senaste leverans ${fmtDate(c.stats.last_delivery)}</div>
    <div class="row-actions" style="margin-top:18px;justify-content:flex-start">
      ${c.active?`<button class="btn primary" onclick="closeDrawer();goPage('order');selectCustomer(${c.customer_no})">Skapa order</button>`:""}
      <button class="btn" onclick="closeDrawer();openCustomerEditor(${c.customer_no})">Redigera</button>
      ${c.active?`<button class="btn danger-soft" onclick="archiveCustomer(${c.customer_no})">Arkivera</button>`:""}
    </div>`);
}

async function openCustomerEditor(no=null){
  let c={
    customer_no:state.meta?.next_customer_no||"",name:"",address:"",postal_address:"",carrier:"",pallet_system:null,
    srs_no:"",bonus_pct:null,default_reference:"",order_email:"",invoice_email:"",label_name:"",notes:"",
    transport_email:"",fortnox_flag:null,phone:"",loading_time:"",pallet_material:"",smak_profile:"",active:1
  };
  if(no!==null) c=await api(`/api/customers/${no}`);
  const editing=no!==null;
  openModal(editing?`Redigera kund ${c.customer_no}`:"Ny kund",editing?"Ändringar sparas i ERP-databasen och skyddas vid framtida Excel-import.":"Skapa kunden direkt i ERP.",`
    <form id="customerEditorForm" onsubmit="saveCustomerEditor(event,${editing?c.customer_no:'null'})">
      <div class="editor-grid">
        <div class="field"><label>Kundnummer</label><input id="customerNoEditor" name="customer_no" type="number" required value="${esc(c.customer_no)}" ${editing?"readonly":""} ${editing?"":'oninput="checkCustomerNumberAvailability(this.value)"'}><div id="customerNoConflict" class="field-error" aria-live="polite"></div></div>
        <div class="field span2"><label>Namn</label><input name="name" type="text" required value="${esc(c.name)}"></div>
        <div class="field span2"><label>Adress</label><input name="address" type="text" value="${esc(c.address||"")}"></div>
        <div class="field"><label>Postadress</label><input name="postal_address" type="text" value="${esc(c.postal_address||"")}"></div>
        <div class="field"><label>Åkeri</label><input name="carrier" type="text" value="${esc(c.carrier||"")}"></div>
        <div class="field"><label>Pallsystem</label><select name="pallet_system"><option value="">—</option><option value="1" ${Number(c.pallet_system)===1?"selected":""}>SRS</option><option value="2" ${Number(c.pallet_system)===2?"selected":""}>EUR</option></select></div>
        <div class="field"><label>SRS-nummer</label><input name="srs_no" type="text" value="${esc(c.srs_no||"")}"></div>
        <div class="field"><label>Standardreferens</label><input name="default_reference" type="text" value="${esc(c.default_reference||"")}"></div>
        <div class="field"><label>Lastningstid</label><input name="loading_time" type="text" placeholder="t.ex. 07:30" value="${esc(c.loading_time||"")}"></div>
        <div class="field"><label>Telefon</label><input name="phone" type="text" value="${esc(c.phone||"")}"></div>
        <div class="field"><label>Order-mail</label><input name="order_email" type="text" value="${esc(c.order_email||"")}"></div>
        <div class="field"><label>Faktura-mail</label><input name="invoice_email" type="text" value="${esc(c.invoice_email||"")}"></div>
        <div class="field"><label>Transport-mail</label><input name="transport_email" type="text" value="${esc(c.transport_email||"")}"></div>
        <div class="field"><label>Etikettnamn</label><input name="label_name" type="text" value="${esc(c.label_name||"")}"></div>
        <div class="field"><label>Bonus %</label><input name="bonus_pct" type="number" step="any" value="${c.bonus_pct??""}"></div>
        <div class="field"><label>Fortnox</label><select name="fortnox_flag"><option value="">—</option><option value="1" ${Number(c.fortnox_flag)===1?"selected":""}>Ja / 1</option><option value="2" ${Number(c.fortnox_flag)===2?"selected":""}>Nej / 2</option></select></div>
        <div class="field"><label>Pallmaterial</label><input name="pallet_material" type="text" value="${esc(c.pallet_material||"")}"></div>
        <div class="field"><label>SMAK-profil</label><input name="smak_profile" type="text" value="${esc(c.smak_profile||"")}"></div>
        <div class="field span3"><label>Övrig info</label><textarea name="notes">${esc(c.notes||"")}</textarea></div>
        <div class="field span3"><label class="check"><input name="active" type="checkbox" ${c.active?"checked":""}> Aktiv kund — synlig vid orderregistrering</label></div>
      </div>
      <div class="editor-actions"><div class="status-note">Kundnummer kan inte ändras efter att kunden skapats.</div><div class="right"><button type="button" class="btn" onclick="closeModal()">Avbryt</button><button class="btn primary" type="submit">SPARA KUND</button></div></div>
    </form>`);
}

async function checkCustomerNumberAvailability(value){
  const box=$("customerNoConflict");
  const input=$("customerNoEditor");
  if(!box || !input) return;
  box.textContent="";
  input.classList.remove("input-error");
  const no=Number(value);
  if(!Number.isInteger(no) || no<=0) return;
  try{
    const c=await api(`/api/customers/${no}`);
    if(Number(input.value)!==no) return;
    const stateText=c.active?"aktiv":"arkiverad";
    box.textContent=`Kundnummer redan i bruk — ${c.name} (${stateText}).`;
    input.classList.add("input-error");
  }catch(err){
    // 404 means the number is free. Other errors should not block typing.
    if(String(err.message||"").toLowerCase().includes("finns inte")) return;
  }
}

async function checkArticleNumberAvailability(value){
  const box=$("articleNoConflict");
  const input=$("articleNoEditor");
  if(!box || !input) return;
  box.textContent="";
  input.classList.remove("input-error");
  const no=Number(value);
  if(!Number.isInteger(no) || no<=0) return;
  try{
    const a=await api(`/api/articles/${no}`);
    if(Number(input.value)!==no) return;
    const stateText=a.active?"aktiv":"arkiverad";
    box.textContent=`Artikelnummer redan i bruk — ${a.name} (${stateText}).`;
    input.classList.add("input-error");
  }catch(err){
    if(String(err.message||"").toLowerCase().includes("finns inte")) return;
  }
}

async function saveCustomerEditor(e,existingNo){
  e.preventDefault();
  const f=new FormData(e.currentTarget);
  const payload={
    customer_no:Number(f.get("customer_no")),name:String(f.get("name")||"").trim(),address:String(f.get("address")||""),postal_address:String(f.get("postal_address")||""),
    carrier:String(f.get("carrier")||""),pallet_system:numberOrNull(f.get("pallet_system")),srs_no:String(f.get("srs_no")||""),bonus_pct:numberOrNull(f.get("bonus_pct")),
    default_reference:String(f.get("default_reference")||""),order_email:String(f.get("order_email")||""),invoice_email:String(f.get("invoice_email")||""),label_name:String(f.get("label_name")||""),
    notes:String(f.get("notes")||""),transport_email:String(f.get("transport_email")||""),fortnox_flag:numberOrNull(f.get("fortnox_flag")),phone:String(f.get("phone")||""),
    loading_time:String(f.get("loading_time")||""),pallet_material:String(f.get("pallet_material")||""),smak_profile:String(f.get("smak_profile")||""),active:f.get("active")!==null
  };
  try{
    await api(existingNo===null?"/api/customers":`/api/customers/${existingNo}`,{method:existingNo===null?"POST":"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
    closeModal(); await reloadActiveMasterData(); await loadCustomerMasterPage();
    showToast(existingNo===null?`Kund ${payload.customer_no} skapad.`:`Kund ${payload.customer_no} uppdaterad.`);
  }catch(err){
    const msg=String(err.message||"");
    const box=$("customerNoConflict");
    const input=$("customerNoEditor");
    if(existingNo===null && box && input && (msg.includes("Kundnummer") || msg.includes("409"))){
      box.textContent=msg; input.classList.add("input-error"); input.focus();
    }else showToast("Kunde inte spara kund: "+msg);
  }
}

async function archiveCustomer(no){
  if(!confirm(`Arkivera kund ${no}? Historiska order påverkas inte.`)) return;
  try{
    await api(`/api/customers/${no}`,{method:"DELETE"}); closeDrawer(); await reloadActiveMasterData(); await loadCustomerMasterPage(); showToast(`Kund ${no} arkiverad.`);
  }catch(err){showToast(err.message)}
}

// ---------------- Articles ----------------
async function loadArticleMasterPage(){
  const q=$("articleMasterSearch")?.value||"";
  const inactive=$("showArchivedArticles")?.checked?"true":"false";
  try{
    const list=await api(`/api/articles?limit=3000&include_inactive=${inactive}&search=${encodeURIComponent(q)}`);
    state.masterArticles=list; renderArticles(list);
  }catch(err){ $("articleMasterTable").innerHTML=`<div class="empty">${esc(err.message)}</div>`; }
}

function renderArticles(list=state.masterArticles){
  $("articleMasterTable").innerHTML=list.length?`<table><thead><tr><th>Artikel</th><th>Benämning</th><th>Station</th><th class="t-right">Kg</th><th class="t-right">/ Pall</th><th class="t-right">/ PPL</th><th class="t-right">Pant</th><th>Status</th></tr></thead>
  <tbody>${list.map(a=>`<tr class="clickable ${a.active?"":"archived"}" onclick="openArticle(${a.article_no})"><td><strong>${a.article_no}</strong></td><td>${esc(a.name)}${a.user_modified?`<div class="small muted">Ändrad i ERP</div>`:""}</td><td>${a.station_id?`S${a.station_id}`:"—"}</td>
  <td class="t-right">${fmt(a.kg_per_order_unit,2)}</td><td class="t-right">${fmt(a.units_per_pallet,2)}</td><td class="t-right">${fmt(a.units_per_ppl,2)}</td><td class="t-right">${fmt(a.deposit_per_unit,2)}</td><td>${a.active?badge("Aktiv","green"):badge("Arkiverad","gray")}</td></tr>`).join("")}</tbody></table>`:`<div class="empty">Ingen artikel.</div>`;
}

async function openArticle(no){
  const a=await api(`/api/articles/${no}`);
  const grouped={};
  a.components.forEach(c=>(grouped[c.category]??=[]).push(c));
  const comps=Object.entries(grouped).map(([cat,arr])=>`<h3 style="font-size:13px;margin:18px 0 7px">${esc(cat)}</h3>
    <table><tbody>${arr.map(c=>`<tr><td>${esc(c.name)}</td><td class="t-right">${fmt(c.qty_per_order_unit,4)}</td></tr>`).join("")}</tbody></table>`).join("");
  openDrawer(`${a.article_no} — ${a.name}`,a.active?"Artikelmaster / BOM":"Arkiverad artikel",`
    <div class="detail-grid">
      <div class="detail-item"><div class="k">Station</div><div class="v">${a.station_id?`S${a.station_id}`:"—"}</div></div>
      <div class="detail-item"><div class="k">Kg / orderenhet</div><div class="v">${fmt(a.kg_per_order_unit,3)}</div></div>
      <div class="detail-item"><div class="k">Antal / pall</div><div class="v">${fmt(a.units_per_pallet,2)}</div></div>
      <div class="detail-item"><div class="k">Antal / PPL</div><div class="v">${fmt(a.units_per_ppl,2)}</div></div>
      <div class="detail-item"><div class="k">Antal / etikett</div><div class="v">${fmt(a.units_per_label,2)}</div></div>
      <div class="detail-item"><div class="k">Produktionstid faktor</div><div class="v">${fmt(a.production_time_factor,4)}</div></div>
    </div>
    <div class="row-actions" style="justify-content:flex-start;margin:0 0 14px"><button class="btn" onclick="closeDrawer();openArticleEditor(${a.article_no})">Redigera artikel</button>${a.active?`<button class="btn danger-soft" onclick="archiveArticle(${a.article_no})">Arkivera</button>`:""}</div>
    <div class="card" style="box-shadow:none"><div class="card-title"><h3>BOM / komponenter</h3><span class="small muted">${a.components.length} poster · läses från Excel i v0.5</span></div><div class="card-body">${comps||`<div class="muted">Ingen komponentdata.</div>`}</div></div>`);
}

async function openArticleEditor(no=null){
  let a={article_no:state.meta?.next_article_no||"",supplier_article_no:"",station_id:null,pallets_per_hour:null,production_time_factor:0,name:"",order_unit_qty:0,unit:"",kg_per_order_unit:0,units_per_label:0,deposit_per_unit:0,units_per_pallet:0,units_per_ppl:0,active:1};
  if(no!==null) a=await api(`/api/articles/${no}`);
  const editing=no!==null;
  openModal(editing?`Redigera artikel ${a.article_no}`:"Ny artikel",editing?"Produktions- och etikettvärden används direkt i nya orderberäkningar.":"Ny artikel skapas i ERP och bevaras vid framtida Excel-import.",`
    <form id="articleEditorForm" onsubmit="saveArticleEditor(event,${editing?a.article_no:'null'})">
      <div class="editor-grid">
        <div class="field"><label>Artikelnummer</label><input id="articleNoEditor" name="article_no" type="number" required value="${esc(a.article_no)}" ${editing?"readonly":""} ${editing?"":'oninput="checkArticleNumberAvailability(this.value)"'}><div id="articleNoConflict" class="field-error" aria-live="polite"></div></div>
        <div class="field"><label>Leverantörsnummer</label><input name="supplier_article_no" type="text" value="${esc(a.supplier_article_no||"")}"></div>
        <div class="field"><label>Station</label><select name="station_id"><option value="">—</option>${[1,2,3,4].map(x=>`<option value="${x}" ${Number(a.station_id)===x?"selected":""}>S${x}</option>`).join("")}</select></div>
        <div class="field span3"><label>Benämning</label><input name="name" type="text" required value="${esc(a.name||"")}"></div>
        <div class="field"><label>Orderenhet antal</label><input name="order_unit_qty" type="number" step="any" value="${a.order_unit_qty??0}"></div>
        <div class="field"><label>Enhet</label><input name="unit" type="text" placeholder="st / kg" value="${esc(a.unit||"")}"></div>
        <div class="field"><label>Kg / orderenhet</label><input name="kg_per_order_unit" type="number" step="any" value="${a.kg_per_order_unit??0}"></div>
        <div class="field"><label>Antal / pall</label><input name="units_per_pallet" type="number" step="any" value="${a.units_per_pallet??0}"></div>
        <div class="field"><label>Antal / PPL</label><input name="units_per_ppl" type="number" step="any" value="${a.units_per_ppl??0}"></div>
        <div class="field"><label>Antal / etikett</label><input name="units_per_label" type="number" step="any" value="${a.units_per_label??0}"></div>
        <div class="field"><label>Pant / enhet</label><input name="deposit_per_unit" type="number" step="any" value="${a.deposit_per_unit??0}"></div>
        <div class="field"><label>Pall / timme</label><input name="pallets_per_hour" type="number" step="any" value="${a.pallets_per_hour??""}"></div>
        <div class="field"><label>Produktionstid faktor</label><input name="production_time_factor" type="number" step="any" value="${a.production_time_factor??0}"></div>
        <div class="field span3"><label class="check"><input name="active" type="checkbox" ${a.active?"checked":""}> Aktiv artikel — valbar på nya order</label></div>
      </div>
      <div class="editor-actions"><div class="status-note">BOM-komponenter är fortfarande Excel-importerade i v0.5; kärnuppgifterna ovan är redigerbara.</div><div class="right"><button type="button" class="btn" onclick="closeModal()">Avbryt</button><button class="btn primary" type="submit">SPARA ARTIKEL</button></div></div>
    </form>`);
}

async function saveArticleEditor(e,existingNo){
  e.preventDefault(); const f=new FormData(e.currentTarget);
  const payload={
    article_no:Number(f.get("article_no")),supplier_article_no:String(f.get("supplier_article_no")||""),station_id:numberOrNull(f.get("station_id")),pallets_per_hour:numberOrNull(f.get("pallets_per_hour")),
    production_time_factor:Number(f.get("production_time_factor")||0),name:String(f.get("name")||"").trim(),order_unit_qty:Number(f.get("order_unit_qty")||0),unit:String(f.get("unit")||""),
    kg_per_order_unit:Number(f.get("kg_per_order_unit")||0),units_per_label:Number(f.get("units_per_label")||0),deposit_per_unit:Number(f.get("deposit_per_unit")||0),units_per_pallet:Number(f.get("units_per_pallet")||0),units_per_ppl:Number(f.get("units_per_ppl")||0),active:f.get("active")!==null
  };
  try{
    await api(existingNo===null?"/api/articles":`/api/articles/${existingNo}`,{method:existingNo===null?"POST":"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
    closeModal(); await reloadActiveMasterData(); await loadArticleMasterPage(); showToast(existingNo===null?`Artikel ${payload.article_no} skapad.`:`Artikel ${payload.article_no} uppdaterad.`);
  }catch(err){
    const msg=String(err.message||"");
    const box=$("articleNoConflict");
    const input=$("articleNoEditor");
    if(existingNo===null && box && input && (msg.includes("Artikelnummer") || msg.includes("409"))){
      box.textContent=msg; input.classList.add("input-error"); input.focus();
    }else showToast("Kunde inte spara artikel: "+msg);
  }
}

async function archiveArticle(no){
  if(!confirm(`Arkivera artikel ${no}? Historiska order påverkas inte.`)) return;
  try{
    await api(`/api/articles/${no}`,{method:"DELETE"}); closeDrawer(); await reloadActiveMasterData(); await loadArticleMasterPage(); showToast(`Artikel ${no} arkiverad.`);
  }catch(err){showToast(err.message)}
}



// ---------------- Purchasing / canonical material + inventory + suppliers + info records + history (v0.7.10) ----------------
let purchasingTab="MATERIALS";
const MATERIAL_TYPE_LABELS={RETURN_PACKAGING:"Returemballage",PACKAGING:"Wellpapp / emballage",FILM:"Plastfilm",LABEL:"Etikett",PALLET:"Pall",CONSUMABLE:"Förbrukningsmaterial",RAW_MATERIAL:"Råvara",SERVICE:"Tjänst / kostnad",OTHER:"Övrigt"};
const INFO_SOURCE_LABELS={PREFERRED:"Föredragen",APPROVED:"Godkänd",BACKUP:"Backup",BLOCKED:"Spärrad"};
function infoStatusBadge(status){
  const s=String(status||"APPROVED").toUpperCase();
  const cls=s==="PREFERRED"?"green":s==="BLOCKED"?"red":s==="BACKUP"?"amber":"blue";
  return badge(INFO_SOURCE_LABELS[s]||s,cls);
}
function priceUnitText(r){
  if(r.purchase_price===null||r.purchase_price===undefined) return "—";
  const q=Number(r.price_unit_qty||1),unit=r.purchase_unit||r.component_unit||"";
  return `${fmt(r.purchase_price,4)} ${esc(r.currency||"SEK")} / ${fmt(q,q%1?2:0)}${unit?" "+esc(unit):""}`;
}
function purchasingDateRange(r){
  if(!r.valid_from&&!r.valid_to) return "Tills vidare";
  if(r.valid_from&&r.valid_to) return `${fmtDate(r.valid_from)} – ${fmtDate(r.valid_to)}`;
  if(r.valid_from) return `Från ${fmtDate(r.valid_from)}`;
  return `Till ${fmtDate(r.valid_to)}`;
}
function setPurchasingTab(tab){
  purchasingTab=tab;
  document.querySelectorAll("[data-purchasing-tab]").forEach(b=>b.classList.toggle("active",b.dataset.purchasingTab===tab));
  if($("purchasingMaterialsPanel")) $("purchasingMaterialsPanel").style.display=tab==="MATERIALS"?"":"none";
  if($("purchasingSuppliersPanel")) $("purchasingSuppliersPanel").style.display=tab==="SUPPLIERS"?"":"none";
  if($("purchasingInfoPanel")) $("purchasingInfoPanel").style.display=tab==="INFO"?"":"none";
  if($("purchasingHistoryPanel")) $("purchasingHistoryPanel").style.display=tab==="HISTORY"?"":"none";
  if(tab==="MATERIALS") loadMaterialMasterPage(); else if(tab==="INFO") loadInfoRecordsPage(); else if(tab==="HISTORY") loadPurchaseHistoryPage(); else loadSuppliersPage();
}
function loadPurchasingPage(){
  if(purchasingTab==="MATERIALS") loadMaterialMasterPage(); else if(purchasingTab==="INFO") loadInfoRecordsPage(); else if(purchasingTab==="HISTORY") loadPurchaseHistoryPage(); else loadSuppliersPage();
}

function materialReviewBadge(status){
  const s=String(status||"REVIEW").toUpperCase();
  if(s==="OK")return badge("Granskad","green");
  if(s==="LEGACY_ALIAS")return badge("Legacy alias","amber");
  if(s==="LEGACY_BOM")return badge("Legacy BOM","gray");
  return badge("Granska","blue");
}
function materialTypeLabel(t){return MATERIAL_TYPE_LABELS[String(t||"OTHER").toUpperCase()]||t||"Övrigt";}
function materialFlagsHtml(m){
  const flags=[]; if(m.purchasable)flags.push(badge("Inköpsbar","green")); if(m.stock_managed)flags.push(badge("Lager","blue")); if(m.bom_usable)flags.push(badge("BOM","gray"));
  return flags.join(" ")||"—";
}
async function loadMaterialMasterPage(){
  const p=new URLSearchParams({search:$("materialMasterSearch")?.value||"",material_type:$("materialMasterType")?.value||"all",include_inactive:$("showArchivedMaterials")?.checked?"true":"false",purchasable_only:$("materialMasterPurchasableOnly")?.checked?"true":"false",limit:"5000"});
  try{const rows=await api(`/api/purchasing/material-master?${p}`);renderMaterialMasterTable(rows);}catch(err){$("materialMasterTable").innerHTML=`<div class="empty">${esc(err.message)}</div>`;}
}
function inventoryText(m){
  if(m.excel_main_stock!=null) return `<strong>${fmt(Number(m.excel_main_stock||0),2)} ${esc(m.unit||"")}</strong><div class="small muted">Excel MAIN</div>`;
  if(!m.stock_managed) return `<span class="muted">Ej lagerförd</span>`;
  if(!m.inventory_movement_count) return `<span class="badge amber">Ingen MAIN-saldo</span>`;
  return `<strong>${fmt(Number(m.on_hand||0),2)} ${esc(m.unit||"")}</strong><div class="small muted">ERP-ledger</div>`;
}
function renderMaterialMasterTable(rows){
  $("materialMasterTable").innerHTML=rows.length?`<table><thead><tr><th>Material</th><th>Typ</th><th>Saldo</th><th class="t-right">SL</th><th class="t-right">MOQ</th><th>Basenhet</th><th>Inköpsenhet</th><th>Styrning</th><th class="t-right">Kopplingar</th></tr></thead><tbody>${rows.map(m=>`<tr class="clickable ${m.active?"":"archived"}" onclick="openPurchasingMaterial('${esc(m.code)}')"><td><strong>${esc(m.code)}</strong> · ${esc(m.name)}${m.alias_count?`<div class="small muted">${m.alias_count} gamla Excel-alias</div>`:""}</td><td>${esc(materialTypeLabel(m.material_type))}</td><td>${inventoryText(m)}</td><td class="t-right">${m.excel_main_reorder_point==null?"—":fmt(Number(m.excel_main_reorder_point),0)}</td><td class="t-right">${m.excel_main_moq==null?"—":fmt(Number(m.excel_main_moq),0)}</td><td>${esc(m.unit||"—")}</td><td>${esc(m.purchase_unit||"—")}</td><td>${materialFlagsHtml(m)}</td><td class="t-right">${m.info_record_count||0} info · ${m.purchase_history_count||0} köp · ${m.bom_usage_count||0} BOM</td></tr>`).join("")}</tbody></table>`:`<div class="empty">Inga kanoniska material matchar filtret.</div>`;
}
function inventoryMovementsTable(rows,unit){
  if(!rows||!rows.length)return `<div class="empty">Inget startsaldo eller några lagerrörelser registrerade ännu.</div>`;
  return `<table><thead><tr><th>Tid</th><th>Händelse</th><th class="t-right">Förändring</th><th>Av</th><th>Notering</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${esc((r.created_at||"").replace("T"," ").slice(0,16))}</td><td>${r.movement_type==="INVENTORY_COUNT"?"Inventering":"Manuell justering"}</td><td class="t-right"><strong>${Number(r.quantity_base)>0?"+":""}${fmt(Number(r.quantity_base||0),2)} ${esc(unit||"")}</strong></td><td>${esc(r.created_by||"—")}</td><td>${esc(r.note||r.reference_no||"—")}</td></tr>`).join("")}</tbody></table>`;
}
async function openPurchasingMaterial(code){
  const m=await api(`/api/purchasing/material-master/${encodeURIComponent(code)}`);
  const searchedAlias=m.requested_alias?`<div class="notice blue" style="margin-top:14px"><strong>${esc(m.requested_alias.code)}</strong> är ett gammalt Excel-${m.requested_alias.review_status==="LEGACY_BOM"?"BOM-fält":"inköpsnummer"}. Du visas därför det enda lagermaterialet <strong>${esc(m.code)} — ${esc(m.name)}</strong>.</div>`:"";
  const aliases=(m.aliases||[]).length?`<div class="detail-item" style="margin-top:14px"><div class="k">Gamla Excel-identiteter</div><div class="v">${m.aliases.map(a=>`<span class="badge gray">${esc(a.code)} · ${esc(a.name)}</span>`).join(" ")}</div><div class="small muted" style="margin-top:6px">Alias är sökbara men har inget eget lagersaldo och används inte för nya inköp.</div></div>`:"";
  const recent=(m.purchase_history||[]).length?purchaseHistoryMiniTable(m.purchase_history,false,true):`<div class="empty">Ingen inköpshistorik ännu.</div>`;
  const stock=m.excel_main_stock!=null?`${fmt(Number(m.excel_main_stock||0),2)} ${esc(m.unit||"")} <span class="badge blue">Excel MAIN</span>`:(m.stock_managed?(m.inventory_initialized?`${fmt(Number(m.on_hand||0),2)} ${esc(m.unit||"")}`:"Ej inventerad"):`Ej lagerförd`);
  openDrawer(`${m.code} — ${m.name}`,`${materialTypeLabel(m.material_type)} · kanoniskt material · ${m.active?"Aktiv":"Arkiverad"}`,`
    <div class="detail-grid"><div class="detail-item"><div class="k">Saldo</div><div class="v">${stock}</div></div><div class="detail-item"><div class="k">SL / orderpunkt</div><div class="v">${m.excel_main_reorder_point==null?"—":fmt(Number(m.excel_main_reorder_point),0)}</div></div><div class="detail-item"><div class="k">MOQ</div><div class="v">${m.excel_main_moq==null?"—":fmt(Number(m.excel_main_moq),0)}</div></div><div class="detail-item"><div class="k">Beställt (MAIN)</div><div class="v">${esc(m.excel_main_on_order||"—")}</div></div><div class="detail-item"><div class="k">Basenhet</div><div class="v">${esc(m.unit||"—")}</div></div><div class="detail-item"><div class="k">Inköpsenhet</div><div class="v">${esc(m.purchase_unit||"—")}</div></div><div class="detail-item"><div class="k">Omräkning</div><div class="v">1 ${esc(m.purchase_unit||m.unit||"enhet")} = ${fmt(Number(m.purchase_to_base_factor||1),2)} ${esc(m.unit||"")}</div></div><div class="detail-item"><div class="k">Styrning</div><div class="v">${materialFlagsHtml(m)}</div></div><div class="detail-item"><div class="k">BOM-användning</div><div class="v">${m.bom_usage_count||0}</div></div><div class="detail-item"><div class="k">Info records</div><div class="v">${(m.info_records||[]).length}</div></div><div class="detail-item"><div class="k">Legacy inköpsnummer</div><div class="v">${m.legacy_purchase_article_no??"—"}</div></div></div>
    ${searchedAlias}${aliases}
    <div class="detail-item" style="margin-top:14px"><div class="k">Anteckningar</div><div class="v">${esc(m.notes||"—")}</div></div>
    ${m.stock_managed?`<div class="drawer-section"><div class="card-title"><h3>Lager</h3><div class="row-actions"><button class="btn" onclick="openInventoryAdjustment('${esc(m.code)}','COUNT',${Number(m.on_hand||0)},'${esc(m.unit||"")}')">Sätt lagersaldo</button><button class="btn" onclick="openInventoryAdjustment('${esc(m.code)}','ADJUSTMENT',${Number(m.on_hand||0)},'${esc(m.unit||"")}')">＋/− Justering</button></div></div>${inventoryMovementsTable(m.inventory_movements||[],m.unit)}</div>`:""}
    <div class="drawer-section"><div class="card-title"><h3>Senaste inköp</h3></div>${recent}</div>
    <div class="drawer-actions"><button class="btn" onclick="closeDrawer();openPurchasingMaterialEditor('${esc(m.code)}')">Redigera material</button>${m.purchasable?`<button class="btn" onclick="closeDrawer();openInfoRecordEditor(null,null,'${esc(m.code)}')">＋ Info record</button><button class="btn primary" onclick="closeDrawer();openPurchaseHistoryEditor(null,'${esc(m.code)}')">＋ Registrera inköp</button>`:""}${m.active?`<button class="btn danger-soft" onclick="archivePurchasingMaterial('${esc(m.code)}')">Arkivera</button>`:""}</div>`);
}
function openInventoryAdjustment(code,mode,current,unit){
  if(!hasPermission("purchasing_edit")){showToast("Du saknar behörighet att ändra lagersaldo.");return;}
  const count=mode==="COUNT";
  openModal(count?`Inventera ${esc(code)}`:`Lagerjustering ${esc(code)}`,count?`Ange faktiskt fysiskt saldo. ERP bokar bara differensen.`:`Ange + för inleverans/korrigering och − för uttag/korrigering.`, `<form onsubmit="saveInventoryAdjustment(event,'${esc(code)}','${mode}')"><div class="editor-grid"><div class="field"><label>${count?"Fysiskt lagersaldo":"Förändring"} (${esc(unit||"basenhet")})</label><input name="quantity" type="number" step="any" ${count?'min="0"':''} required value="${count?Number(current||0):''}"></div><div class="field span2"><label>Referens / notering</label><input name="note" placeholder="t.ex. Inventering 14/10, skadat emballage…"></div></div><div class="editor-actions"><div class="status-note">${count?`Nuvarande ERP-saldo: ${fmt(Number(current||0),2)} ${esc(unit||"")}`:"Manuella justeringar loggas i lagerhistoriken."}</div><div class="right"><button type="button" class="btn" onclick="closeModal()">Avbryt</button><button class="btn primary">BOKA</button></div></div></form>`);
}
async function saveInventoryAdjustment(e,code,mode){
  e.preventDefault();const f=new FormData(e.currentTarget);const payload={mode,quantity:Number(f.get("quantity")),note:String(f.get("note")||"")};
  try{await api(`/api/purchasing/material-master/${encodeURIComponent(code)}/inventory-adjustment`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});closeModal();closeDrawer();await loadMaterialMasterPage();await openPurchasingMaterial(code);showToast(mode==="COUNT"?"Lagersaldo inventerat.":"Lagerjustering bokad.");}catch(err){showToast(err.message);}
}
async function openPurchasingMaterialEditor(code=null){
  if(!hasPermission("purchasing_edit")){showToast("Du saknar behörighet att ändra inköpsmaterial.");return;}
  const m=code?await api(`/api/purchasing/material-master/${encodeURIComponent(code)}`):{code:"",name:"",material_type:"OTHER",unit:"st",purchase_unit:"st",purchase_to_base_factor:1,purchasable:1,stock_managed:1,bom_usable:1,canonical_material_code:"",supplier_article_no:"",review_status:"OK",notes:"",active:1};
  const typeOpts=Object.entries(MATERIAL_TYPE_LABELS).map(([v,l])=>`<option value="${v}" ${m.material_type===v?"selected":""}>${esc(l)}</option>`).join("");
  openModal(code?`Redigera material ${esc(code)}`:"Nytt inköpsmaterial",code?"ERP-materialmaster":"Skapa ett permanent ERP-material",`<form id="purchasingMaterialForm" onsubmit="savePurchasingMaterial(event,'${esc(code||"")}')"><div class="editor-grid">
    <div class="field"><label>Materialnummer</label><input name="code" required value="${esc(m.code||"")}" ${code?"readonly":""}></div><div class="field span2"><label>Benämning</label><input name="name" required value="${esc(m.name||"")}"></div><div class="field"><label>Materialtyp</label><select name="material_type">${typeOpts}</select></div>
    <div class="field"><label>Basenhet</label><input name="unit" value="${esc(m.unit||"")}" placeholder="st, kg…"></div><div class="field"><label>Inköpsenhet</label><input name="purchase_unit" value="${esc(m.purchase_unit||"")}" placeholder="st, pall, rulle…"></div><div class="field"><label>Omräkningsfaktor</label><input name="purchase_to_base_factor" type="number" min="0.000001" step="any" required value="${m.purchase_to_base_factor??1}"><div class="small muted">Antal basenheter per inköpsenhet</div></div><div class="field"><label>Legacy leverantörsart.nr</label><input name="supplier_article_no" value="${esc(m.supplier_article_no||"")}"></div>
    <div class="field"><label>Kanoniskt material</label><input name="canonical_material_code" value="${esc(m.canonical_material_code||"")}" placeholder="Tomt för normalt material"><div class="small muted">Endast legacy-alias/BOM-länk</div></div><div class="field"><label>Migrationsstatus</label><select name="review_status"><option value="OK" ${m.review_status==="OK"?"selected":""}>Granskad</option><option value="REVIEW" ${m.review_status==="REVIEW"?"selected":""}>Behöver granskas</option><option value="LEGACY_ALIAS" ${m.review_status==="LEGACY_ALIAS"?"selected":""}>Legacy alias</option><option value="LEGACY_BOM" ${m.review_status==="LEGACY_BOM"?"selected":""}>Legacy BOM</option></select></div>
    <div class="field span3"><label>Styrning</label><div class="check-row"><label class="check"><input type="checkbox" name="purchasable" ${m.purchasable?"checked":""}> Inköpsbar</label><label class="check"><input type="checkbox" name="stock_managed" ${m.stock_managed?"checked":""}> Lagerförd</label><label class="check"><input type="checkbox" name="bom_usable" ${m.bom_usable?"checked":""}> Får användas i BOM</label><label class="check"><input type="checkbox" name="active" ${m.active?"checked":""}> Aktiv</label></div></div>
    <div class="field span3"><label>Anteckningar</label><textarea name="notes" rows="3">${esc(m.notes||"")}</textarea></div></div><div class="editor-actions"><div class="status-note">I slutmålet används samma kanoniska material genom inköp, godsmottagning, lager och BOM.</div><div class="right"><button type="button" class="btn" onclick="closeModal()">Avbryt</button><button class="btn primary" type="submit">SPARA MATERIAL</button></div></div></form>`);
}
async function savePurchasingMaterial(e,code){
  e.preventDefault();const f=new FormData(e.currentTarget);const payload={code:String(f.get("code")||"").trim(),name:String(f.get("name")||""),material_type:String(f.get("material_type")||"OTHER"),unit:String(f.get("unit")||""),purchase_unit:String(f.get("purchase_unit")||""),purchase_to_base_factor:Number(f.get("purchase_to_base_factor")||1),purchasable:f.get("purchasable")!==null,stock_managed:f.get("stock_managed")!==null,bom_usable:f.get("bom_usable")!==null,canonical_material_code:String(f.get("canonical_material_code")||""),supplier_article_no:String(f.get("supplier_article_no")||""),review_status:String(f.get("review_status")||"OK"),notes:String(f.get("notes")||""),active:f.get("active")!==null};
  try{await api(code?`/api/purchasing/material-master/${encodeURIComponent(code)}`:"/api/purchasing/material-master",{method:code?"PUT":"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});closeModal();purchasingTab="MATERIALS";setPurchasingTab("MATERIALS");showToast(code?"Material uppdaterat.":"Material skapat.");}catch(err){showToast(err.message);}
}
async function archivePurchasingMaterial(code){if(!confirm(`Arkivera material ${code}? Historik och gamla inköp ligger kvar.`))return;try{await api(`/api/purchasing/material-master/${encodeURIComponent(code)}`,{method:"DELETE"});closeDrawer();await loadMaterialMasterPage();showToast("Material arkiverat.");}catch(err){showToast(err.message);}}

async function loadSuppliersPage(){
  const q=$("supplierSearch")?.value||"";
  const inactive=$("showArchivedSuppliers")?.checked?"true":"false";
  try{
    const list=await api(`/api/suppliers?limit=3000&include_inactive=${inactive}&search=${encodeURIComponent(q)}`);
    renderSupplierTable(list);
  }catch(err){ $("supplierTable").innerHTML=`<div class="empty">${esc(err.message)}</div>`; }
}

function renderSupplierTable(list){
  $("supplierTable").innerHTML=list.length?`<table><thead><tr>
    <th>Leverantör</th><th>Kontakt</th><th>Betalningsvillkor</th><th>Leveransvillkor</th><th class="t-right">Ledtid</th><th>Status</th>
  </tr></thead><tbody>${list.map(s=>`<tr class="clickable ${s.active?"":"archived"}" onclick="openSupplier(${s.supplier_no})">
    <td><strong>${s.supplier_no}</strong> · ${esc(s.name)}${s.org_no?`<div class="small muted">Org.nr ${esc(s.org_no)}</div>`:""}</td>
    <td>${esc(s.contact_name||"—")}<div class="small muted">${esc(s.email||s.phone||"")}</div></td>
    <td>${esc(s.payment_terms||"—")}</td><td>${esc(s.delivery_terms||"—")}</td>
    <td class="t-right">${s.default_lead_time_days!==null&&s.default_lead_time_days!==undefined?fmt(s.default_lead_time_days,0)+" d":"—"}</td>
    <td>${s.active?badge("Aktiv","green"):badge("Arkiverad","gray")}</td>
  </tr>`).join("")}</tbody></table>`:`<div class="empty">Ingen leverantör ännu.<br><span class="small">Skapa den första leverantören med knappen ovan.</span></div>`;
}

function purchaseHistoryPriceText(r){
  if(r.purchase_price===null||r.purchase_price===undefined) return "—";
  const q=Number(r.price_unit_qty||1),unit=r.purchase_unit||r.component_unit||"";
  return `${fmt(r.purchase_price,4)} ${esc(r.currency||"SEK")} / ${fmt(q,q%1?2:0)}${unit?" "+esc(unit):""}`;
}
function purchaseHistoryMiniTable(rows,showMaterial=true,showSupplier=true){
  if(!rows||!rows.length) return `<div class="empty">Ingen faktisk inköpshistorik registrerad ännu.</div>`;
  return `<table><thead><tr><th>Datum</th>${showMaterial?"<th>Material</th>":""}${showSupplier?"<th>Leverantör</th>":""}<th class="t-right">Kvantitet</th><th>Pris</th><th>Referens</th></tr></thead><tbody>${rows.map(r=>`<tr class="clickable" onclick="openPurchaseHistory(${r.id})"><td>${fmtDate(r.purchase_date)}</td>${showMaterial?`<td><strong>${esc(r.component_code)}</strong>${r.component_name?` · ${esc(r.component_name)}`:""}</td>`:""}${showSupplier?`<td><strong>${r.supplier_no}</strong>${r.supplier_name?` · ${esc(r.supplier_name)}`:""}</td>`:""}<td class="t-right">${r.quantity!==null&&r.quantity!==undefined?fmt(r.quantity,2)+(r.purchase_unit?" "+esc(r.purchase_unit):""):"—"}</td><td>${purchaseHistoryPriceText(r)}</td><td>${esc(r.external_reference||"—")}</td></tr>`).join("")}</tbody></table>`;
}

function supplierInfoRecordsHtml(s){
  const rows=s.info_records||[];
  if(!rows.length) return `<div class="empty">Inga materialkopplingar ännu.<br><span class="small">Skapa ett info record för att ange vad som kan köpas från leverantören.</span></div>`;
  return `<table><thead><tr><th>Material</th><th>Lev. art.nr</th><th>Pris</th><th>Källstatus</th><th class="t-right">Ledtid</th></tr></thead><tbody>${rows.map(r=>`<tr class="clickable ${r.active?"":"archived"}" onclick="event.stopPropagation();openInfoRecord(${r.id})">
    <td><strong>${esc(r.component_code)}</strong> · ${esc(r.component_name)}<div class="small muted">${esc(r.component_category||"")}</div></td>
    <td>${esc(r.supplier_material_no||"—")}</td><td>${priceUnitText(r)}</td><td>${infoStatusBadge(r.source_status)}</td>
    <td class="t-right">${r.effective_lead_time_days!==null&&r.effective_lead_time_days!==undefined?fmt(r.effective_lead_time_days,0)+" d":"—"}</td>
  </tr>`).join("")}</tbody></table>`;
}

async function openSupplier(no){
  const s=await api(`/api/suppliers/${no}`);
  openDrawer(`${s.supplier_no} — ${s.name}`,s.active?"Leverantörsmaster":"Arkiverad leverantör",`
    <div class="detail-grid">
      <div class="detail-item"><div class="k">Org.nr</div><div class="v">${esc(s.org_no||"—")}</div></div>
      <div class="detail-item"><div class="k">Kontakt</div><div class="v">${esc(s.contact_name||"—")}</div></div>
      <div class="detail-item"><div class="k">E-post</div><div class="v">${esc(s.email||"—")}</div></div>
      <div class="detail-item"><div class="k">Telefon</div><div class="v">${esc(s.phone||"—")}</div></div>
      <div class="detail-item"><div class="k">Standard ledtid</div><div class="v">${s.default_lead_time_days!==null&&s.default_lead_time_days!==undefined?fmt(s.default_lead_time_days,0)+" dagar":"—"}</div></div>
      <div class="detail-item"><div class="k">Valuta</div><div class="v">${esc(s.currency||"SEK")}</div></div>
      <div class="detail-item"><div class="k">Betalningsvillkor</div><div class="v">${esc(s.payment_terms||"—")}</div></div>
      <div class="detail-item"><div class="k">Leveransvillkor</div><div class="v">${esc(s.delivery_terms||"—")}</div></div>
      <div class="detail-item"><div class="k">Minsta ordervärde</div><div class="v">${s.min_order_value!==null&&s.min_order_value!==undefined?fmt(s.min_order_value,2)+" "+esc(s.currency||"SEK"):"—"}</div></div>
    </div>
    <div class="detail-item" style="margin:14px 0"><div class="k">Adress</div><div class="v">${esc(s.address||"—")} ${esc(s.postal_address||"")}</div></div>
    <div class="detail-item" style="margin:0 0 14px"><div class="k">Anteckningar</div><div class="v">${esc(s.notes||"—")}</div></div>
    <div class="row-actions" style="justify-content:flex-start;margin:0 0 14px">
      <button class="btn" onclick="closeDrawer();openSupplierEditor(${s.supplier_no})">Redigera leverantör</button>
      ${s.active?`<button class="btn" onclick="closeDrawer();openInfoRecordEditor(null,${s.supplier_no})">＋ Info record</button>`:""}
      <button class="btn primary" onclick="closeDrawer();openPurchaseHistoryEditor(${s.supplier_no})">＋ Registrera inköp</button>
      ${s.active?`<button class="btn danger-soft" onclick="archiveSupplier(${s.supplier_no})">Arkivera</button>`:""}
    </div>
    <div class="card" style="box-shadow:none"><div class="card-title"><h3>Material / info records</h3><span class="small muted">${(s.info_records||[]).length} kopplingar</span></div>
      <div class="card-body">${supplierInfoRecordsHtml(s)}</div></div>
    <div class="card" style="box-shadow:none;margin-top:14px"><div class="card-title"><h3>Senaste faktiska inköp</h3><span class="small muted">${(s.purchase_history||[]).length} senaste</span></div>
      <div class="card-body">${purchaseHistoryMiniTable(s.purchase_history||[],true,false)}</div></div>`);
}

async function openSupplierEditor(no=null){
  let s={supplier_no:"",name:"",org_no:"",contact_name:"",email:"",phone:"",address:"",postal_address:"",payment_terms:"",delivery_terms:"",default_lead_time_days:null,min_order_value:null,currency:"SEK",notes:"",active:1};
  if(no!==null) s=await api(`/api/suppliers/${no}`);
  const editing=no!==null;
  openModal(editing?`Redigera leverantör ${s.supplier_no}`:"Ny leverantör",editing?"Ändringar sparas direkt i ERP-databasen.":"Skapa leverantören som ERP-native masterdata.",`
    <form id="supplierEditorForm" onsubmit="saveSupplierEditor(event,${editing?s.supplier_no:'null'})">
      <div class="editor-grid">
        <div class="field"><label>Leverantörsnummer</label><input id="supplierNoEditor" name="supplier_no" type="number" min="1" required value="${esc(s.supplier_no)}" ${editing?"readonly":''} ${editing?'':'oninput="checkSupplierNumberAvailability(this.value)"'}><div id="supplierNoConflict" class="field-error" aria-live="polite"></div></div>
        <div class="field span2"><label>Leverantörsnamn</label><input name="name" required value="${esc(s.name||"")}"></div>
        <div class="field"><label>Org.nr</label><input name="org_no" value="${esc(s.org_no||"")}"></div>
        <div class="field"><label>Kontaktperson</label><input name="contact_name" value="${esc(s.contact_name||"")}"></div>
        <div class="field"><label>Telefon</label><input name="phone" value="${esc(s.phone||"")}"></div>
        <div class="field span2"><label>E-post</label><input name="email" type="email" value="${esc(s.email||"")}"></div>
        <div class="field"><label>Valuta</label><input name="currency" maxlength="3" value="${esc(s.currency||"SEK")}"></div>
        <div class="field span2"><label>Adress</label><input name="address" value="${esc(s.address||"")}"></div>
        <div class="field"><label>Postadress</label><input name="postal_address" value="${esc(s.postal_address||"")}"></div>
        <div class="field"><label>Betalningsvillkor</label><input name="payment_terms" placeholder="t.ex. 30 dagar" value="${esc(s.payment_terms||"")}"></div>
        <div class="field"><label>Leveransvillkor</label><input name="delivery_terms" value="${esc(s.delivery_terms||"")}"></div>
        <div class="field"><label>Standard ledtid (dagar)</label><input name="default_lead_time_days" type="number" step="any" min="0" value="${s.default_lead_time_days??""}"></div>
        <div class="field"><label>Minsta ordervärde</label><input name="min_order_value" type="number" step="any" min="0" value="${s.min_order_value??""}"></div>
        <div class="field span3"><label>Anteckningar</label><textarea name="notes" rows="4">${esc(s.notes||"")}</textarea></div>
        <div class="field span3"><label class="check"><input name="active" type="checkbox" ${s.active?"checked":""}> Aktiv leverantör</label></div>
      </div>
      <div class="editor-actions"><div class="status-note">Info records kopplar leverantören till BOM-material.</div><div class="right"><button type="button" class="btn" onclick="closeModal()">Avbryt</button><button class="btn primary" type="submit">SPARA LEVERANTÖR</button></div></div>
    </form>`);
}

async function checkSupplierNumberAvailability(value){
  const box=$("supplierNoConflict"),input=$("supplierNoEditor");
  if(!box||!input) return;
  box.textContent="";input.classList.remove("input-error");
  const no=Number(value); if(!no) return;
  try{
    const s=await api(`/api/suppliers/${no}`);
    box.textContent=`Leverantörsnummer redan i bruk — ${s.name} (${s.active?"aktiv":"arkiverad"}).`;
    input.classList.add("input-error");
  }catch(err){ if(!String(err.message||"").includes("404")){} }
}

async function saveSupplierEditor(e,existingNo){
  e.preventDefault();const f=new FormData(e.currentTarget);
  const payload={
    supplier_no:Number(f.get("supplier_no")),name:String(f.get("name")||"").trim(),org_no:String(f.get("org_no")||""),
    contact_name:String(f.get("contact_name")||""),email:String(f.get("email")||""),phone:String(f.get("phone")||""),
    address:String(f.get("address")||""),postal_address:String(f.get("postal_address")||""),payment_terms:String(f.get("payment_terms")||""),
    delivery_terms:String(f.get("delivery_terms")||""),default_lead_time_days:numberOrNull(f.get("default_lead_time_days")),
    min_order_value:numberOrNull(f.get("min_order_value")),currency:String(f.get("currency")||"SEK").toUpperCase(),notes:String(f.get("notes")||""),active:f.get("active")!==null
  };
  try{
    await api(existingNo===null?"/api/suppliers":`/api/suppliers/${existingNo}`,{method:existingNo===null?"POST":"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
    closeModal();await loadPurchasingPage();showToast(existingNo===null?`Leverantör ${payload.supplier_no} skapad.`:`Leverantör ${payload.supplier_no} uppdaterad.`);
  }catch(err){
    const msg=String(err.message||"");const box=$("supplierNoConflict"),input=$("supplierNoEditor");
    if(existingNo===null&&box&&input&&(msg.includes("Leverantörsnummer")||msg.includes("409"))){box.textContent=msg;input.classList.add("input-error");input.focus();}
    else showToast("Kunde inte spara leverantör: "+msg);
  }
}

async function archiveSupplier(no){
  if(!confirm(`Arkivera leverantör ${no}? Historik och info records kommer att bevaras.`)) return;
  try{await api(`/api/suppliers/${no}`,{method:"DELETE"});closeDrawer();await loadPurchasingPage();showToast(`Leverantör ${no} arkiverad.`);}catch(err){showToast(err.message)}
}

async function loadInfoRecordsPage(){
  const q=$("infoRecordSearch")?.value||"";
  const status=$("infoRecordStatus")?.value||"all";
  const inactive=$("showArchivedInfoRecords")?.checked?"true":"false";
  try{
    const list=await api(`/api/purchasing/info-records?limit=5000&include_inactive=${inactive}&source_status=${encodeURIComponent(status)}&search=${encodeURIComponent(q)}`);
    renderInfoRecordTable(list);
  }catch(err){ if($("infoRecordTable")) $("infoRecordTable").innerHTML=`<div class="empty">${esc(err.message)}</div>`; }
}

function renderInfoRecordTable(list){
  if(!$("infoRecordTable")) return;
  $("infoRecordTable").innerHTML=list.length?`<table><thead><tr>
    <th>Material</th><th>Leverantör</th><th>Lev. art.nr</th><th>Pris / prisenhet</th><th>Källstatus</th><th class="t-right">MOQ</th><th class="t-right">Ledtid</th><th>Giltighet</th>
  </tr></thead><tbody>${list.map(r=>`<tr class="clickable ${r.active?"":"archived"}" onclick="openInfoRecord(${r.id})">
    <td><strong>${esc(r.component_code)}</strong> · ${esc(r.component_name)}<div class="small muted">${esc(r.component_category||"")}</div></td>
    <td><strong>${r.supplier_no}</strong> · ${esc(r.supplier_name)}${r.supplier_active?"":`<div class="small danger-text">Leverantör arkiverad</div>`}</td>
    <td>${esc(r.supplier_material_no||"—")}</td><td>${priceUnitText(r)}</td><td>${infoStatusBadge(r.source_status)}${r.active?"":` ${badge("Arkiverad","gray")}`}</td>
    <td class="t-right">${r.min_order_qty!==null&&r.min_order_qty!==undefined?fmt(r.min_order_qty,2):"—"}</td>
    <td class="t-right">${r.effective_lead_time_days!==null&&r.effective_lead_time_days!==undefined?fmt(r.effective_lead_time_days,0)+" d":"—"}${r.lead_time_source==="SUPPLIER_DEFAULT"?`<div class="small muted">leverantör</div>`:""}</td>
    <td>${purchasingDateRange(r)}</td>
  </tr>`).join("")}</tbody></table>`:`<div class="empty">Inga info records matchar sökningen.<br><span class="small">Skapa en leverantör ↔ material-koppling med “Nytt info record”.</span></div>`;
}

async function openInfoRecord(id){
  const r=await api(`/api/purchasing/info-records/${id}`);
  const eventHtml=(r.events||[]).length?(r.events||[]).slice(0,8).map(e=>`<div class="info-event"><strong>${esc(e.action)}</strong><span>${esc(e.message)}</span><span class="small muted">${esc(e.created_at)}</span></div>`).join(""):`<div class="small muted">Ingen ändringshistorik ännu.</div>`;
  openDrawer(`Info record ${r.id}`,`${r.supplier_no} ${r.supplier_name} ↔ ${r.component_code} ${r.component_name}`,`
    <div class="source-relationship"><div><div class="eyebrow">LEVERANTÖR</div><strong>${r.supplier_no} · ${esc(r.supplier_name)}</strong></div><div class="source-arrow">↔</div><div><div class="eyebrow">MATERIAL</div><strong>${esc(r.component_code)} · ${esc(r.component_name)}</strong><div class="small muted">${esc(r.component_category||"")}</div></div></div>
    <div class="detail-grid" style="margin-top:16px">
      <div class="detail-item"><div class="k">Leverantörens art.nr</div><div class="v">${esc(r.supplier_material_no||"—")}</div></div>
      <div class="detail-item"><div class="k">Inköpspris</div><div class="v">${priceUnitText(r)}</div></div>
      <div class="detail-item"><div class="k">Källstatus</div><div class="v">${infoStatusBadge(r.source_status)}</div></div>
      <div class="detail-item"><div class="k">Ledtid</div><div class="v">${r.effective_lead_time_days!==null&&r.effective_lead_time_days!==undefined?fmt(r.effective_lead_time_days,0)+" dagar":"—"}${r.lead_time_days===null||r.lead_time_days===undefined?` <span class="small muted">(leverantörens standard)</span>`:""}</div></div>
      <div class="detail-item"><div class="k">MOQ</div><div class="v">${r.min_order_qty!==null&&r.min_order_qty!==undefined?fmt(r.min_order_qty,2):"—"}</div></div>
      <div class="detail-item"><div class="k">Ordermultipel</div><div class="v">${r.order_multiple_qty!==null&&r.order_multiple_qty!==undefined?fmt(r.order_multiple_qty,2):"—"}</div></div>
      <div class="detail-item"><div class="k">Giltighet</div><div class="v">${purchasingDateRange(r)}</div></div>
      <div class="detail-item"><div class="k">Status</div><div class="v">${r.active?badge("Aktiv","green"):badge("Arkiverad","gray")}</div></div>
    </div>
    <div class="detail-item" style="margin:14px 0"><div class="k">Anteckningar</div><div class="v">${esc(r.notes||"—")}</div></div>
    <div class="row-actions" style="justify-content:flex-start;margin-bottom:16px"><button class="btn" onclick="closeDrawer();openInfoRecordEditor(${r.id})">Redigera</button><button class="btn primary" onclick='closeDrawer();openPurchaseHistoryEditor(${r.supplier_no},${JSON.stringify(String(r.component_code))},${r.id})'>＋ Registrera inköp</button>${r.active?`<button class="btn danger-soft" onclick="archiveInfoRecord(${r.id})">Arkivera</button>`:""}</div>
    <div class="card" style="box-shadow:none;margin-bottom:14px"><div class="card-title"><h3>Faktisk inköpshistorik</h3><span class="small muted">${(r.purchase_history||[]).length} senaste</span></div><div class="card-body">${purchaseHistoryMiniTable((r.purchase_history||[]).map(x=>({...x,supplier_no:r.supplier_no,supplier_name:r.supplier_name,component_name:r.component_name,component_unit:r.component_unit})),false,false)}</div></div>
    <div class="card" style="box-shadow:none"><div class="card-title"><h3>Masterdatahistorik</h3><span class="small muted">ändringar i info record</span></div><div class="card-body info-events">${eventHtml}</div></div>`);
}

async function openInfoRecordEditor(id=null,prefillSupplier=null,prefillComponent=""){
  const [suppliers,materials,current]=await Promise.all([
    api('/api/suppliers?limit=3000&include_inactive=true'),
    api('/api/purchasing/materials?limit=2000'),
    id?api(`/api/purchasing/info-records/${id}`):Promise.resolve(null)
  ]);
  let r=current||{supplier_no:prefillSupplier||"",component_code:prefillComponent||"",supplier_material_no:"",purchase_price:null,currency:"SEK",price_unit_qty:1,purchase_unit:"",min_order_qty:null,order_multiple_qty:null,lead_time_days:null,source_status:"APPROVED",valid_from:"",valid_to:"",notes:"",active:1};
  if(!current&&prefillSupplier){const s=suppliers.find(x=>x.supplier_no===Number(prefillSupplier));if(s&&s.currency)r.currency=s.currency;}
  const supplierOptions=suppliers.map(s=>`<option value="${s.supplier_no}" ${Number(r.supplier_no)===Number(s.supplier_no)?"selected":""} ${!s.active&&!current?"disabled":""}>${s.supplier_no} — ${esc(s.name)}${s.active?"":" (arkiverad)"}</option>`).join("");
  const materialOptions=materials.map(m=>`<option value="${esc(m.code)}" ${String(r.component_code)===String(m.code)?"selected":""}>${esc(m.code)} — ${esc(m.name)}${m.category?` · ${esc(m.category)}`:""}</option>`).join("");
  openModal(id?`Redigera info record ${id}`:"Nytt info record",id?"Ändra inköpskällans masterdata.":"Koppla ett BOM-material till en leverantör.",`
    <form id="infoRecordEditorForm" onsubmit="saveInfoRecord(event,${id||'null'})">
      <div class="editor-grid">
        <div class="field span2"><label>Leverantör</label><select name="supplier_no" required><option value="">Välj leverantör…</option>${supplierOptions}</select></div>
        <div class="field span2"><label>Material</label><select name="component_code" required><option value="">Välj material…</option>${materialOptions}</select></div>
        <div class="field span2"><label>Leverantörens artikelnummer</label><input name="supplier_material_no" value="${esc(r.supplier_material_no||"")}"></div>
        <div class="field"><label>Inköpspris</label><input name="purchase_price" type="number" min="0" step="any" value="${r.purchase_price??""}"></div>
        <div class="field"><label>Valuta</label><input name="currency" maxlength="3" value="${esc(r.currency||"SEK")}"></div>
        <div class="field"><label>Prisenhet</label><input name="price_unit_qty" type="number" min="0.000001" step="any" required value="${r.price_unit_qty??1}"><div class="small muted">t.ex. 1000 om priset är per 1000 st</div></div>
        <div class="field"><label>Inköpsenhet</label><input name="purchase_unit" placeholder="st, kg, rulle…" value="${esc(r.purchase_unit||"")}"></div>
        <div class="field"><label>MOQ</label><input name="min_order_qty" type="number" min="0" step="any" value="${r.min_order_qty??""}"></div>
        <div class="field"><label>Ordermultipel</label><input name="order_multiple_qty" type="number" min="0" step="any" value="${r.order_multiple_qty??""}"></div>
        <div class="field"><label>Ledtid (dagar)</label><input name="lead_time_days" type="number" min="0" step="any" value="${r.lead_time_days??""}"><div class="small muted">Tomt = leverantörens standard</div></div>
        <div class="field"><label>Källstatus</label><select name="source_status"><option value="PREFERRED" ${r.source_status==="PREFERRED"?"selected":""}>Föredragen</option><option value="APPROVED" ${r.source_status==="APPROVED"?"selected":""}>Godkänd</option><option value="BACKUP" ${r.source_status==="BACKUP"?"selected":""}>Backup</option><option value="BLOCKED" ${r.source_status==="BLOCKED"?"selected":""}>Spärrad</option></select></div>
        <div class="field"><label>Giltig från</label><input name="valid_from" type="date" value="${esc(r.valid_from||"")}"></div>
        <div class="field"><label>Giltig till</label><input name="valid_to" type="date" value="${esc(r.valid_to||"")}"></div>
        <div class="field span3"><label>Anteckningar</label><textarea name="notes" rows="4">${esc(r.notes||"")}</textarea></div>
        <div class="field span3"><label class="check"><input name="active" type="checkbox" ${r.active?"checked":""}> Aktivt info record</label></div>
        <div id="infoRecordConflict" class="field-error span3" aria-live="polite"></div>
      </div>
      <div class="editor-actions"><div class="status-note">Ett info record per leverantör + material. Prisändringar sparas i masterdatahistoriken.</div><div class="right"><button type="button" class="btn" onclick="closeModal()">Avbryt</button><button class="btn primary" type="submit">SPARA INFO RECORD</button></div></div>
    </form>`);
}

async function saveInfoRecord(e,id){
  e.preventDefault();const f=new FormData(e.currentTarget);
  const payload={supplier_no:Number(f.get("supplier_no")),component_code:String(f.get("component_code")||""),supplier_material_no:String(f.get("supplier_material_no")||""),purchase_price:numberOrNull(f.get("purchase_price")),currency:String(f.get("currency")||"SEK").toUpperCase(),price_unit_qty:Number(f.get("price_unit_qty")||1),purchase_unit:String(f.get("purchase_unit")||""),min_order_qty:numberOrNull(f.get("min_order_qty")),order_multiple_qty:numberOrNull(f.get("order_multiple_qty")),lead_time_days:numberOrNull(f.get("lead_time_days")),source_status:String(f.get("source_status")||"APPROVED"),valid_from:String(f.get("valid_from")||""),valid_to:String(f.get("valid_to")||""),notes:String(f.get("notes")||""),active:f.get("active")!==null};
  try{
    await api(id?`/api/purchasing/info-records/${id}`:'/api/purchasing/info-records',{method:id?'PUT':'POST',headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
    closeModal();purchasingTab="INFO";setPurchasingTab("INFO");showToast(id?"Info record uppdaterat.":"Info record skapat.");
  }catch(err){const box=$("infoRecordConflict");if(box)box.textContent=String(err.message||err);else showToast(err.message);}
}

async function archiveInfoRecord(id){
  if(!confirm("Arkivera info recordet? Leverantörs-/materialhistoriken bevaras.")) return;
  try{await api(`/api/purchasing/info-records/${id}`,{method:"DELETE"});closeDrawer();await loadInfoRecordsPage();showToast("Info record arkiverat.");}catch(err){showToast(err.message)}
}


// ---------------- Purchasing history (v0.7.8) ----------------
function clearPurchaseHistoryFilters(){
  ["purchaseHistoryMaterialSearch","purchaseHistorySupplierSearch","purchaseHistoryReferenceSearch","purchaseHistoryFrom","purchaseHistoryTo"].forEach(id=>{if($(id)) $(id).value="";});
  loadPurchaseHistoryPage();
}
async function loadPurchaseHistoryPage(){
  if(!$("purchaseHistoryTable")) return;
  const p=new URLSearchParams({
    material_search:$("purchaseHistoryMaterialSearch")?.value||"",
    supplier_search:$("purchaseHistorySupplierSearch")?.value||"",
    reference_search:$("purchaseHistoryReferenceSearch")?.value||"",
    date_from:$("purchaseHistoryFrom")?.value||"",
    date_to:$("purchaseHistoryTo")?.value||"",
    limit:5000
  });
  try{
    const [rows,summary]=await Promise.all([api(`/api/purchasing/history?${p}`),api(`/api/purchasing/history-summary?${p}`)]);
    renderPurchaseHistorySummary(summary);
    renderPurchaseHistoryTable(rows);
  }catch(err){$("purchaseHistoryTable").innerHTML=`<div class="empty">${esc(err.message)}</div>`;}
}
function renderPurchaseHistorySummary(s){
  const last=s.last_purchase;
  $("purchaseHistoryKpis").innerHTML=`
    <div class="kpi"><div class="kpi-label">Historiska inköp</div><div class="kpi-value">${s.purchase_count||0}</div></div>
    <div class="kpi"><div class="kpi-label">Leverantörer</div><div class="kpi-value">${s.supplier_count||0}</div></div>
    <div class="kpi"><div class="kpi-label">Material</div><div class="kpi-value">${s.material_count||0}</div></div>
    <div class="kpi"><div class="kpi-label">Senaste köp</div><div class="kpi-value" style="font-size:18px">${last?fmtDate(last.purchase_date):"—"}</div><div class="small muted">${last?`${last.supplier_no} · ${esc(last.supplier_name)} · ${esc(last.component_code)}`:"Ingen historik ännu"}</div></div>`;
}
function renderPurchaseHistoryTable(rows){
  $("purchaseHistoryTable").innerHTML=rows.length?`<table><thead><tr><th>Datum</th><th>Material</th><th>Leverantör</th><th class="t-right">Kvantitet</th><th>Inköpspris</th><th>PO / referens</th><th>Källa</th></tr></thead><tbody>${rows.map(r=>`<tr class="clickable" onclick="openPurchaseHistory(${r.id})"><td><strong>${fmtDate(r.purchase_date)}</strong></td><td><strong>${esc(r.component_code)}</strong> · ${esc(r.component_name)}<div class="small muted">${esc(r.component_category||"")}</div></td><td><strong>${r.supplier_no}</strong> · ${esc(r.supplier_name)}</td><td class="t-right">${r.quantity!==null&&r.quantity!==undefined?fmt(r.quantity,2)+(r.purchase_unit?" "+esc(r.purchase_unit):""):"—"}</td><td>${purchaseHistoryPriceText(r)}</td><td>${esc(r.external_reference||"—")}</td><td>${r.source==="PO"?badge("PO","green"):badge("Manuell historik","blue")}</td></tr>`).join("")}</tbody></table>`:`<div class="empty">Ingen inköpshistorik matchar filtret.<br><span class="small">Registrera ett tidigare faktiskt inköp, eller låt framtida purchase orders skapa historiken automatiskt.</span></div>`;
}
async function openPurchaseHistory(id){
  const r=await api(`/api/purchasing/history/${id}`);
  openDrawer(`Inköp ${fmtDate(r.purchase_date)}`,`${r.component_code} ${r.component_name} · ${r.supplier_no} ${r.supplier_name}`,`
    <div class="source-relationship"><div><div class="eyebrow">LEVERANTÖR</div><strong>${r.supplier_no} · ${esc(r.supplier_name)}</strong></div><div class="source-arrow">→</div><div><div class="eyebrow">MATERIAL</div><strong>${esc(r.component_code)} · ${esc(r.component_name)}</strong><div class="small muted">${esc(r.component_category||"")}</div></div></div>
    <div class="detail-grid" style="margin-top:16px">
      <div class="detail-item"><div class="k">Inköpsdatum</div><div class="v">${fmtDate(r.purchase_date)}</div></div>
      <div class="detail-item"><div class="k">Kvantitet</div><div class="v">${r.quantity!==null&&r.quantity!==undefined?fmt(r.quantity,2)+(r.purchase_unit?" "+esc(r.purchase_unit):""):"—"}</div></div>
      <div class="detail-item"><div class="k">Inköpspris</div><div class="v">${purchaseHistoryPriceText(r)}</div></div>
      <div class="detail-item"><div class="k">Leverantörens art.nr</div><div class="v">${esc(r.supplier_material_no||"—")}</div></div>
      <div class="detail-item"><div class="k">PO / referens</div><div class="v">${esc(r.external_reference||"—")}</div></div>
      <div class="detail-item"><div class="k">Källa</div><div class="v">${r.source==="PO"?badge("ERP purchase order","green"):badge("Manuellt registrerad","blue")}</div></div>
      <div class="detail-item"><div class="k">Registrerad av</div><div class="v">${esc(r.created_by||"—")}</div></div>
      <div class="detail-item"><div class="k">Registrerad</div><div class="v">${esc(r.created_at||"—")}</div></div>
    </div>
    <div class="detail-item" style="margin-top:14px"><div class="k">Anteckningar</div><div class="v">${esc(r.notes||"—")}</div></div>
    <div class="notice blue" style="margin-top:16px"><strong>Snapshot:</strong> inköpshistoriken behåller datum, pris, kvantitet och leverantör som de registrerades. Ändringar i ett info record skriver inte om gamla inköp.</div>`);
}
async function openPurchaseHistoryEditor(prefillSupplier=null,prefillComponent="",prefillInfoRecord=null){
  const [suppliers,materials,ir]=await Promise.all([
    api('/api/suppliers?limit=3000&include_inactive=true'),
    api('/api/purchasing/materials?limit=2000'),
    prefillInfoRecord?api(`/api/purchasing/info-records/${prefillInfoRecord}`):Promise.resolve(null)
  ]);
  const supplierNo=Number(prefillSupplier||ir?.supplier_no||0)||"";
  const componentCode=String(prefillComponent||ir?.component_code||"");
  const supplierOptions=suppliers.map(s=>`<option value="${s.supplier_no}" ${Number(supplierNo)===Number(s.supplier_no)?"selected":""}>${s.supplier_no} — ${esc(s.name)}${s.active?"":" (arkiverad)"}</option>`).join("");
  const materialOptions=materials.map(m=>`<option value="${esc(m.code)}" ${String(componentCode)===String(m.code)?"selected":""}>${esc(m.code)} — ${esc(m.name)}${m.category?` · ${esc(m.category)}`:""}</option>`).join("");
  const today=new Date().toLocaleDateString('sv-SE');
  openModal("Registrera tidigare inköp","Faktiskt inköp / historikpost — inte ett info-recordpris",`
    <form id="purchaseHistoryEditorForm" onsubmit="savePurchaseHistory(event)">
      <div class="editor-grid">
        <div class="field span2"><label>Leverantör</label><select name="supplier_no" required><option value="">Välj leverantör…</option>${supplierOptions}</select></div>
        <div class="field span2"><label>Material</label><select name="component_code" required><option value="">Välj material…</option>${materialOptions}</select></div>
        <div class="field"><label>Inköpsdatum</label><input name="purchase_date" type="date" required value="${today}"></div>
        <div class="field"><label>Kvantitet</label><input name="quantity" type="number" min="0.000001" step="any"></div>
        <div class="field"><label>Inköpsenhet</label><input name="purchase_unit" placeholder="st, kg, rulle…" value="${esc(ir?.purchase_unit||"")}"></div>
        <div class="field"><label>Faktiskt inköpspris</label><input name="purchase_price" type="number" min="0" step="any" value="${ir?.purchase_price??""}"></div>
        <div class="field"><label>Valuta</label><input name="currency" maxlength="3" value="${esc(ir?.currency||"SEK")}"></div>
        <div class="field"><label>Prisenhet</label><input name="price_unit_qty" type="number" min="0.000001" step="any" required value="${ir?.price_unit_qty??1}"><div class="small muted">t.ex. 1000 om priset var per 1000 st</div></div>
        <div class="field span2"><label>Leverantörens artikelnummer</label><input name="supplier_material_no" value="${esc(ir?.supplier_material_no||"")}"></div>
        <div class="field span2"><label>PO / referens</label><input name="external_reference" placeholder="t.ex. gammalt PO-nummer, orderbekräftelse…"></div>
        <div class="field span3"><label>Anteckningar</label><textarea name="notes" rows="3" placeholder="Valfritt — t.ex. akutköp eller källa till historiken"></textarea></div>
      </div>
      <div class="editor-actions"><div class="status-note">Den här posten är en historisk snapshot. Framtida ERP-PO kommer att skapa samma typ av historik automatiskt.</div><div class="right"><button type="button" class="btn" onclick="closeModal()">Avbryt</button><button class="btn primary" type="submit">SPARA INKÖP</button></div></div>
    </form>`);
}
async function savePurchaseHistory(e){
  e.preventDefault(); const f=new FormData(e.currentTarget);
  const payload={supplier_no:Number(f.get("supplier_no")),component_code:String(f.get("component_code")||""),purchase_date:String(f.get("purchase_date")||""),quantity:numberOrNull(f.get("quantity")),purchase_unit:String(f.get("purchase_unit")||""),purchase_price:numberOrNull(f.get("purchase_price")),currency:String(f.get("currency")||"SEK").toUpperCase(),price_unit_qty:Number(f.get("price_unit_qty")||1),supplier_material_no:String(f.get("supplier_material_no")||""),external_reference:String(f.get("external_reference")||""),notes:String(f.get("notes")||"")};
  try{await api('/api/purchasing/history',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});closeModal();purchasingTab="HISTORY";setPurchasingTab("HISTORY");showToast("Inköpshistorik registrerad.");}catch(err){showToast(err.message);}
}

// ---------------- Production ----------------
async function loadProduction(){
  const d=await api("/api/production");
  const summaries=[1,2,3,4].map(s=>d.station_summary[String(s)]||{orders:0,pallets:0,ready_orders:0});
  $("productionKpis").innerHTML=summaries.map((x,i)=>`<div class="kpi"><div class="kpi-label">Station ${i+1}</div><div class="kpi-value">${fmt(x.pallets,0)} pall</div><div class="kpi-sub">${fmt(x.orders,0)} order · ${fmt(x.ready_orders,0)} READY</div></div>`).join("");
  const labels={OVERDUE:"Försenat",TODAY:"Idag",TOMORROW:"Imorgon",LATER:"Senare"};
  const keys=["OVERDUE","TODAY","TOMORROW","LATER"];
  const groups=keys.map(k=>[k,d.orders.filter(o=>o.bucket===k)]).filter(([,rows])=>rows.length);
  $("productionOrders").innerHTML=groups.length?groups.map(([k,rows])=>`<div class="prod-day-group"><div class="prod-day-title"><div><strong>${labels[k]}</strong><span>${rows.length} order</span></div></div>${rows.map(o=>`<div class="prod-order"><div class="prod-head"><div><div class="prod-order-title"><strong>Order ${o.order_no} — ${esc(o.customer_name)}</strong><span class="source-pill ${o.source==='erp_v2'?'erp':'excel'}">${o.source==='erp_v2'?'ERP v2':(o.source==='excel_current'?'Excel aktuell':'Excel')}</span></div><div class="small muted">${fmtDate(o.delivery_date)} · ${weekdaySv(o.delivery_date)} · kund ${esc(o.customer_no)} · ${fmt(o.total_kg,0)} kg</div></div>${o.all_ready?badge("HELA ORDERN READY","green"):badge("Väntar","amber")}</div><div class="prod-stations">${o.stations.map(s=>`<button class="station-btn ${s.ready?"ready":""}" onclick="toggleStation(${o.id},${s.station_id})" title="Rå beräkning ${fmt(s.raw_pallets??s.required_pallets,2)} pall"><span>S${s.station_id}</span><strong>${fmt(s.required_pallets,0)} pall</strong>${s.ready?" ✓":""}</button>`).join("")}</div></div>`).join("")}</div>`).join(""):`<div class="card"><div class="empty">Inga produktionsorder inom perioden.</div></div>`;
  $("productionEvents").innerHTML=d.events.length?d.events.map(a=>`<div class="activity"><div class="activity-icon">${a.event_type==="ORDER_READY"?"✓":"•"}</div><div><div class="strong">${a.order_no?`#${a.order_no} · `:""}${esc(a.customer_name||"")}</div><div class="small">${esc(a.message)}</div><div class="time">${esc(a.created_at)}</div></div></div>`).join(""):`<div class="empty">Inga händelser.</div>`;
}

async function toggleStation(orderId,stationId){
  try{ await api(`/api/production/${orderId}/station/${stationId}/toggle`,{method:"POST"}); await loadProduction(); }
  catch(err){showToast(err.message)}
}

// ---------------- Transport ----------------
async function loadTransport(){
  const rows=await api("/api/transport");
  $("transportTable").innerHTML=rows.length?`<table><thead><tr><th>Order</th><th>Kund</th><th>Leverans</th><th>Åkeri</th><th class="t-right">Kg</th><th class="t-right">Pall</th><th class="t-right">PPL</th><th>Status</th></tr></thead><tbody>
    ${rows.map(r=>`<tr><td><strong>${r.order_no}</strong></td><td>${esc(r.customer_name)}</td><td>${fmtDate(r.delivery_date)}</td><td>${esc(r.carrier||"—")}</td>
      <td class="t-right">${fmt(r.total_kg,0)}</td><td class="t-right">${fmt(r.pallets,2)}</td><td class="t-right">${fmt(r.ppl,2)}</td>
      <td><select class="transport-status" onchange="setTransportStatus(${r.id},this.value)">
        ${["Ej bokad","Planerad","Bokad manuellt"].map(s=>`<option ${r.status===s?"selected":""}>${s}</option>`).join("")}
      </select></td></tr>`).join("")}</tbody></table>`:`<div class="empty">Inga ERP v2-order ännu.</div>`;
}
async function setTransportStatus(orderId,status){
  try{await api(`/api/transport/${orderId}`,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({status})});showToast("Transportstatus uppdaterad.")}
  catch(err){showToast(err.message)}
}

// ---------------- Labels ----------------
async function searchLabelOrders(){
  const q=$("labelOrderSearch").value||"";
  const d=await api("/api/orders?limit=40&search="+encodeURIComponent(q));
  $("labelOrderResults").innerHTML=d.rows.length?`<table><tbody>${d.rows.map(o=>`<tr class="clickable" onclick="loadLabels(${o.id})">
    <td><strong>${o.order_no}</strong></td><td>${esc(o.customer_name)}</td><td>${fmtDate(o.delivery_date)}</td><td class="t-right"><button class="btn">Etiketter</button></td></tr>`).join("")}</tbody></table>`:`<div class="small muted">Ingen order.</div>`;
}
async function loadLabels(orderId){
  state.currentLabelOrder=orderId;
  let d=await api(`/api/labels/${orderId}`);
  if(!d.labels.length && d.line_count){
    try{ await api(`/api/labels/${orderId}/generate`,{method:"POST"}); d=await api(`/api/labels/${orderId}`); }
    catch(err){ showToast(err.message); }
  }
  const btn=$("printLabelsBtn"); if(btn) btn.disabled=!d.labels.length;
  const counts=d.labels.reduce((a,x)=>(a[x.template_name]=(a[x.template_name]||0)+1,a),{});
  const templateSummary=Object.entries(counts).map(([k,v])=>`${k}: ${v}`).join(" · ");
  $("labelPreview").innerHTML=`<div class="card label-preview-card"><div class="card-title"><div><h2>Order ${d.order.order_no} — ${esc(d.order.customer_name)}</h2><div class="small muted">${d.labels.length} fysiska pallar · ${esc(templateSummary||"inga etiketter")}</div></div><div class="row-actions no-print"><button class="btn" onclick="regenerateLabels(${orderId})">Uppdatera från order</button></div></div>
    <div class="card-body"><div class="label-sheet">${d.labels.length?d.labels.map(j=>`<div class="label-card pallet-label template-${esc((j.template_name||"STANDARD").toLowerCase().replaceAll("ö","o"))}">
      <div class="label-top"><div><div class="customer">${esc(d.order.customer_name)}</div><div class="label-order-meta"><span>OrderNr: <strong>${d.order.order_no}</strong></span><span>Datum: <strong>${fmtDate(d.order.delivery_date)}</strong></span>${d.order.loading_time?`<span>Lastning: <strong>${esc(d.order.loading_time)}</strong></span>`:""}</div></div></div>
      <div class="label-main"><div><div class="article">${esc(j.article_name||"")}</div><div class="label-qty"><strong>${fmt(j.qty_on_label,0)} av ${fmt(j.total_qty,0)}</strong> &nbsp;: &nbsp; Pall ${j.sequence_no} av ${j.total_sequences}</div><div class="small">Art nr: A${j.article_no}${j.station_id?` &nbsp;&nbsp; Nr: ${j.station_id}`:""}</div><div class="label-uid">${esc(j.label_uid)}</div><div class="no-print" style="margin-top:8px">${badge(j.lifecycle_state||"CREATED",j.lifecycle_state==="PRODUCED"?"green":j.lifecycle_state==="PRINTED"?"blue":"gray")}</div></div><img class="label-qr" src="/api/labels/qr/${encodeURIComponent(j.label_uid)}" alt="QR ${esc(j.label_uid)}"></div>
    </div>`).join(""):`<div class="empty">Inga palletiketter kunde skapas. Kontrollera att artiklarna har <strong>enheter per pall</strong>.</div>`}</div></div></div>`;
}
async function regenerateLabels(orderId){
  try{const r=await api(`/api/labels/${orderId}/generate`,{method:"POST"}); await loadLabels(orderId); showToast(`${r.created} nya · ${r.updated} uppdaterade palletiketter.`)}
  catch(err){showToast(err.message)}
}
async function printCurrentLabels(){
  if(!state.currentLabelOrder)return;
  try{
    const r=await api(`/api/labels/${state.currentLabelOrder}/mark-printed`,{method:"POST"});
    await loadLabels(state.currentLabelOrder);
    showToast(`${r.count} etiketter markerade för ${r.printer}.`);
    setTimeout(()=>window.print(),120);
  }catch(err){showToast(err.message)}
}

async function scanProducedLabel(){
  const input=$("productionScanInput"), result=$("productionScanResult");
  const uid=(input?.value||"").trim(); if(!uid)return;
  try{
    const r=await api("/api/labels/scan/produced",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({label_uid:uid,device_code:"PROD-TEST"})});
    result.innerHTML=`<div class="notice green"><strong>OK — PRODUCED</strong><br>${esc(r.label_uid)} · Order ${esc(r.order_no)} · A${esc(r.article_no)} · Pall ${r.sequence_no} av ${r.total_sequences}</div>`;
    input.value=""; input.focus();
    if(state.currentLabelOrder) await loadLabels(state.currentLabelOrder);
    showToast(`${r.label_uid} → PRODUCED`);
  }catch(err){
    result.innerHTML=`<div class="notice amber"><strong>Skanning nekad</strong><br>${esc(err.message)}</div>`;
    input.select(); showToast(err.message);
  }
}

// ---------------- Fortnox ----------------
async function searchFortnoxOrders(){
  const q=$("fortnoxSearch").value||"";
  const d=await api("/api/orders?limit=50&search="+encodeURIComponent(q));
  $("fortnoxOrders").innerHTML=d.rows.length?`<table><tbody>${d.rows.map(o=>`<tr class="clickable" onclick="previewFortnox(${o.id})">
    <td><strong>${o.order_no}</strong><div class="small muted">${o.source==="legacy"?"Excel":"ERP v2"}</div></td>
    <td>${esc(o.customer_name)}</td><td>${fmtDate(o.delivery_date)}</td><td>${badge(o.status,statusKind(o.status))}</td></tr>`).join("")}</tbody></table>`:`<div class="empty">Ingen order.</div>`;
}
async function previewFortnox(orderId){
  try{
    const d=await api(`/api/fortnox/${orderId}/preview`);
    $("fortnoxNote").innerHTML=`<strong>${esc(d.verified_source)}</strong><br>${esc(d.note)}`;
    $("fortnoxJson").textContent=JSON.stringify(d.payload,null,2);
  }catch(err){showToast(err.message)}
}


// ---------------- Statistics ----------------
async function loadStatisticsPage(){
  try{
    if(!state.statsOptions){
      state.statsOptions=await api("/api/statistics/options");
      $("statsCustomer").innerHTML=`<option value="">Alla kunder</option>`+state.statsOptions.customers.map(c=>`<option value="${c.customer_no}">${c.customer_no} — ${esc(c.name)}</option>`).join("");
      $("statsArticle").innerHTML=`<option value="">Alla artiklar</option>`+state.statsOptions.articles.map(a=>`<option value="${a.article_no}">${a.article_no} — ${esc(a.name)}</option>`).join("");
      resetStatistics(false);
    }
    await loadStatistics();
  }catch(err){showToast(err.message)}
}
function resetStatistics(load=true){
  const now=new Date(); const from=new Date(now.getFullYear(),0,1);
  $("statsFrom").value=from.toISOString().slice(0,10); $("statsTo").value=now.toISOString().slice(0,10);
  $("statsCustomer").value=""; $("statsArticle").value=""; $("statsWeekday").value="";
  if(load)loadStatistics();
}
function statRanks(rows,labelKey,valueKey,onClickExpr=""){
  if(!rows?.length)return `<div class="empty">Ingen data.</div>`;
  const max=Math.max(1,...rows.map(r=>Number(r[valueKey]||0)));
  return rows.map(r=>`<div class="rank-row ${onClickExpr?"clickable":""}" ${onClickExpr?`onclick="${onClickExpr.replaceAll("__VALUE__",r.customer_no??r.article_no??"")}"`:""}><div><strong>${esc(r[labelKey]||"—")}</strong><div class="small muted">${r.orders?`${fmt(r.orders,0)} order`:""}</div></div><div class="rank-bar"><span style="width:${Math.max(2,Number(r[valueKey]||0)/max*100)}%"></span></div><div class="t-right strong">${valueKey==="kg"?fmt(Number(r[valueKey]||0)/1000,1)+" t":fmt(r[valueKey],0)}</div></div>`).join("");
}
function renderTrend(rows){
  if(!rows?.length)return `<div class="empty">Ingen volymdata.</div>`;
  const W=1000,H=220,pad=30,max=Math.max(1,...rows.map(r=>Number(r.kg||0)));
  const pts=rows.map((r,i)=>{const x=pad+(rows.length===1?0:(W-pad*2)*i/(rows.length-1));const y=H-pad-(H-pad*2)*Number(r.kg||0)/max;return [x,y,r]} );
  const line=pts.map(p=>`${p[0]},${p[1]}`).join(" ");
  const area=`${pad},${H-pad} ${line} ${pts[pts.length-1][0]},${H-pad}`;
  const labels=pts.filter((_,i)=>i===0||i===pts.length-1||i===Math.floor(pts.length/2));
  return `<svg class="stat-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"><line class="stat-gridline" x1="${pad}" y1="${pad}" x2="${W-pad}" y2="${pad}"/><line class="stat-gridline" x1="${pad}" y1="${H/2}" x2="${W-pad}" y2="${H/2}"/><line class="stat-gridline" x1="${pad}" y1="${H-pad}" x2="${W-pad}" y2="${H-pad}"/><polygon class="stat-area" points="${area}"/><polyline class="stat-line" points="${line}"/>${pts.map(p=>`<circle class="stat-point" cx="${p[0]}" cy="${p[1]}" r="2.6"><title>${esc(p[2].period)} · ${fmt(p[2].kg/1000,1)} t</title></circle>`).join("")}${labels.map(p=>`<text class="stat-axis-label" x="${p[0]}" y="${H-5}" text-anchor="middle">${esc(p[2].period)}</text>`).join("")}<text class="stat-axis-label" x="${pad}" y="18">${fmt(max/1000,1)} t</text></svg>`;
}
async function loadStatistics(){
  if(!$("statsFrom")?.value)return;
  const p=new URLSearchParams({date_from:$("statsFrom").value,date_to:$("statsTo").value});
  if($("statsCustomer").value)p.set("customer_no",$("statsCustomer").value); if($("statsArticle").value)p.set("article_no",$("statsArticle").value); if($("statsWeekday").value!=="")p.set("weekday",$("statsWeekday").value);
  try{
    const d=await api("/api/statistics/overview?"+p.toString()); const k=d.kpis;
    $("statsKpis").innerHTML=`<div class="kpi"><div class="kpi-label">Order</div><div class="kpi-value">${fmt(k.orders,0)}</div><div class="kpi-sub">valda filter</div></div><div class="kpi"><div class="kpi-label">Volym</div><div class="kpi-value">${fmt(k.kg/1000,1)} t</div><div class="kpi-sub">dailyLoad</div></div><div class="kpi"><div class="kpi-label">Snitt/order</div><div class="kpi-value">${fmt(k.avg_kg_per_order,0)} kg</div><div class="kpi-sub">fysisk volym</div></div><div class="kpi"><div class="kpi-label">Kunder</div><div class="kpi-value">${fmt(k.customers,0)}</div><div class="kpi-sub">unika</div></div><div class="kpi"><div class="kpi-label">Produkter</div><div class="kpi-value">${fmt(k.products,0)}</div><div class="kpi-sub">dailyLoad</div></div><div class="kpi"><div class="kpi-label">ERP-försäljning*</div><div class="kpi-value">${money(k.native_sales_value)}</div><div class="kpi-sub">${fmt(k.native_sales_orders,0)} native order</div></div>`;
    $("statsTrend").innerHTML=renderTrend(d.trend); $("statsTrendLabel").textContent=`${fmtDate(d.filters.date_from)} – ${fmtDate(d.filters.date_to)}`;
    const dayNames={0:"Söndag",1:"Måndag",2:"Tisdag",3:"Onsdag",4:"Torsdag",5:"Fredag",6:"Lördag"}; const maxDay=Math.max(1,...d.weekdays.map(x=>Number(x.kg||0)));
    $("statsWeekdays").innerHTML=d.weekdays.length?d.weekdays.map(x=>`<div class="weekday-row"><strong>${dayNames[x.weekday]}</strong><div class="rank-bar"><span style="width:${Math.max(2,Number(x.kg||0)/maxDay*100)}%"></span></div><div class="t-right">${fmt(x.kg/1000,1)} t</div></div>`).join(""):`<div class="empty">Ingen data.</div>`;
    $("statsStations").innerHTML=d.stations.length?`<div class="station-grid">${d.stations.map(x=>`<div class="station-stat"><div class="small muted">STATION ${x.station_id}</div><strong>${fmt(x.orders,0)} order</strong><div class="small">${fmt(x.qty,0)} beställd qty</div></div>`).join("")}</div>`:`<div class="empty">Ingen stationsdata.</div>`;
    $("statsCustomers").innerHTML=statRanks(d.top_customers,"name","kg","setStatsCustomer(__VALUE__)");
    $("statsProducts").innerHTML=statRanks(d.top_products,"product","kg");
    $("statsArticles").innerHTML=d.top_articles.length?`<table><thead><tr><th>Artikel</th><th>Namn</th><th class="t-right">Order</th><th class="t-right">Beställd qty</th></tr></thead><tbody>${d.top_articles.map(x=>`<tr class="clickable" onclick="setStatsArticle(${x.article_no})"><td><strong>${x.article_no}</strong></td><td>${esc(x.name)}</td><td class="t-right">${fmt(x.orders,0)}</td><td class="t-right">${fmt(x.qty,0)}</td></tr>`).join("")}</tbody></table>`:`<div class="empty">Ingen artikeldata.</div>`;
    $("statsNotes").innerHTML=`<strong>Datakällor</strong><br>${d.notes.map(esc).join("<br>")}`;
  }catch(err){showToast("Statistik: "+err.message)}
}
function setStatsCustomer(no){$("statsCustomer").value=String(no);loadStatistics()}
function setStatsArticle(no){$("statsArticle").value=String(no);loadStatistics()}

// ---------------- Users / devices / audit ----------------
async function loadUsersPage(){
  if(state.auth?.role!=="admin")return;
  try{
    const [users,devices,audit]=await Promise.all([api("/api/users"),api("/api/devices"),api("/api/audit?limit=80")]);
    $("usersList").innerHTML=users.map(u=>`<div class="user-row"><div><strong>${esc(u.display_name)}</strong> ${badge(u.role_label,u.active?"blue":"gray")}<div class="small muted">${esc(u.username)} · ${u.account_type==="operator"?"PIN / operatör":"Kontor"}${u.last_login_at?` · senast ${esc(u.last_login_at)}`:""}</div></div><button class="btn" onclick="openUserEditor(${u.id})">Redigera</button></div>`).join("");
    state._users=users;
    $("devicesList").innerHTML=devices.length?devices.map(d=>`<div class="device-row"><div><strong>${esc(d.device_code)} — ${esc(d.name)}</strong><div class="small muted">${esc(d.location_type)}${d.station_id?` · S${d.station_id}`:""} · ${d.active?"Aktiv":"Inaktiv"}</div></div><button class="btn" onclick='openDeviceEditor(${JSON.stringify(d.device_code)})'>Redigera</button></div>`).join(""):`<div class="empty">Inga enheter skapade ännu.</div>`; state._devices=devices;
    $("auditList").innerHTML=audit.length?audit.map(a=>`<div class="audit-row"><div class="audit-top"><strong>${esc(a.display_name||a.username||"System")}</strong><span class="small muted">${esc(a.created_at)}</span></div><div class="audit-message">${esc(a.message)}</div><div class="audit-meta">${esc(a.action)} · ${esc(a.entity_type||"")} ${esc(a.entity_key||"")}</div></div>`).join(""):`<div class="empty">Ingen audit ännu.</div>`;
  }catch(err){showToast(err.message)}
}
async function openUserEditor(id=null){
  const u=id?(state._users||[]).find(x=>x.id===id):null;
  openModal(u?"Redigera användare":"Ny användare","Kontorsanvändare använder lösenord. Produktionsoperatörer använder snabb PIN.",`<form id="userEditor" class="form-grid"><div class="field span2"><label>Namn</label><input id="ueName" value="${esc(u?.display_name||"")}" required></div><div class="field span2"><label>Användarnamn / operatörskod</label><input id="ueUsername" value="${esc(u?.username||"")}" required></div><div class="field span2"><label>E-post</label><input id="ueEmail" type="email" value="${esc(u?.email||"")}"></div><div class="field"><label>Kontotyp</label><select id="ueType"><option value="office" ${u?.account_type==="office"?"selected":""}>Kontor</option><option value="operator" ${u?.account_type==="operator"?"selected":""}>Operatör / PIN</option></select></div><div class="field"><label>Roll</label><select id="ueRole">${[["admin","Admin"],["office","Kontor"],["production","Produktion"],["loading","Utlastning"],["viewer","Läsbehörighet"]].map(([v,l])=>`<option value="${v}" ${u?.role===v?"selected":""}>${l}</option>`).join("")}</select></div><div class="field span2"><label>${u?"Nytt lösenord/PIN (lämna tomt för oförändrat)":"Lösenord eller PIN"}</label><input id="ueSecret" type="password" ${u?"":"required"}></div><div class="field"><label>Badge-kod (valfritt)</label><input id="ueBadge" value="${esc(u?.badge_code||"")}"></div><div class="field"><label>Aktiv</label><select id="ueActive"><option value="1" ${u?.active!==false?"selected":""}>Ja</option><option value="0" ${u?.active===false?"selected":""}>Nej</option></select></div><div class="span4 row-actions"><button class="btn primary" type="submit">SPARA</button><button class="btn" type="button" onclick="closeModal()">Avbryt</button></div></form>`);
  $("userEditor").onsubmit=e=>saveUserEditor(e,u?.id||null);
}
async function saveUserEditor(e,id){e.preventDefault();const payload={username:$("ueUsername").value,display_name:$("ueName").value,email:$("ueEmail").value,account_type:$("ueType").value,role:$("ueRole").value,secret:$("ueSecret").value,badge_code:$("ueBadge").value,active:$("ueActive").value==="1"};try{await api(id?`/api/users/${id}`:"/api/users",{method:id?"PUT":"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});closeModal();showToast("Användare sparad.");loadUsersPage()}catch(err){showToast(err.message)}}
function openDeviceEditor(code=null){const d=code?(state._devices||[]).find(x=>x.device_code===code):null;openModal(d?"Redigera enhet":"Ny scanner / terminal","Enheten beskriver var scanningen sker. Operatören är en separat identitet.",`<form id="deviceEditor" class="form-grid"><div class="field span2"><label>Enhetskod</label><input id="deCode" value="${esc(d?.device_code||"")}" ${d?"readonly":""} required placeholder="PROD-S1"></div><div class="field span2"><label>Namn</label><input id="deName" value="${esc(d?.name||"")}" required placeholder="Station 1 scanner"></div><div class="field"><label>Plats</label><select id="deLocation"><option value="STATION" ${d?.location_type==="STATION"?"selected":""}>Produktion</option><option value="UNLOADING" ${d?.location_type==="UNLOADING"?"selected":""}>Utlastning</option></select></div><div class="field"><label>Station</label><select id="deStation"><option value="">—</option>${[1,2,3,4].map(x=>`<option value="${x}" ${Number(d?.station_id)===x?"selected":""}>S${x}</option>`).join("")}</select></div><div class="field"><label>Aktiv</label><select id="deActive"><option value="1" ${d?.active!==0?"selected":""}>Ja</option><option value="0" ${d?.active===0?"selected":""}>Nej</option></select></div><div class="span4 row-actions"><button class="btn primary" type="submit">SPARA</button><button class="btn" type="button" onclick="closeModal()">Avbryt</button></div></form>`);$("deviceEditor").onsubmit=e=>saveDeviceEditor(e,d?.device_code||null)}
async function saveDeviceEditor(e,code){e.preventDefault();const payload={device_code:$("deCode").value,name:$("deName").value,location_type:$("deLocation").value,station_id:$("deStation").value?Number($("deStation").value):null,active:$("deActive").value==="1"};try{await api(code?`/api/devices/${encodeURIComponent(code)}`:"/api/devices",{method:code?"PUT":"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});closeModal();showToast("Enhet sparad.");loadUsersPage()}catch(err){showToast(err.message)}}

document.addEventListener("keydown",e=>{
  if(e.key==="Escape"){
    closeDrawer();
    closeModal();
  }
});
window.addEventListener("beforeunload",e=>{
  if(state.orderDirty && !state.orderSaved){
    e.preventDefault();
    e.returnValue="";
  }
});
document.addEventListener("DOMContentLoaded",init);
