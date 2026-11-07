"""TAM 360 cloud Calculator agent (runs in Azure Container Apps).

- Microsoft Entra sign-in (Container Apps authentication) sits in front of everything: only your organisation's users reach this app.
- A real Chromium runs on a virtual display; the "live browser view" (/vnc/) lets you sign in to the dedicated
  calculator Microsoft account once. Only the sign-in cookies are kept (Azure Files, /data/session.json).
- Your Azure AI Foundry deployment drives the official Azure Pricing Calculator through calculator_agent.py
  (managed identity, no keys). The tested save/share code returns the share link.
"""
from __future__ import annotations

import datetime as dt, html, json, logging, os, re, threading, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, parse_qs
from urllib.request import Request, urlopen

import calculator_agent as ca
import legacy_share

DATA = Path(os.environ.get('DATA_DIR', '/data'))
JOBS_DIR = DATA / 'jobs'
SESSION = DATA / 'session.json'
ENDPOINT = os.environ.get('FOUNDRY_ENDPOINT', '').rstrip('/')
DEPLOYMENT = os.environ.get('FOUNDRY_DEPLOYMENT', '')
CLIENT_ID = os.environ.get('AZURE_CLIENT_ID') or None
REGION = os.environ.get('DEFAULT_REGION', 'UAE North')
MAX_STEPS = int(os.environ.get('MAX_STEPS', '60'))
VISION = os.environ.get('VISION', '1') != '0'
HEADLESS = os.environ.get('HEADLESS', '0') == '1'
ALLOW_ANON = os.environ.get('ALLOW_ANON', '0') == '1'       # local testing only
PORT = int(os.environ.get('PORT', '8080'))
LOCK = threading.Lock()
RUN = {'thread': None, 'kind': None}
SIGNIN = {'status': 'unknown', 'message': '', 'time_utc': None}
_TOKEN = {'value': None, 'exp': 0}
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')


# ------------------------------------------------------------------ Azure AI Foundry (managed identity)
def token():
    if _TOKEN['value'] and time.time() < _TOKEN['exp'] - 300:
        return _TOKEN['value']
    from azure.identity import ManagedIdentityCredential, DefaultAzureCredential
    cred = ManagedIdentityCredential(client_id=CLIENT_ID) if os.environ.get('IDENTITY_ENDPOINT') else DefaultAzureCredential()
    t = cred.get_token('https://cognitiveservices.azure.com/.default')
    _TOKEN.update(value=t.token, exp=t.expires_on)
    return t.token


def foundry_call(messages, on_wait=None, max_tokens=1500):
    if not ENDPOINT or not DEPLOYMENT:
        raise ValueError('FOUNDRY_ENDPOINT and FOUNDRY_DEPLOYMENT are not configured.')
    data = json.dumps({'model': DEPLOYMENT, 'messages': messages, 'max_completion_tokens': max_tokens}).encode()
    for attempt in range(8):
        req = Request(ENDPOINT + '/openai/v1/chat/completions', data=data,
                      headers={'Authorization': 'Bearer ' + token(), 'Content-Type': 'application/json'})
        try:
            with urlopen(req, timeout=180) as r:
                return json.load(r)['choices'][0]['message'].get('content') or ''
        except HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < 7:
                ra = e.headers.get('retry-after-ms') or ''
                wait = (int(ra) / 1000 if ra.isdigit() else None) or (float(e.headers.get('Retry-After')) if (e.headers.get('Retry-After') or '').replace('.', '', 1).isdigit() else min(60, 5 * 2 ** attempt))
                wait = max(2, min(90, wait))
                if on_wait: on_wait(f'Azure AI Foundry is busy ({e.code}). Waiting {wait:.0f}s…')
                time.sleep(wait); continue
            if e.code in (401, 403):
                raise ValueError('Foundry refused the app identity (HTTP %d). Give the container app\'s managed identity the "Cognitive Services OpenAI User" role on the Foundry resource.' % e.code)
            raise
        except (URLError, OSError):
            if attempt < 7:
                if on_wait: on_wait('Connection to Azure AI Foundry dropped. Retrying…')
                time.sleep(min(30, 3 * 2 ** attempt)); continue
            raise ValueError('Could not reach Azure AI Foundry after several retries.')


# ------------------------------------------------------------------ jobs
def save(d, s):
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / 'status.tmp'; tmp.write_text(json.dumps(s, indent=1), encoding='utf-8'); tmp.replace(d / 'status.json')


