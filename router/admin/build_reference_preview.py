"""Build the test UI from the owner's reference, preserving its renderer."""
from pathlib import Path

REFERENCE = Path('/home/ai_service/sandbox/users/black-walf16/files/output/vpn-admin-prototype.html')
DEST = Path(__file__).resolve().parents[2] / 'web' / 'admin_panel'
source = REFERENCE.read_text()
start = source.index('<script>')
end = source.index('</script>', start)
script = source[start + len('<script>'):end]
a = script.index('const STATE = {')
b = script.index('\nfunction formatExpiry', a)
script = script[:a] + '''const STATE = {ownNodes: [], subs: [], policy: {}, routeOpen: {general:false, gemini:true, ru:false}, deleteConfirmId:null, editingNodeKey:null, editingSubId:null};
function esc(x) { const e = document.createElement('span'); e.textContent = String(x ?? ''); return e.innerHTML; }
''' + script[b:]
# Keep the reference's original rendering; escape data in its template HTML.
for expr in ['node.original_name','node.custom_name','priNode ? priNode.custom_name : pol.primary',
             'priNode ? priNode.custom_name : "наш сервер"','sub.name','origName','customName']:
    script = script.replace('${' + expr + '}', '${esc(' + expr + ')}')
script = script.replace('const label = n ? n.custom_name : id;', 'const label = esc(n ? n.custom_name : id);')
# The currently active outbound of leastPing is not exposed by this snapshot.
script = script.replace('ownSpoiler.append(renderPoolNodeControl(scKey, o.id, o.original_name, o.custom_name, o.probe));', '''const row = renderPoolNodeControl(scKey, o.id, o.original_name, o.custom_name, o.probe);
      const ip = document.createElement("span"); ip.className = "probe-info"; ip.textContent = o.ip || "IP не подтверждён"; row.querySelector(".node-left").append(ip); ownSpoiler.append(row);''')
# The legacy Xray uses leastPing; the array is not an ordered failover chain.
script = script.replace('sumRight.innerHTML = `Primary: <b>${esc(priNode ? priNode.custom_name : pol.primary)}</b> · В резерве по списку: <b>${pol.fallbacks.length}</b>`;', 'sumRight.textContent = `Тестовый основной: ${priNode?.custom_name || pol.primary} · резервов: ${pol.fallbacks.length} · боевой активный выход неизвестен`;')
script = script.replace('chainEl.innerHTML = `Порядок переключения при падении: ${chainNames.join(" → ")} → возврат на <b>${esc(priNode ? priNode.custom_name : "наш сервер")}</b>`;', 'chainEl.innerHTML = `Тестовая очередь (не применяется к Xray): ${chainNames.join(" → ")}`;')
script = script.replace('(Primary)</b>`', '(тестовый основной)</b>`')
script = script.replace('isPri ? "★ Основной (Primary)" : "Сделать основным"', 'isPri ? "★ Тестовый основной" : "Сделать основным"')
script = script.replace('    // Спойлеры по каждой подключенной подписке', '''    const legacyIds = [pol.primary, ...pol.fallbacks].filter(id => !findNodeById(id));
    if (legacyIds.length) {
      const legacy = document.createElement('details'); legacy.className = 'src-spoiler'; legacy.open = true;
      const heading = document.createElement('summary'); heading.textContent = 'Ранее выбранные выходы Xray (без сопоставления с подпиской)'; legacy.append(heading);
      for (const id of legacyIds) legacy.append(renderPoolNodeControl(scKey, id, id, id, null));
      card.append(legacy);
    }
    // Спойлеры по каждой подключенной подписке''')
script = script.replace('applyBtn.textContent = "Применить маршрут";', 'applyBtn.textContent = "Сохранить тестовый маршрут";')
script = script.replace('badge.textContent = sub.status === "active" ? "● Активна" : "● Не работает";', 'badge.textContent = sub.status === "active" ? "● Активна" : (sub.error_full?.startsWith("Мок:") ? "● Не загружена (мок)" : "● Не работает");')
script = script.replace('right.textContent = "не проверен";', 'right.textContent = pr ? "результат не определён" : "не проверен";')
script = script.replace('"1. Основной маршрут (проверка пингом)"', '"1. Основной маршрут (проверка HTTPS)"')
script = script.replace('"Пинг всех узлов"', '"Проверка доступности всех узлов"')
script = script.replace('"Пинговать отобранный пул"', '"Проверить отобранный пул"')
script = script.replace('pBtn.className = "btn btn-sm";', 'pBtn.className = "btn btn-sm probe-action";')
script = script.replace('''          showNotice(`Запущена проверка «${pTitle}» для всех серверов подписки «${esc(sub.name)}»…`);''', '''          runProbe(pKey, sub.nodes.map(n => n.node_id));''')
script = script.replace('probePoolBtn.className = "btn btn-sm";', 'probePoolBtn.className = "btn btn-sm probe-action";')
script = script.replace('probePoolBtn.onclick = () => showNotice(`Быстрая проверка только отобранного пула для «${scTitle}»…`);', '''probePoolBtn.onclick = () => runProbe(scKey, [...STATE.ownNodes.filter(o => o.id === defaultOwnId).map(o => o.id), ...STATE.subs.flatMap(s => s.nodes.filter(n => n.pools[scKey]).map(n => n.node_id))]);''')
# Bind the reference controls to the isolated mock database, never to production.
assert script.count('    titleWrap.append(toggle);') == 1
script = script.replace('    titleWrap.append(toggle);', '''    if (STATE.editingSubId === sub.id) {
      const field = document.createElement('input'); field.className = 'inp sub-name-input'; field.value = sub.name; field.maxLength = 200; field.setAttribute('aria-label', 'Название подписки');
      const save = document.createElement('button'); save.className = 'btn btn-sm btn-primary sub-name-save'; save.textContent = 'Сохранить';
      const cancel = document.createElement('button'); cancel.className = 'btn btn-sm sub-name-cancel'; cancel.textContent = 'Отмена';
      save.onclick = () => { STATE.editingSubId = null; mockAction({action:'rename_sub',sub_id:sub.id,name:field.value,version:sub.version}, 'Название подписки сохранено только в тестовой БД'); };
      cancel.onclick = () => { STATE.editingSubId = null; renderAll(); };
      field.onkeydown = e => { if (e.key === 'Enter') save.click(); else if (e.key === 'Escape') cancel.click(); };
      titleWrap.append(field, save, cancel);
    } else { titleWrap.append(toggle); }''')
