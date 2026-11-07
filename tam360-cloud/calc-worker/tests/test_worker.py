"""Offline tests for the cloud worker: auth gate, CSRF gate, page, full job on a mock calculator with a stand-in model."""
import json, os, re, sys, tempfile, threading, time, unittest
from pathlib import Path
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
TMP = tempfile.mkdtemp()
os.environ.update(DATA_DIR=TMP, HEADLESS='1', FOUNDRY_ENDPOINT='https://x.services.ai.azure.com', FOUNDRY_DEPLOYMENT='m')
import worker  # noqa: E402
import calculator_agent as ca  # noqa: E402

MOCK = (HERE / 'mock_calculator.html').as_uri()


def fake_llm(msgs):
    text = msgs[-1]['content'] if isinstance(msgs[-1]['content'], str) else msgs[-1]['content'][0]['text']
    ctl = re.findall(r'\[(\d+)\] ([^|\n]+)\| ([^|\n]+)(.*)', text)
    find = lambda name: next((int(i) for i, k, lab, r in ctl if lab.strip() == name), None)
    if 'Azure Firewall' not in text.split('ESTIMATE SO FAR:')[1].split('\n')[0]:
        add = find('Add to estimate: Azure Firewall')
        return json.dumps({'thought': 'add', 'actions': [{'do': 'click', 'id': add}] if add else [{'do': 'search', 'text': 'Firewall'}]})
    return json.dumps({'thought': 'done', 'actions': [], 'done': True, 'summary': 'Azure Firewall'})


class Worker(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        worker.foundry_call = lambda msgs, on_wait=None, max_tokens=0: fake_llm(msgs)
        real = worker.agent
        def agent(request, d, notify, name='x'):
            a = real(request, d, notify, name); a.url = MOCK; a.vision = False; return a
        worker.agent = agent
        cls.srv = ThreadingHTTPServer(('127.0.0.1', 0), worker.Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = f'http://127.0.0.1:{cls.srv.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def req(self, path, body=None, headers=None):
        h = {'X-MS-CLIENT-PRINCIPAL-NAME': 'varun@example.com', **(headers or {})}
        data = json.dumps(body).encode() if body is not None else None
        if data: h['Content-Type'] = 'application/json'
        return json.load(urlopen(Request(self.url + path, data=data, headers=h)))

    def test_requires_entra_identity(self):
        with self.assertRaises(HTTPError) as cm:
            urlopen(self.url + '/api/state')
        self.assertEqual(cm.exception.code, 401)
        self.assertEqual(json.load(urlopen(self.url + '/healthz')), {'ok': True})

    def test_blocks_cross_site_posts(self):
        with self.assertRaises(HTTPError) as cm:
            self.req('/api/jobs', {'request': 'Azure Firewall Standard'})
        self.assertEqual(cm.exception.code, 403)
        with self.assertRaises(HTTPError) as cm:
            self.req('/api/jobs', {'request': 'Azure Firewall Standard'}, {'X-Requested-With': 'tam360', 'Origin': 'https://evil.example'})
        self.assertEqual(cm.exception.code, 403)

    def test_page_and_full_job(self):
        h = Request(self.url + '/', headers={'X-MS-CLIENT-PRINCIPAL-NAME': 'varun@example.com'})
        page = urlopen(h).read().decode()
        self.assertIn('Open live browser view', page)
        r = self.req('/api/jobs', {'request': 'UAE North: Azure Firewall Standard', 'name': 'Test'}, {'X-Requested-With': 'tam360'})
        job = r['job']
        for _ in range(120):
            s = self.req('/api/state')
            j = next(x for x in s['jobs'] if x['id'] == job)
            if j['status'] != 'running': break
            time.sleep(0.5)
        self.assertEqual(j['status'], 'completed', j['message'])
        self.assertEqual(j['url'], 'https://azure.com/e/abc123DEF')
        self.assertEqual(j['user'], 'varun@example.com')
        self.assertTrue(Path(TMP, 'session.json').is_file())       # calculator sign-in cookies remembered


if __name__ == '__main__':
    unittest.main()