def agent(request, d, notify, name='Azure estimate'):
    return ca.CalculatorAgent(request, lambda msgs: foundry_call(msgs, on_wait=lambda m: notify(m)), d, SESSION, legacy_share.share_estimate,
                              name=name, default_region=REGION, max_steps=MAX_STEPS, vision=VISION, notify=notify,
                              launch=lambda p: p.chromium.launch(headless=HEADLESS, args=['--no-first-run', '--test-type']))


def busy():
    t = RUN['thread']
    return t is not None and t.is_alive()


def start_job(request, name, user):
    request = (request or '').strip()
    if not 10 <= len(request) <= 6000:
        raise ValueError('Describe the services (10–6000 characters).')
    with LOCK:
        if busy():
            raise ValueError('The cloud browser is busy with another job. Try again when it finishes.')
        job = uuid.uuid4().hex[:10]; d = JOBS_DIR / job
        s = {'status': 'running', 'message': 'Starting the cloud browser…', 'request': request[:1500], 'name': (name or 'Azure estimate')[:120],
             'user': user, 'created_utc': now(), 'log': []}
        save(d, s)
        def notify(m, step=None):
            s['message'] = str(m)[:300]
            if step: s['log'] = (s['log'] + [{'step': step['step'], 'thought': step['thought'][:200], 'results': [r[:160] for r in step['results']]}])[-40:]
            save(d, s)
        def go():
            try:
                r = agent(request, d, notify, s['name']).run()
                s.update(status=r['status'], url=r.get('url'), summary=r.get('summary', ''), modules=r.get('modules', []), total=r.get('total'), steps=r.get('steps'),
                         message='Done. Share link ready.' if r['status'] == 'completed' else 'Stopped: ' + r.get('error', 'unknown error'))
                if r['status'] == 'completed':
                    SIGNIN.update(status='completed', message='Signed in (remembered).', time_utc=now())
            except Exception as e:
                s.update(status='failed', message='Stopped: ' + (str(e) if isinstance(e, ValueError) else type(e).__name__ + ': ' + str(e)[:200]))
            save(d, s); logging.info('job=%s status=%s', job, s['status'])
        RUN.update(thread=threading.Thread(target=go, daemon=True), kind='job'); RUN['thread'].start()
    return {'job': job, 'status': 'Started. Watch progress below; open the live browser view to see it work.'}


def start_signin():
    with LOCK:
        if busy():
            raise ValueError('The cloud browser is busy. Try again when the current job finishes.')
        SIGNIN.update(status='running', message='Opening the calculator in the cloud browser. Click "Open live browser view" and sign in there.', time_utc=now())
        def note(m, **k):
            SIGNIN['message'] = str(m)[:300]
        def go():
            r = ca.CalculatorAgent('sign in', None, DATA / 'signin', SESSION, None, notify=note,
                                   launch=lambda p: p.chromium.launch(headless=HEADLESS, args=['--no-first-run', '--test-type'])).sign_in_only()
            SIGNIN.update(status=r['status'], time_utc=now(),
                          message='Signed in. The calculator account is remembered; you can build estimates.' if r['status'] == 'completed' else 'Not signed in: ' + r.get('error', ''))
        RUN.update(thread=threading.Thread(target=go, daemon=True), kind='signin'); RUN['thread'].start()
    return {'status': SIGNIN['message']}


