import json
from pathlib import Path
from fastapi.testclient import TestClient
from router.admin import read_store
from router.admin.read_api import create_app
from router.admin.preview_probe import ProbeService
from router.admin.snapshot_import import import_snapshot


def test_snapshot_and_probe_boundaries(tmp_path):
    path=tmp_path/'snapshot.json'
    path.write_text(json.dumps({'subscriptions':[{'id':'s','name':'Demo','status':'active','url':'https://example.test/secret','nodes':[{'node_id':'n','original_name':'N','link':'vless://secret@example.test:443','pools':{'lte':True}}]}], 'policy':{'gemini':['nl','p15'],'ru':['ru1']}, 'outbounds':[{'tag':'direct','protocol':'freedom'}]}))
    engine=read_store.open_store(tmp_path/'private'/'test.sqlite3',initialize=True)
    import_snapshot(engine,path)
    p=ProbeService(path)
    def fake_one(node_id, scenario):
        return {'status':'unknown','latency_ms':None,'checked_at':'2026-11-23T21:00:00+00:00'}
    p._one = fake_one
    client=TestClient(create_app(engine,operator='owner',probe_service=p))
    h={'X-VPN-Operator':'owner','Origin':'https://test.ai.esmien.ru'}
    data={'scenario':'general','ids':['n']}
    assert client.post('/api/routes/probe',json=data).status_code==403
    assert client.post('/api/routes/probe',json=data,headers={**h,'Origin':'https://evil.example'}).status_code==403
    assert client.post('/api/routes/probe',json={'scenario':'general','ids':['missing']},headers=h).status_code==400
    r=client.post('/api/routes/probe',json=data,headers=h)
    assert r.status_code==200
    job=r.json()['job_id']
    import time
    result = {}
    for _ in range(100):
        result=client.get('/api/routes/probe/'+job,headers=h).json()
        if result['status']=='complete': break
        time.sleep(.01)
    assert result['status']=='complete' and result['results']['n']['status']=='unknown'
    assert 'secret' not in client.get('/api/subs',headers=h).text
    assert client.post('/api/routes/apply',json={},headers=h).status_code==404
    engine.dispose()
