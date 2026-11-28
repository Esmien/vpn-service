from fastapi.testclient import TestClient
from router.admin import read_store as s
from router.admin.mock_store import MockStore
from router.admin.read_api import create_app
from router.admin.snapshot_import import import_snapshot
import json


def test_mock_changes_survive_reopen_and_are_isolated(tmp_path):
    snapshot=tmp_path/'snapshot.json'
    snapshot.write_text(json.dumps({'subscriptions':[{'id':'s','name':'Provider','status':'active','url':'https://example.test/private','nodes':[{'node_id':'n1','original_name':'A','link':'vless://SECRET@example.test:443','pools':{}},{'node_id':'n2','original_name':'B','link':'vless://SECRET@example.test:443','pools':{}}]}], 'policy':{'gemini':['nl','p15'],'ru':['ru1']},'outbounds':[]}))
    path=tmp_path/'private'/'mock.sqlite3'; engine=s.open_store(path,initialize=True); import_snapshot(engine,snapshot)
    app=create_app(engine,operator='owner',mock_store=MockStore(engine)); client=TestClient(app)
    h={'X-VPN-Operator':'owner','Origin':'https://test.ai.esmien.ru'}
    def post(data): return client.post('/api/mock',json=data,headers=h)
    assert client.post('/api/mock',json={'action':'refresh','sub_id':'s'}).status_code==403
    assert client.post('/api/mock',json={'action':'refresh','sub_id':'s'},headers={**h,'Origin':'https://evil.example'}).status_code==403
    for node in ('n1','n2'):
        assert post({'action':'node','sub_id':'s','node_id':node,'pools':{'general':True,'lte':True}}).status_code==200
    assert post({'action':'node','sub_id':'s','node_id':'n1','rename':True,'custom_name':'Тест'}).status_code==200
    assert post({'action':'route','scenario':'general','primary':'direct','fallbacks':['n1','n2'],'version':1}).status_code==200
    assert post({'action':'route','scenario':'general','primary':'direct','fallbacks':['n2','n1'],'version':2}).status_code==200
    assert client.get('/api/routes',headers=h).json()['scenarios']['general']['fallbacks']==['n2','n1']
    assert post({'action':'route','scenario':'general','primary':'n1','fallbacks':['n2'],'version':3}).status_code==200
    assert post({'action':'route','scenario':'general','primary':'n2','fallbacks':[],'version':1}).status_code==400
    assert post({'action':'node','sub_id':'s','node_id':'n1','pools':{'general':False}}).status_code==200
    route=client.get('/api/routes',headers=h).json()['scenarios']['general']
    assert route['primary']=='direct' and route['fallbacks']==['n2']
    assert post({'action':'add','name':'Extra','url':'https://example.test/secret2','tg_url':'@example'}).status_code==200
    rows=client.get('/api/subs',headers=h).json()['subscriptions']; extra=next(row for row in rows if row['name']=='Extra')
    assert post({'action':'rename_sub','sub_id':extra['sub_id'],'name':'  Новое название  ','version':extra['version']}).status_code==200
    rows=client.get('/api/subs',headers=h).json()['subscriptions']; renamed=next(row for row in rows if row['sub_id']==extra['sub_id'])
    assert renamed['name']=='Новое название' and renamed['version']==extra['version']+1
    assert post({'action':'rename_sub','sub_id':extra['sub_id'],'name':'Устаревшая правка','version':extra['version']}).status_code==400
    assert post({'action':'rename_sub','sub_id':extra['sub_id'],'name':'   ','version':renamed['version']}).status_code==400
    assert post({'action':'replace_url','sub_id':extra['sub_id'],'url':'https://example.test/secret3'}).status_code==200
    assert post({'action':'refresh','sub_id':extra['sub_id']}).status_code==200
    assert post({'action':'delete','sub_id':extra['sub_id'],'confirm':'wrong'}).status_code==400
    assert post({'action':'delete','sub_id':extra['sub_id'],'confirm':'DELETE'}).status_code==200
    assert post({'action':'route','scenario':'lte','primary':'n1','version':1}).status_code==422
    assert 'SECRET' not in client.get('/api/subs',headers=h).text
    client.close(); engine.dispose()
    reopened=s.open_store(path)
    with s.ReadUnitOfWork(reopened) as r:
        subscriptions=r.subscriptions().subscriptions
        assert len(subscriptions)==1
        nodes={n.node_id:n for n in subscriptions[0].nodes}
        assert nodes['n1'].custom_name=='Тест' and nodes['n1'].pools.lte and not nodes['n1'].pools.general
        assert nodes['n2'].pools.general and r.routes().scenarios['general'].fallbacks==['n2']
    reopened.dispose()