def jobs():
    out = []
    if JOBS_DIR.is_dir():
        for d in sorted((x for x in JOBS_DIR.iterdir() if x.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True)[:10]:
            try:
                s = json.loads((d / 'status.json').read_text(encoding='utf-8'))
            except (FileNotFoundError, json.JSONDecodeError):
                continue
            if s.get('status') == 'running' and not (busy() and RUN['kind'] == 'job'):
                s.update(status='failed', message='Stopped (the container restarted). Run it again.')
            s['id'] = d.name
            out.append(s)
    return out


# ------------------------------------------------------------------ web
class Handler(BaseHTTPRequestHandler):
    server_version = 'TAM360Calc/1'

    def log_message(self, fmt, *args):
        logging.info('http %s', fmt % args)

    def user(self):
        u = self.headers.get('X-MS-CLIENT-PRINCIPAL-NAME')
        if not u and not ALLOW_ANON:
            return None
        return u or 'local-test'

    def send(self, code, body, ctype='application/json'):
        data = body if isinstance(body, bytes) else (json.dumps(body) if ctype == 'application/json' else body).encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype + ('; charset=utf-8' if 'json' in ctype or 'html' in ctype else ''))
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store'); self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY'); self.send_header('Referrer-Policy', 'same-origin')
        self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
        self.end_headers(); self.wfile.write(data)

    def do_GET(self):
        if self.path == '/healthz':
            return self.send(200, {'ok': True})
        u = self.user()
        if not u:
            return self.send(401, {'error': 'Sign-in required (Microsoft Entra).'})
        path = urlparse(self.path).path
        if path == '/':
            return self.send(200, PAGE, 'text/html')
        if path == '/api/state':
            return self.send(200, {'user': u, 'busy': busy(), 'busy_kind': RUN['kind'] if busy() else None,
                                   'signin': dict(SIGNIN, remembered=SESSION.is_file()), 'jobs': jobs(), 'region': REGION,
                                   'model': DEPLOYMENT})
        return self.send(404, {'error': 'Not found'})

    def do_POST(self):
        u = self.user()
        if not u:
            return self.send(401, {'error': 'Sign-in required (Microsoft Entra).'})
        origin = self.headers.get('Origin')
        if self.headers.get('X-Requested-With') != 'tam360' or (origin and urlparse(origin).netloc != self.headers.get('Host')):
            return self.send(403, {'error': 'Request blocked (cross-site).'})
        try:
            n = int(self.headers.get('Content-Length', '0'))
            if not 0 <= n <= 20000:
                raise ValueError('Request too large.')
            body = json.loads(self.rfile.read(n) or b'{}')
            if self.path == '/api/jobs':
                return self.send(200, start_job(str(body.get('request', '')), str(body.get('name', '')), u))
            if self.path == '/api/signin':
                return self.send(200, start_signin())
            return self.send(404, {'error': 'Not found'})
        except ValueError as e:
            return self.send(400, {'error': str(e)})
        except Exception as e:
            logging.exception('request failed')
            return self.send(500, {'error': type(e).__name__})


