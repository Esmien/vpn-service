import sys, tempfile, json, subprocess, time, urllib.request
from pathlib import Path
sys.path.append(str(Path(subprocess.check_output(['python3','-c','import websocket; print(websocket.__path__[0])'], text=True).strip()).parent))
from fastapi.testclient import TestClient
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import websocket
from router.admin import read_store as s
from router.admin.snapshot_import import import_snapshot
from router.admin.mock_store import MockStore
from router.admin.read_api import create_app

with tempfile.TemporaryDirectory(dir='/root/.hermes/cache/scratch') as temp:
    base=Path(temp); snap=base/'snap.json'
    snap.write_text(json.dumps({'subscriptions':[{'id':'s','name':'Browser Test','status':'active','url':'https://example.test/secret','nodes':[{'node_id':'n1','original_name':'Node A','link':'vless://SECRET@example.test:443','pools':{}},{'node_id':'n2','original_name':'Node B','link':'vless://SECRET@example.test:443','pools':{}}]}], 'policy':{'gemini':['nl'],'ru':['ru1']},'outbounds':[]}))
    engine=s.open_store(base/'private'/'mock.sqlite3',initialize=True); import_snapshot(engine,snap)
    client=TestClient(create_app(engine,operator='owner',mock_store=MockStore(engine),preview_assets=Path('/root/.hermes/cache/scratch/vpn-mock-ui')))
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def go(self):
            if not self.path.startswith('/vpn-admin/'): self.send_error(404); return
            data=self.rfile.read(int(self.headers.get('Content-Length','0')))
            headers={'X-VPN-Operator':'owner','Origin':'https://test.ai.esmien.ru'}
            if data: headers['Content-Type']='application/json'
            r=client.request(self.command,self.path.removeprefix('/vpn-admin'),content=data or None,headers=headers)
            self.send_response(r.status_code);self.send_header('Content-Type',r.headers.get('content-type','application/json'));self.send_header('Content-Security-Policy',"default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; connect-src 'self'");self.end_headers();self.wfile.write(r.content)
        do_GET=go;do_POST=go
    with tempfile.TemporaryDirectory(dir=base) as profile, ThreadingHTTPServer(('127.0.0.1',0),Handler) as srv:
        t=Thread(target=srv.serve_forever,daemon=True);t.start()
        chrome=subprocess.Popen(['/root/.hermes/tools/chromium-1208/chrome-linux64/chrome','--headless=new','--no-sandbox','--disable-gpu','--no-first-run','--remote-allow-origins=*','--remote-debugging-port=0','--user-data-dir='+profile, f'http://127.0.0.1:{srv.server_port}/vpn-admin/'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            pf=Path(profile)/'DevToolsActivePort'
            for _ in range(100):
                if pf.exists(): break
                time.sleep(.1)
            port=pf.read_text().splitlines()[0]; pages=json.loads(urllib.request.urlopen('http://127.0.0.1:'+port+'/json').read()); ws=websocket.create_connection(next(p['webSocketDebuggerUrl'] for p in pages if p['type']=='page'),timeout=10,suppress_origin=True)
            counter=[0]
            def js(expr):
                counter[0]+=1; ident=counter[0];ws.send(json.dumps({'id':ident,'method':'Runtime.evaluate','params':{'expression':expr,'returnByValue':True}}))
                while True:
                    r=json.loads(ws.recv())
                    if r.get('id')==ident:
                        if 'exceptionDetails' in r.get('result',{}): raise RuntimeError(str(r['result']['exceptionDetails']))
                        return r['result']['result'].get('value')
            def wait(expr):
                for _ in range(100):
                    if js(expr): return
                    time.sleep(.1)
                raise RuntimeError('Timed out: '+expr+' notice='+str(js("document.querySelector('#notice')?.innerText")))
            wait("document.body.dataset.result==='ready'")
            assert js("document.querySelectorAll('.sub-card').length") == 1
            js("document.querySelector('.pool-box input[type=checkbox]').click()")
            wait("document.querySelector('#notice').innerText.includes('Галочка сохранена')")
            wait("document.querySelector('.pool-box input[type=checkbox]').checked")
            def wait_db(predicate):
                for _ in range(100):
                    data=client.get('/api/subs',headers={'X-VPN-Operator':'owner'}).json()
                    if predicate(data): return
                    time.sleep(.1)
                raise RuntimeError('database state timed out')
            js("document.querySelectorAll('.pool-box')[0].querySelectorAll('input[type=checkbox]')[1].click()")
            wait_db(lambda d: next(n for n in d['subscriptions'][0]['nodes'] if n['node_id']=='n2')['pools']['general'])
            time.sleep(.3)
            js("document.querySelectorAll('.pool-box')[3].open=true; document.querySelectorAll('.pool-box')[3].querySelector('input[type=checkbox]').click()")
            wait_db(lambda d: next(n for n in d['subscriptions'][0]['nodes'] if n['node_id']=='n1')['pools']['lte'])
            time.sleep(.3)
            js("document.querySelector('.node-row button').click()")
            js("document.querySelector('.node-row input.inp').value='Новое имя'; document.querySelector('.node-row .btn-primary').click()")
            wait("document.querySelector('#notice').innerText.includes('Название сохранено')")
            assert js("document.querySelector('.node-row').innerText.includes('Новое имя')")
            js("document.querySelector('.route-card').open=true")
            wait("[...document.querySelectorAll('.route-card')][0].innerText.includes('Новое имя')")
            js("[...document.querySelectorAll('.route-card')][0].querySelectorAll('.role-pill')[1].click()")
            wait("document.querySelector('#notice').innerText.includes('маршрута сохранены')")
            assert client.get('/api/routes',headers={'X-VPN-Operator':'owner'}).json()['scenarios']['general']['primary']=='n1'
            js("[...document.querySelectorAll('.route-card')][0].querySelectorAll('.node-row')[0].querySelectorAll('.role-pill')[1].click()")
            for _ in range(100):
                route=client.get('/api/routes',headers={'X-VPN-Operator':'owner'}).json()['scenarios']['general']
                if route['fallbacks']==['direct']:break
                time.sleep(.1)
            assert route['fallbacks']==['direct']; time.sleep(.3)
            js("[...document.querySelectorAll('.route-card')][0].querySelectorAll('.node-row')[2].querySelectorAll('.role-pill')[1].click()")
            for _ in range(100):
                route=client.get('/api/routes',headers={'X-VPN-Operator':'owner'}).json()['scenarios']['general']
                if route['fallbacks']==['direct','n2']:break
                time.sleep(.1)
            assert route['fallbacks']==['direct','n2']; time.sleep(.3)
            js("[...document.querySelectorAll('.route-card')][0].querySelectorAll('.node-row')[2].querySelector('.ord-btn').click()")
            for _ in range(100):
                route=client.get('/api/routes',headers={'X-VPN-Operator':'owner'}).json()['scenarios']['general']
                if route['fallbacks']==['n2','direct']:break
                time.sleep(.1)
            assert route['fallbacks']==['n2','direct']
            js("document.querySelector('#add-name').value='Extra'; document.querySelector('#add-url').value='https://example.test/new'; document.querySelector('#add-btn').click()")
            wait("document.querySelector('#notice').innerText.includes('добавлена')")
            assert js("document.querySelectorAll('.sub-card').length")==2
            assert js("[...document.querySelectorAll('.sub-card')].find(c=>c.innerText.includes('Extra')).querySelector('.btn-danger').innerText")=='Удалить'
            js("[...document.querySelectorAll('.sub-card')].find(c=>c.innerText.includes('Extra')).querySelector('.sub-rename').click()")
            js("document.querySelector('.sub-name-input').value='Extra renamed'; document.querySelector('.sub-name-save').click()")
            wait("document.querySelector('#notice').innerText.includes('Название подписки сохранено')")
            assert js("[...document.querySelectorAll('.sub-toggle')].some(x=>x.innerText==='Extra renamed')")
            assert any(row['name']=='Extra renamed' for row in client.get('/api/subs',headers={'X-VPN-Operator':'owner'}).json()['subscriptions'])
            js("[...document.querySelectorAll('.sub-card')].find(c=>c.innerText.includes('Extra')).querySelector('.sub-rename').click()")
            js("document.querySelector('.sub-name-input').value='Not saved'; document.querySelector('.sub-name-cancel').click()")
            assert js("[...document.querySelectorAll('.sub-toggle')].some(x=>x.innerText==='Extra renamed')")
            js("[...document.querySelectorAll('.sub-card')].find(c=>c.innerText.includes('Extra')).querySelector('.sub-toggle').click()")
            js("(()=>{const c=[...document.querySelectorAll('.sub-card')].find(x=>x.innerText.includes('Extra')); c.querySelector('.sub-url-bar input').value='https://example.test/replaced';c.querySelector('.sub-url-bar button').click()})()")
            wait("document.querySelector('#notice').innerText.includes('URL заменён')")
            js("[...document.querySelectorAll('.sub-card')].find(c=>c.innerText.includes('Extra')).querySelectorAll('.sub-actions button')[1].click()")
            wait("document.querySelector('#notice').innerText.includes('Мок обновления')")
            js("[...document.querySelectorAll('.sub-card')].find(c=>c.innerText.includes('Extra')).querySelector('.sub-actions .btn-danger').click()")
            js("(()=>{const c=[...document.querySelectorAll('.sub-card')].find(x=>x.innerText.includes('Extra'));c.querySelector('.cfm-input').value='DELETE';c.querySelector('.sub-actions .btn-danger').click()})()")
            wait("document.querySelector('#notice').innerText.includes('удалена')")
            assert js("document.querySelectorAll('.sub-card').length")==1
            # Prove reload retains all successful settings in the actual SQLite database.
            js('location.reload()');wait("document.body.dataset.result==='ready'")
            rows=client.get('/api/subs',headers={'X-VPN-Operator':'owner'}).json()['subscriptions']; a=next(x for x in rows if x['sub_id']=='s')
            assert a['nodes'][0]['pools']['general'] and a['nodes'][0]['custom_name']=='Новое имя'
            assert len(rows)==1
            print('Chromium mock smoke: pool, rename, priority, add, replace URL, mock refresh and delete; settings persisted after reload.')
            ws.close()
        finally:
            chrome.terminate();chrome.wait(timeout=10);srv.shutdown();t.join();client.close();engine.dispose()
