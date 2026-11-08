import copy
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from relay import ask, config, reader
from relay.cli import main


class ReaderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.pdf = self.root / 'sample.pdf'
        self.pdf.write_bytes(b'%PDF-test')
        self.conf = copy.deepcopy(config.DEFAULTS)
        runner = self.root / 'runner.py'
        runner.write_text("print('답변 $x_i$\\n검색어: graph neural networks')")
        self.conf['ask']['easy'] = [[sys.executable, str(runner)]]
        self.conf['ask']['deep'] = [[sys.executable, str(runner)]]
        def capture(address, handler):
            self.assertEqual(address, ('127.0.0.1', 0))
            self.handler = handler
            return None
        with patch.object(ask, 'pdf_pages', return_value=['first page', 'second page context']), patch.object(reader, 'ThreadingHTTPServer', side_effect=capture):
            reader.create_server(self.root, self.conf, self.pdf)

    def request(self, path, payload=None):
        body = json.dumps(payload).encode() if payload is not None else b''
        method = 'POST' if payload is not None else 'GET'
        request = f'{method} {path} HTTP/1.0\r\nContent-Length: {len(body)}\r\n\r\n'.encode() + body
        output = io.BytesIO()

        class Connection:
            def sendall(self, data):
                output.write(data)

            def makefile(self, mode, *args):
                return io.BytesIO(request) if mode == 'rb' else output
        # HTTP/1.0 uses the write file object directly; preserve it for inspection.
        class Output(io.BytesIO):
            def close(self):
                pass
        output = Output()
        server = type('Server', (), {'server_port': 12345})()
        self.handler(Connection(), ('127.0.0.1', 12345), server)
        headers, data = output.getvalue().split(b'\r\n\r\n', 1)
        code = int(headers.split()[1])
        if code >= 400:
            raise urllib.error.HTTPError(path, code, 'error', {}, io.BytesIO(data))
        return data

    def post(self, payload):
        return json.loads(self.request('/ask', payload))

    def test_ask_json_notebook_and_page_context(self):
        with patch.object(ask, 'professor_prompt', wraps=ask.professor_prompt) as prompt:
            result = self.post(dict(selection='selected', page=2, question='q', mode='research'))
        self.assertIn('second page context', prompt.call_args.kwargs['context'])
        self.assertEqual(set(result), {'markdown', 'runner', 'failures', 'links'})
        self.assertEqual(result['runner'], sys.executable)
        self.assertIn('graph%20neural%20networks', result['links'][0]['google_scholar'])
        qa = self.root / self.conf['ask']['notes'] / 'sample' / 'qa.md'
        self.assertIn('sample.pdf p.2', qa.read_text())
        self.assertIn('Semantic Scholar', qa.read_text())

    def test_pdf_exact_path_only(self):
        self.assertEqual(self.request('/pdf'), self.pdf.read_bytes())
        for path in ['/pdf/../runner.py', '/runner.py', '/pdf?path=/etc/passwd', '/%2e%2e/etc/passwd']:
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request(path)
            self.assertEqual(error.exception.code, 404)
            error.exception.close()

    def test_validation_and_runner_failure(self):
        for values in [dict(page=0), dict(page=True), dict(mode='invalid'), dict(selection=[]), dict(page=3)]:
            payload = dict(selection='x', page=1, question='', mode='explain')
            payload.update(values)
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.post(payload)
            self.assertEqual(error.exception.code, 400)
            error.exception.close()
        with patch.object(ask, 'answer', side_effect=RuntimeError('codex: timeout')):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.post(dict(selection='x', page=1))
            result = json.load(error.exception)
            self.assertEqual(error.exception.code, 502)
            self.assertIn('codex: timeout', result['failures'])
            error.exception.close()

    def test_prompts_and_links(self):
        required = {'explain': ['한 줄 요약', '직관/비유', '정확한 의미', '기호 표', 'mermaid', '확인 질문'],
                    'research': ['2~4개', '분야/방법/핵심 아이디어', '확인 필요', 'DOI', '검색어'],
                    'learn': ['선수 지식 1~3개', '다음에 공부할 것 1~3개', '흔한 오해 1개'],
                    'deep': ['유도·증명은 단계별로']}
        for mode, phrases in required.items():
            prompt = ask.professor_prompt(mode, question='q', selection='s', where='p.1', context='context')
            for phrase in phrases:
                self.assertIn(phrase, prompt)
        links = ask.search_links('검색어: a & b\n- **검색어**: 한글 test')
        self.assertEqual(len(links), 2)
        self.assertIn('a%20%26%20b', links[0]['semantic_scholar'])
        self.assertEqual(ask.search_links('no search terms'), [])

    def test_explicit_modes_do_not_auto_escalate(self):
        with patch.object(ask, 'answer', return_value=('ok', 'stub', {}, [])) as answer:
            for mode in ask.MODE_PROMPTS:
                ask.ask(self.root, self.conf, '∑ x_i = ∫ y', '증명', str(self.pdf),
                        mode=mode, page=1, pages=['context'])
                self.assertEqual(answer.call_args.args[2], mode == 'deep')

    def test_viewer_and_cli(self):
        html = self.request('/').decode()
        for part in ['pdfjs-dist@3.11.174', 'renderTextLayer', '@@MATH', 'spinner', 'data-mode="research"', 'width:40%']:
            self.assertIn(part, html)
        with patch.object(reader, 'serve') as serve:
            self.assertEqual(main(['read', str(self.pdf), '--no-open', '--port', '9123']), 0)
            self.assertEqual(serve.call_args.args[2:], (str(self.pdf), 9123, False))