PAGE = r'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TAM 360 · Azure Calculator agent</title>
<style>
:root{--bg:#0b1220;--panel:#121c2e;--line:#26344d;--fg:#e8eef8;--muted:#93a3bb;--accent:#4fd1b5;--warn:#f1b44c;--bad:#ff7a85}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 "Segoe UI",system-ui,sans-serif}
main{max-width:1100px;margin:auto;padding:24px max(16px,env(safe-area-inset-left))}
h1{font-size:22px;margin:0 0 4px}small,.muted{color:var(--muted)}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:18px;margin:16px 0}
textarea,input{width:100%;background:#0b1425;color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:10px;font:inherit}
textarea{min-height:90px}button,.btn{background:var(--accent);color:#06221b;border:0;border-radius:8px;padding:10px 16px;font-weight:600;cursor:pointer;text-decoration:none;display:inline-block}
button.secondary,.btn.secondary{background:#22324c;color:var(--fg)}button:disabled{opacity:.5;cursor:default}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:10px}.pill{border-radius:20px;padding:2px 10px;font-size:13px;background:#22324c}
.ok{background:#163d38;color:#83e5bd}.bad{background:#4a1d24;color:#ffb3bd}.job{border-top:1px solid var(--line);padding:12px 0}
.link{display:flex;gap:8px;flex-wrap:wrap}.link input{flex:1;min-width:240px}ol{margin:6px 0;padding-left:20px;color:var(--muted);font-size:13.5px}
</style></head><body><main>
<h1>Azure Calculator agent</h1><small id="who">Loading…</small>
<div class="panel"><b>Step 1 · Calculator account sign-in (one time)</b>
 <div class="row"><span class="pill" id="sstate">Checking…</span><button class="secondary" id="signin">Sign in to the calculator account</button>
 <a class="btn secondary" id="vnc" href="vnc/vnc.html?autoconnect=1&amp;resize=scale&amp;path=vnc/websockify" target="_blank" rel="noopener">Open live browser view</a></div>
 <p class="muted" id="smsg">Click "Sign in", then "Open live browser view" and sign in there with the dedicated calculator Microsoft account (password + MFA, "Stay signed in: Yes"). Nobody else sees the password; only the sign-in cookies are kept.</p></div>
<div class="panel"><b>Step 2 · Build an estimate</b>
 <textarea id="req" placeholder="e.g. UAE North: Azure Firewall Standard, DDoS Protection, Application Gateway WAF v2, 3 x D4s v5 Windows VMs"></textarea>
 <div class="row"><input id="name" placeholder="Estimate name" style="max-width:320px"><button id="go">Build &amp; get share link</button></div>
 <p class="muted" id="msg"></p></div>
<div class="panel"><b>Estimates</b><div id="jobs"><p class="muted">None yet.</p></div></div>
</main><script>
const $=id=>document.getElementById(id);const el=(t,x,c)=>{const e=document.createElement(t);if(x!=null)e.textContent=x;if(c)e.className=c;return e};
const q=new URLSearchParams(location.search);if(q.get('request'))$('req').value=q.get('request').slice(0,6000);if(q.get('name'))$('name').value=q.get('name').slice(0,120);
async function post(p,b){const r=await fetch(p,{method:'POST',headers:{'Content-Type':'application/json','X-Requested-With':'tam360'},body:JSON.stringify(b||{})});const j=await r.json();if(!r.ok)throw Error(j.error||r.status);return j}
$('signin').onclick=async()=>{try{const j=await post('api/signin');$('smsg').textContent=j.status;window.open($('vnc').href,'_blank','noopener')}catch(e){$('smsg').textContent=e.message}};
$('go').onclick=async()=>{try{const j=await post('api/jobs',{request:$('req').value,name:$('name').value});$('msg').textContent=j.status;refresh()}catch(e){$('msg').textContent=e.message}};
async function refresh(){try{const s=await (await fetch('api/state')).json();$('who').textContent='Signed in as '+s.user+' · model '+s.model+' · default region '+s.region;
 const si=s.signin;const ok=si.status==='completed'||(si.remembered&&si.status!=='failed'&&si.status!=='running');$('sstate').textContent=ok?'Calculator account remembered':si.status==='running'?'Waiting for sign-in in live view…':'Not signed in yet';$('sstate').className='pill '+(ok?'ok':si.status==='running'?'':'bad');if(si.message)$('smsg').textContent=si.message;
 $('go').disabled=s.busy;$('signin').disabled=s.busy;const box=$('jobs');box.replaceChildren();if(!s.jobs.length){box.append(el('p','None yet.','muted'));return}
 s.jobs.forEach((j,n)=>{const w=el('div','','job');const h=el('div','','row');h.append(el('b',j.name),el('span',j.status==='completed'?'Share link ready':j.status==='failed'?'Stopped':'Working','pill '+(j.status==='completed'?'ok':j.status==='failed'?'bad':'')),el('small',(j.created_utc||'').replace('T',' ').slice(0,16)+' UTC · '+(j.user||'')));w.append(h,el('p',j.message,'muted'));
  if(j.url){const l=el('div','','link');const i=el('input');i.value=j.url;i.readOnly=true;const c=el('button','Copy','secondary');c.onclick=async()=>{try{await navigator.clipboard.writeText(j.url);c.textContent='Copied'}catch(e){i.select()}};const a=el('a','Open','btn secondary');a.href=j.url;a.target='_blank';a.rel='noopener';l.append(i,c,a);w.append(l)}
  if(j.modules&&j.modules.length){const ul=el('ul');j.modules.forEach(m=>ul.append(el('li',m)));w.append(el('small','In the estimate'+(j.total?' · total '+j.total+'/month':'')),ul)}
  if(n===0&&j.log&&j.log.length){const d=el('details');d.append(el('summary','What the agent did ('+j.log.length+' steps)'));const ol=el('ol');j.log.slice(-15).forEach(x=>ol.append(el('li',x.thought+' → '+x.results.join('; '))));d.append(ol);w.append(d)}
  box.append(w)})}catch(e){$('who').textContent='Cannot reach the service: '+e.message}}
setInterval(refresh,3000);refresh();
</script></body></html>'''


def main():
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    if SESSION.is_file():
        SIGNIN.update(status='remembered', message='A calculator sign-in is remembered. If a job asks for sign-in, open the live browser view.')
    srv = ThreadingHTTPServer(('127.0.0.1' if os.environ.get('BIND_LOCAL', '1') == '1' else '0.0.0.0', PORT), Handler)
    logging.info('worker listening on %s', PORT)
    srv.serve_forever()


if __name__ == '__main__':
    main()