assert script.count('    actions.append(updBtn);') == 1
assert script.count('delBtn.textContent = "×";') == 1
script = script.replace('delBtn.textContent = "×";', 'delBtn.textContent = "Удалить";')
def section(text, begin, end, replacement):
    assert text.count(begin) == 1 and text.count(end) == 1, (begin, end)
    i = text.index(begin)
    j = text.index(end, i)
    return text[:i] + replacement + text[j:]

script = section(script, '  cb.onchange = () => {', '  left.append(cb);', '''  cb.onchange = () => mockAction({action:'node', sub_id:subId, node_id:node.node_id, pools:{[poolKey]:cb.checked}}, 'Галочка сохранена в тестовой БД');
''')
script = section(script, '    saveBtn.onclick = () => {', '    left.append(origSpan, inp, saveBtn);', '''    saveBtn.onclick = () => { STATE.editingNodeKey = null; mockAction({action:'node', sub_id:subId, node_id:node.node_id, rename:true, custom_name:inp.value.trim()}, 'Название сохранено в тестовой БД'); };
''')
script = section(script, '    updBtn.onclick = () => {', '    actions.append(updBtn);', '''    updBtn.onclick = () => mockAction({action:'refresh', sub_id:sub.id}, 'Мок обновления сохранён. Запрос к провайдеру не выполнялся');
''')
script = script.replace('    actions.append(updBtn);', '''    const renameBtn = document.createElement('button'); renameBtn.className = 'btn btn-sm sub-rename'; renameBtn.textContent = 'Переименовать';
    renameBtn.onclick = () => { STATE.editingSubId = sub.id; STATE.deleteConfirmId = null; renderAll(); document.querySelector('.sub-name-input')?.focus(); };
    actions.append(renameBtn, updBtn);''')
script = section(script, '      yes.onclick = () => {', '      const no = document.createElement("button");', '''      yes.onclick = () => { if (inp.value.trim() !== 'DELETE') { inp.value=''; inp.placeholder='введи DELETE'; return; } STATE.deleteConfirmId = null; mockAction({action:'delete',sub_id:sub.id,confirm:inp.value.trim()}, 'Подписка удалена только из тестовой БД'); };
''')
script = script.replace('    body.append(urlBar);', '''    urlBar.querySelector('button').onclick = () => mockAction({action:'replace_url',sub_id:sub.id,url:urlBar.querySelector('input').value}, 'URL заменён только в тестовой БД; провайдер не запрашивался');
    body.append(urlBar);''')
script = script.replace('applyBtn.onclick = () => showNotice(`Маршрут «${scTitle}» применён через blue/green.`);', 'applyBtn.onclick = () => saveRoute(scKey, pol);')
# Preserve changes immediately, including ordering. Server handles concurrency and pool validation.
for fragment in [
    '    pol.primary = nodeId;\\n    pol.fallbacks = pol.fallbacks.filter(id => id !== nodeId);\\n    renderAll();',
    '      if (isFb) pol.fallbacks.splice(fbIdx, 1);\\n      else pol.fallbacks.push(nodeId);\\n      renderAll();',
    '        [pol.fallbacks[fbIdx - 1], pol.fallbacks[fbIdx]] = [pol.fallbacks[fbIdx], pol.fallbacks[fbIdx - 1]];\\n        renderAll();',
    '        [pol.fallbacks[fbIdx + 1], pol.fallbacks[fbIdx]] = [pol.fallbacks[fbIdx], pol.fallbacks[fbIdx + 1]];\\n        renderAll();',
]:
    fragment = fragment.replace('\\n', '\n')
    assert script.count(fragment) == 1
    script = script.replace(fragment, fragment.rsplit('renderAll();', 1)[0] + 'saveRoute(scKey, pol);')

a = script.index('document.getElementById("add-btn").onclick')
script = script[:a] + '''async function mockAction(payload, message) {
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
'''
html = source[:start] + '<script src="read-preview.js" defer></script>' + source[end + len('</script>'):]
html = html.replace('обновлено <b>23.11, 14:20</b> · активный цвет <b>a</b> · сторож failover <b>включён (по списку)</b>', 'Загружаем данные тестового API…')
html = html.replace('МАКЕТ ПРОТОТИПА</b> · демонстрация на вымышленных данных (боевой VPN не затронут)', 'ТЕСТОВЫЙ СТЕНД</b> · загрузка данных')
(DEST / 'read-preview.html').write_text(html)
(DEST / 'read-preview.js').write_text(script)
