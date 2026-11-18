import sys,json,pathlib,urllib.parse,concurrent.futures,subprocess,tempfile,os,socket,time,re
sys.path.insert(0,'/root/vpn-router')
from probe_sub import build
links=json.loads(pathlib.Path('/root/vpn-router/provider-subscriptions/subscription-1.json').read_text())
marker=re.compile(r'this\.gbar_=\{CONFIG:\[\[\[0,"www\.gstatic\.com","[^"]*","([a-z]{2,3}(?:\.[a-z]{2})?)","([a-z]{2})"')
def probe(item):
 i,link=item;u=urllib.parse.urlparse(link);name=urllib.parse.unquote(u.fragment).strip();q=urllib.parse.parse_qs(u.query)
 s=socket.socket();s.bind(('127.0.0.1',0));port=s.getsockname()[1];s.close()
 try:
  if u.scheme=='hysteria2':
   conf={'server':f'{u.hostname}:{u.port}', 'auth':urllib.parse.unquote(u.username or ''), 'tls':{'sni':q.get('sni',[u.hostname])[0]},'socks5':{'listen':f'127.0.0.1:{port}'}}
   cmd=['/usr/local/bin/hysteria','client','-c']; suffix='.json'
  else:
   conf=build(link,port)
   if not conf:return i,name,'unsupported'
   cmd=['/usr/local/bin/xray','run','-config'];suffix='.json'
  with tempfile.NamedTemporaryFile(mode='w',dir=os.environ['TMPDIR'],suffix=suffix) as f:
   os.chmod(f.name,0o600);json.dump(conf,f);f.flush();p=subprocess.Popen(cmd+[f.name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
   try:
    time.sleep(2 if u.scheme=='hysteria2' else 1)
    if p.poll() is not None:return i,name,'client start failed'
    r=subprocess.run(['curl','-sS','--max-time','22','-H','Accept-Language: en','--socks5-hostname',f'127.0.0.1:{port}','https://gemini.google.com/app'],capture_output=True,text=True)
    m=marker.search(r.stdout)
    return i,name,('marker='+m.group(1) if m else f'no marker curl={r.returncode} bytes={len(r.stdout)}')
   finally:
    p.terminate()
    try:p.wait(timeout=3)
    except subprocess.TimeoutExpired:p.kill();p.wait()
 except (ValueError,KeyError,OSError) as e:return i,name,'error '+type(e).__name__
with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool: results=list(pool.map(probe,enumerate(links)))
for i,n,res in results:print(f'{i:2} {n}: {res}',flush=True)
print('TOTAL',len(results),'markers',sum(x[2].startswith('marker=') for x in results))
