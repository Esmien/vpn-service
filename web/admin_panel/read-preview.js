
const NOW = Math.floor(Date.now() / 1000);
const DAY = 86400;

const STATE = {ownNodes: [], subs: [], policy: {}, routeOpen: {general:false, gemini:true, ru:false}, deleteConfirmId:null, editingNodeKey:null, editingSubId:null};
function esc(x) { const e = document.createElement('span'); e.textContent = String(x ?? ''); return e.innerHTML; }

function formatExpiry(ts) {
  if (!ts || ts <= 0) {
    return { text: "нет информации от провайдера", cls: "exp-gray" };
  }
  const d = new Date(ts * 1000);
  const dd = String(d.getDate()).padStart(2, "0");
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const yyyy = d.getFullYear();
  const days = Math.ceil((ts - NOW) / DAY);
  const cls = days > 7 ? "exp-green" : (days >= 4 ? "exp-orange" : "exp-red");
  const leftText = days >= 0 ? `осталось ${days} дн.` : `истекла ${-days} дн. назад`;
  return { text: `${dd}.${mm}.${yyyy} — ${leftText}`, cls };
}

function showNotice(msg) {
  const el = document.getElementById("notice");
  el.textContent = msg;
  el.style.display = "block";
}

function findNodeById(id) {
  for (const o of STATE.ownNodes) if (o.id === id) return { ...o, subName: "Наш сервер" };
  for (const s of STATE.subs) {
    for (const n of s.nodes) if (n.node_id === id) return { ...n, id: n.node_id, subName: s.name };
  }
  return null;
}

function renderNodeRow(node, subId, poolKey) {
  const row = document.createElement("div");
  row.className = "node-row";
  const editKey = `${subId}:${node.node_id}:${poolKey}`;
  const pr = poolKey === "lte" ? node.probe?.general : node.probe?.[poolKey];

  const left = document.createElement("div");
  left.className = "node-left";

  const st = document.createElement("span");
  if (pr?.status === "ok") { st.className = "st-ok"; st.textContent = "✓"; }
  else if (pr?.status === "bad") { st.className = "st-bad"; st.textContent = "×"; }
  else { st.className = "st-un"; st.textContent = "•"; }
  left.append(st);

  const cb = document.createElement("input");
  cb.type = "checkbox";
  cb.checked = !!node.pools[poolKey];
  cb.onchange = () => mockAction({action:'node', sub_id:subId, node_id:node.node_id, pools:{[poolKey]:cb.checked}}, 'Галочка сохранена в тестовой БД');
  left.append(cb);

  if (STATE.editingNodeKey === editKey) {
    const origSpan = document.createElement("span");
    origSpan.className = "name-orig";
    origSpan.textContent = node.original_name + " / ";
    const inp = document.createElement("input");
    inp.className = "inp";
    inp.style.padding = "3px 7px";
    inp.style.fontSize = "12.5px";
    inp.value = node.custom_name;
    const saveBtn = document.createElement("button");
    saveBtn.className = "btn btn-sm btn-primary";
    saveBtn.textContent = "Сохранить";
    saveBtn.onclick = () => { STATE.editingNodeKey = null; mockAction({action:'node', sub_id:subId, node_id:node.node_id, rename:true, custom_name:inp.value.trim()}, 'Название сохранено в тестовой БД'); };
    left.append(origSpan, inp, saveBtn);
  } else {
    const pair = document.createElement("div");
    pair.className = "name-pair";
    pair.innerHTML = `<span class="name-orig">${esc(node.original_name)}</span><span class="name-sep">/</span><span class="name-custom">${esc(node.custom_name)}</span>`;
    const editBtn = document.createElement("button");
    editBtn.className = "btn btn-sm";
    editBtn.textContent = "редактировать";
    editBtn.onclick = () => { STATE.editingNodeKey = editKey; renderAll(); };
    left.append(pair, editBtn);
  }

  const right = document.createElement("div");
  right.className = "probe-info";
  if (pr?.status === "ok") {
    right.textContent = (pr.region ? `регион ${pr.region} · ` : "") + `${pr.latency_ms} мс`;
  } else if (pr?.status === "bad") {
    right.textContent = pr.region ? `непригоден (${pr.region})` : "недоступен";
  } else {
    right.textContent = pr ? "результат не определён" : "не проверен";
  }

  row.append(left, right);
  return row;
}

function renderSubs() {
  const root = document.getElementById("subs-root");
  root.replaceChildren();

  for (const sub of STATE.subs) {
    const card = document.createElement("div");
    card.className = "sub-card" + (sub.open ? " open" : "");

    const head = document.createElement("div");
    head.className = "sub-head";

    const titleWrap = document.createElement("div");
    titleWrap.className = "sub-title-wrap";

    const toggle = document.createElement("span");
    toggle.className = "sub-toggle";
    toggle.textContent = sub.name;
    toggle.onclick = () => { sub.open = !sub.open; renderAll(); };
    if (STATE.editingSubId === sub.id) {
      const field = document.createElement('input'); field.className = 'inp sub-name-input'; field.value = sub.name; field.maxLength = 200; field.setAttribute('aria-label', 'Название подписки');
      const save = document.createElement('button'); save.className = 'btn btn-sm btn-primary sub-name-save'; save.textContent = 'Сохранить';
      const cancel = document.createElement('button'); cancel.className = 'btn btn-sm sub-name-cancel'; cancel.textContent = 'Отмена';
      save.onclick = () => { STATE.editingSubId = null; mockAction({action:'rename_sub',sub_id:sub.id,name:field.value,version:sub.version}, 'Название подписки сохранено только в тестовой БД'); };
      cancel.onclick = () => { STATE.editingSubId = null; renderAll(); };
      field.onkeydown = e => { if (e.key === 'Enter') save.click(); else if (e.key === 'Escape') cancel.click(); };
      titleWrap.append(field, save, cancel);
    } else { titleWrap.append(toggle); }

    const badge = document.createElement("span");
    badge.className = "badge " + (sub.status === "active" ? "ok" : "err");
    badge.textContent = sub.status === "active" ? "● Активна" : (sub.error_full?.startsWith("Мок:") ? "● Не загружена (мок)" : "● Не работает");
    titleWrap.append(badge);

    if (sub.status === "error" && sub.error_snippet) {
      const sp = document.createElement("details");
      sp.className = "err-spoiler";
      const sum = document.createElement("summary");
      sum.textContent = `⚠ «${sub.error_snippet}»`;
      const full = document.createElement("div");
      full.className = "err-full";
      full.textContent = sub.error_full;
      sp.append(sum, full);
      titleWrap.append(sp);
    }

    const exp = formatExpiry(sub.expire);
    const expSpan = document.createElement("span");
    expSpan.className = exp.cls;
    expSpan.textContent = exp.text;
    titleWrap.append(expSpan);

    if (sub.tg_url) {
      const tg = document.createElement("a");
      tg.className = "tg-link";
      const cleanTg = sub.tg_url.replace(/^https?:\/\//, "");
      tg.href = "https://" + cleanTg;
      tg.target = "_blank";
      tg.rel = "noreferrer";
      tg.textContent = cleanTg;
      titleWrap.append(tg);
    }

    const actions = document.createElement("div");
    actions.className = "sub-actions";

    const updBtn = document.createElement("button");
    updBtn.className = "btn btn-sm";
    updBtn.textContent = "Обновить";
    updBtn.onclick = () => mockAction({action:'refresh', sub_id:sub.id}, 'Мок обновления сохранён. Запрос к провайдеру не выполнялся');
    const renameBtn = document.createElement('button'); renameBtn.className = 'btn btn-sm sub-rename'; renameBtn.textContent = 'Переименовать';
    renameBtn.onclick = () => { STATE.editingSubId = sub.id; STATE.deleteConfirmId = null; renderAll(); document.querySelector('.sub-name-input')?.focus(); };
    actions.append(renameBtn, updBtn);

    if (STATE.deleteConfirmId === sub.id) {
      const inp = document.createElement("input");
      inp.className = "cfm-input";
      inp.placeholder = "DELETE";
      const yes = document.createElement("button");
      yes.className = "btn btn-sm btn-danger";
      yes.textContent = "Удалить";
      yes.onclick = () => { if (inp.value.trim() !== 'DELETE') { inp.value=''; inp.placeholder='введи DELETE'; return; } STATE.deleteConfirmId = null; mockAction({action:'delete',sub_id:sub.id,confirm:inp.value.trim()}, 'Подписка удалена только из тестовой БД'); };
      const no = document.createElement("button");
      no.className = "btn btn-sm";
      no.textContent = "Отмена";
      no.onclick = () => { STATE.deleteConfirmId = null; renderAll(); };
      actions.append(inp, yes, no);
    } else {
      const delBtn = document.createElement("button");
      delBtn.className = "btn btn-sm btn-danger";
      delBtn.textContent = "Удалить";
      delBtn.title = "Удалить подписку";
      delBtn.onclick = () => { STATE.deleteConfirmId = sub.id; renderAll(); };
      actions.append(delBtn);
    }

    head.append(titleWrap, actions);
    card.append(head);

    const body = document.createElement("div");
    body.className = "sub-body";

    const urlBar = document.createElement("div");
    urlBar.className = "sub-url-bar";
    urlBar.innerHTML = `<span>Ссылка на подписку (скрыта):</span><input class="inp" style="flex:1;padding:5px 9px;font-size:12.5px" placeholder="Вставьте новый URL для замены"><button class="btn btn-sm">Заменить URL</button>`;
    urlBar.querySelector('button').onclick = () => mockAction({action:'replace_url',sub_id:sub.id,url:urlBar.querySelector('input').value}, 'URL заменён только в тестовой БД; провайдер не запрашивался');
    body.append(urlBar);

    const poolDefs = [
      ["general", "1. Основной маршрут (проверка HTTPS)", "Проверка доступности всех узлов"],
      ["gemini",  "2. Gemini (проверка по правилам)", "Проверить доступность Gemini"],
      ["ru",      "3. Российские сервисы / RU (проверка по правилам)", "Проверить доступность RU"],
      ["lte",     "4. Белые списки / LTE (напрямую в подписку Esmien VPN LTE)", null]
    ];

    for (const [pKey, pTitle, probeLabel] of poolDefs) {
      const pBox = document.createElement("details");
      pBox.className = "pool-box";
      pBox.open = !!sub.openPools?.[pKey];
      pBox.ontoggle = () => { sub.openPools = sub.openPools || {}; sub.openPools[pKey] = pBox.open; };

      const pSum = document.createElement("summary");
      const count = sub.nodes.filter(n => n.pools[pKey]).length;
      const leftTitle = document.createElement("div");
      leftTitle.innerHTML = `<span class="pool-title">${pTitle}</span><span class="pool-meta">выбрано в пул: <b>${count}</b> из ${sub.nodes.length}</span>`;
      pSum.append(leftTitle);

      if (probeLabel) {
        const pBtn = document.createElement("button");
        pBtn.className = "btn btn-sm probe-action";
        pBtn.textContent = probeLabel;
        pBtn.onclick = (e) => {
          e.preventDefault(); e.stopPropagation();
          runProbe(pKey, sub.nodes.map(n => n.node_id));
        };
        pSum.append(pBtn);
      }
      pBox.append(pSum);

      const nList = document.createElement("div");
      nList.className = "node-list";
      if (!sub.nodes.length) {
        nList.innerHTML = `<div class="node-row" style="color:#77809a">Нет доступных серверов (подписка вернула ошибку)</div>`;
      } else {
        for (const n of sub.nodes) nList.append(renderNodeRow(n, sub.id, pKey));
      }
      pBox.append(nList);
      body.append(pBox);
    }

    card.append(body);
    root.append(card);
  }
}

function renderRoutes() {
  const root = document.getElementById("routes-root");
  root.replaceChildren();

  const scenarios = [
    ["general", "Основной маршрут (весь трафик без правил)", "direct"],
    ["gemini",  "Gemini (Google AI)", "nl"],
    ["ru",      "Российские сервисы (RU — банки, Госуслуги, маркетплейсы)", "ru1"]
  ];

  for (const [scKey, scTitle, defaultOwnId] of scenarios) {
    const pol = STATE.policy[scKey];
    const card = document.createElement("details");
    card.className = "route-card";
    card.open = !!STATE.routeOpen[scKey];
    card.ontoggle = () => { STATE.routeOpen[scKey] = card.open; };

    const sum = document.createElement("summary");
    const priNode = findNodeById(pol.primary);
    const titleEl = document.createElement("div");
    titleEl.className = "rc-title";
    titleEl.textContent = scTitle;

    const sumRight = document.createElement("div");
    sumRight.className = "rc-summary";
    sumRight.textContent = `Тестовый основной: ${priNode?.custom_name || pol.primary} · резервов: ${pol.fallbacks.length} · боевой активный выход неизвестен`;
    sum.append(titleEl, sumRight);
    card.append(sum);

    const chainNames = [pol.primary, ...pol.fallbacks].map((id, idx) => {
      const n = findNodeById(id);
      const label = esc(n ? n.custom_name : id);
      return idx === 0 ? `<b>1. ${label} (тестовый основной)</b>` : `${idx + 1}. ${label}`;
    });
    const chainEl = document.createElement("div");
    chainEl.className = "chain-preview";
    chainEl.innerHTML = `Тестовая очередь (не применяется к Xray): ${chainNames.join(" → ")}`;
    card.append(chainEl);

    const bar = document.createElement("div");
    bar.style.cssText = "display:flex;gap:8px;margin-bottom:10px";
    const probePoolBtn = document.createElement("button");
    probePoolBtn.className = "btn btn-sm probe-action";
    probePoolBtn.textContent = scKey === "general" ? "Проверить отобранный пул" : "Проверить отобранный пул";
    probePoolBtn.onclick = () => runProbe(scKey, [...STATE.ownNodes.filter(o => o.id === defaultOwnId).map(o => o.id), ...STATE.subs.flatMap(s => s.nodes.filter(n => n.pools[scKey]).map(n => n.node_id))]);
    const applyBtn = document.createElement("button");
    applyBtn.className = "btn btn-sm btn-primary";
    applyBtn.textContent = "Сохранить тестовый маршрут";
    applyBtn.onclick = () => saveRoute(scKey, pol);
    bar.append(probePoolBtn, applyBtn);
    card.append(bar);

    // Спойлер 1: Наши серверы
    const ownSpoiler = document.createElement("details");
    ownSpoiler.className = "src-spoiler";
    ownSpoiler.open = true;
    const ownNodes = STATE.ownNodes.filter(o => o.id === defaultOwnId);
    ownSpoiler.innerHTML = `<summary><span>Наши серверы</span><span class="pool-meta">${ownNodes.length} узел</span></summary>`;
    for (const o of ownNodes) {
      const row = renderPoolNodeControl(scKey, o.id, o.original_name, o.custom_name, o.probe);
      const ip = document.createElement("span"); ip.className = "probe-info"; ip.textContent = o.ip || "IP не подтверждён"; row.querySelector(".node-left").append(ip); ownSpoiler.append(row);
    }
    card.append(ownSpoiler);

    const legacyIds = [pol.primary, ...pol.fallbacks].filter(id => !findNodeById(id));
    if (legacyIds.length) {
      const legacy = document.createElement('details'); legacy.className = 'src-spoiler'; legacy.open = true;
      const heading = document.createElement('summary'); heading.textContent = 'Ранее выбранные выходы Xray (без сопоставления с подпиской)'; legacy.append(heading);
      for (const id of legacyIds) legacy.append(renderPoolNodeControl(scKey, id, id, id, null));
      card.append(legacy);
    }
    // Спойлеры по каждой подключенной подписке (только узлы, где pools[scKey] == true)
    for (const sub of STATE.subs) {
      const poolNodes = sub.nodes.filter(n => n.pools[scKey]);
      const sp = document.createElement("details");
      sp.className = "src-spoiler";
      sp.open = poolNodes.length > 0;
      sp.innerHTML = `<summary><span>Подписка: ${esc(sub.name)}</span><span class="pool-meta">в пуле: ${poolNodes.length}</span></summary>`;
      if (!poolNodes.length) {
        const empty = document.createElement("div");
        empty.style.cssText = "font-size:12px;color:#6c7486;padding:6px 0";
        empty.textContent = "Нет выбранных серверов. Отметьте их галочками в настройках этой подписки выше.";
        sp.append(empty);
      } else {
        for (const n of poolNodes) {
          sp.append(renderPoolNodeControl(scKey, n.node_id, n.original_name, n.custom_name, n.probe?.[scKey]));
        }
      }
      card.append(sp);
    }

    root.append(card);
  }
}

function renderPoolNodeControl(scKey, nodeId, origName, customName, pr) {
  const pol = STATE.policy[scKey];
  const isPri = pol.primary === nodeId;
  const fbIdx = pol.fallbacks.indexOf(nodeId);
  const isFb = fbIdx !== -1;

  const row = document.createElement("div");
  row.className = "node-row";

  const left = document.createElement("div");
  left.className = "node-left";
  const st = document.createElement("span");
  if (pr?.status === "ok") { st.className = "st-ok"; st.textContent = "✓"; }
  else if (pr?.status === "bad") { st.className = "st-bad"; st.textContent = "×"; }
  else { st.className = "st-un"; st.textContent = "•"; }

  const pair = document.createElement("div");
  pair.className = "name-pair";
  pair.innerHTML = `<span class="name-orig">${esc(origName)}</span><span class="name-sep">/</span><span class="name-custom">${esc(customName)}</span>`;
  left.append(st, pair);

  const right = document.createElement("div");
  right.className = "role-controls";

  if (pr?.latency_ms) {
    const lat = document.createElement("span");
    lat.className = "probe-info";
    lat.textContent = `${pr.latency_ms} мс`;
    right.append(lat);
  }

  const priBtn = document.createElement("button");
  priBtn.className = "role-pill" + (isPri ? " active-pri" : "");
  priBtn.textContent = isPri ? "★ Тестовый основной" : "Сделать основным";
  priBtn.onclick = () => {
    pol.primary = nodeId;
    pol.fallbacks = pol.fallbacks.filter(id => id !== nodeId);
    saveRoute(scKey, pol);
  };
  right.append(priBtn);

  if (!isPri) {
    const fbBtn = document.createElement("button");
    fbBtn.className = "role-pill" + (isFb ? " active-fb" : "");
    fbBtn.textContent = isFb ? `Запасной #${fbIdx + 1}` : "+ В резерв";
    fbBtn.onclick = () => {
      if (isFb) pol.fallbacks.splice(fbIdx, 1);
      else pol.fallbacks.push(nodeId);
      saveRoute(scKey, pol);
    };
    right.append(fbBtn);

    if (isFb) {
      const up = document.createElement("button");
      up.className = "ord-btn";
      up.textContent = "↑";
      up.disabled = fbIdx === 0;
      up.onclick = () => {
        [pol.fallbacks[fbIdx - 1], pol.fallbacks[fbIdx]] = [pol.fallbacks[fbIdx], pol.fallbacks[fbIdx - 1]];
        saveRoute(scKey, pol);
      };
      const dn = document.createElement("button");
      dn.className = "ord-btn";
      dn.textContent = "↓";
      dn.disabled = fbIdx === pol.fallbacks.length - 1;
      dn.onclick = () => {
        [pol.fallbacks[fbIdx + 1], pol.fallbacks[fbIdx]] = [pol.fallbacks[fbIdx], pol.fallbacks[fbIdx + 1]];
        saveRoute(scKey, pol);
      };
      right.append(up, dn);
    }
  }

  row.append(left, right);
  return row;
}

function renderAll() {
  renderSubs();
  renderRoutes();
}

async function mockAction(payload, message) {
  try {
    const r = await fetch('api/mock', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
    const result = await r.json();
    if (!r.ok) throw Error(result.error || ('HTTP ' + r.status));
    await start(); showNotice(message);
    return true;
  } catch (e) { await start(); showNotice('Не сохранено: ' + e.message); return false; }
}
function saveRoute(scenario, policy) {
  return mockAction({action:'route',scenario,primary:policy.primary,fallbacks:policy.fallbacks,version:policy.policy_version}, 'Настройки маршрута сохранены только в тестовой БД');
}
document.getElementById('add-btn').onclick = async () => {
  const name = document.getElementById('add-name'), url = document.getElementById('add-url'), tg = document.getElementById('add-tg');
  if (await mockAction({action:'add',name:name.value,url:url.value,tg_url:tg.value}, 'Подписка добавлена в тестовую БД, данные провайдера не загружались')) { name.value=''; url.value=''; tg.value=''; }
};
async function runProbe(scenario, ids) {
  if (!ids.length) { showNotice('Нет узлов для проверки.'); return; }
  document.querySelectorAll('.probe-action').forEach(b => b.disabled = true);
  showNotice(`Проверка ${scenario}: 0 / ${ids.length}. Маршруты не меняются.`);
  try {
    const response = await fetch('api/routes/probe', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({scenario, ids})});
    if (!response.ok) throw Error('HTTP ' + response.status);
    const {job_id} = await response.json();
    let job;
    do {
      await new Promise(resolve => setTimeout(resolve, 1500));
      const r = await fetch('api/routes/probe/' + encodeURIComponent(job_id), {cache:'no-store'});
      if (!r.ok) throw Error('HTTP ' + r.status);
      job = await r.json();
      for (const [id, result] of Object.entries(job.results)) {
        for (const sub of STATE.subs) { const n = sub.nodes.find(x => x.node_id === id); if (n) n.probe[scenario] = result; }
        const own = STATE.ownNodes.find(x => x.id === id);
        if (own) { own.probe = result; if (result.ip) own.ip = result.ip; }
      }
      renderAll();
      showNotice(`Проверка ${scenario}: ${Object.keys(job.results).length} / ${ids.length}` + (job.status === 'complete' ? ' · завершена' : ' · выполняется'));
      document.querySelectorAll('.probe-action').forEach(b => b.disabled = job.status !== 'complete');
    } while (job.status !== 'complete');
  } catch (e) { showNotice('Проверка не выполнена: ' + e.message); document.querySelectorAll('.probe-action').forEach(b => b.disabled = false); }
}
async function start() {
  try {
    const get = async p => { const r = await fetch('api/' + p, {cache:'no-store'}); if (!r.ok) throw Error('HTTP ' + r.status); return r.json(); };
    const [subs, routes] = await Promise.all([get('subs'), get('routes')]);
    const oldSubs = new Map(STATE.subs.map(s => [s.id,s]));
    const oldOwn = new Map(STATE.ownNodes.map(o => [o.id,o]));
    STATE.subs = subs.subscriptions.map((s, i) => ({...s, id:s.sub_id, status:s.fetch_status,
      expire:s.expires_at ? Date.parse(s.expires_at)/1000 : 0, open:oldSubs.get(s.sub_id)?.open ?? (i===0),
      openPools:oldSubs.get(s.sub_id)?.openPools || {general:true}, nodes:s.nodes.map(n => ({...n, custom_name:n.custom_name || n.original_name, probe:oldSubs.get(s.sub_id)?.nodes.find(x=>x.node_id===n.node_id)?.probe || n.probes || {}}))}));
    STATE.ownNodes = Object.entries(routes.own_names).map(([id,name]) => ({id, original_name:id, custom_name:name, ip:oldOwn.get(id)?.ip || routes.own_egress[id]?.ip, probe:oldOwn.get(id)?.probe || null}));
    STATE.policy = routes.scenarios;
    renderAll();
    document.querySelector('.proto-banner span').textContent = 'МОК-СТЕНД · изменения только в тестовой БД · боевой VPN не меняется';
    document.querySelector('.meta').textContent = 'Изменения сохраняются только в тестовой БД. Проверки выполняются отдельно; загрузка подписок имитируется.';
    document.body.dataset.result = 'ready';
  } catch (_) {
    document.querySelector('.meta').textContent = 'Не удалось загрузить тестовый API';
    document.querySelectorAll('button, input').forEach(el => { el.disabled = true; });
    document.body.dataset.result = 'error';
  }
}
start();
