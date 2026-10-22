"""Loopback-only PDF professor, using the existing ask routing and notebook."""
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from relay import ask


def viewer_html() -> str:
    # Use exactly the same math protection and answer rendering as relay ask.
    template = ask.render_html(dict(doc='', where='', runner='', time='', question='', selection=''), '')
    head = template.split('</head>')[0]
    renderer = template.split('const math = [];', 1)[1].split('</script>', 1)[0]
    renderer = ('const math = [];' + renderer).replace('DATA.answer', 'markdown').replace(
        'const el = document.getElementById("answer");', '').replace('mermaid.run();', 'await mermaid.run({nodes: el.querySelectorAll(".mermaid")});')
    # The inherited markdown renderer permits HTML. Strip active HTML and unsafe URLs
    # before attaching generated output to the local viewer.
    renderer = renderer.replace('el.innerHTML = out;', '''const parsed = new DOMParser().parseFromString(out, 'text/html');
parsed.body.querySelectorAll('script,style,iframe,object,embed,form,input,button,link,meta,svg,math').forEach(n => n.remove());
parsed.body.querySelectorAll('*').forEach(n => [...n.attributes].forEach(a => {
 if (a.name.startsWith('on') || !['class','href','title','colspan','rowspan'].includes(a.name) ||
 (a.name === 'href' && !/^https?:\\/\\//i.test(a.value))) n.removeAttribute(a.name);
}));
el.replaceChildren(...parsed.body.childNodes);''')
    return head + '''
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/pdfjs-dist@3.11.174/web/pdf_viewer.css">
<script src="https://cdn.jsdelivr.net/npm/pdfjs-dist@3.11.174/build/pdf.min.js"></script>
<style>
body{padding:0;margin:0;overflow:hidden}#pages{width:60%;height:100vh;overflow:auto;background:#888}#panel{position:fixed;right:0;top:0;width:40%;height:100vh;overflow:auto;background:var(--bg)}
.page{position:relative;margin:12px auto;background:white}.page canvas{display:block}.textLayer{position:absolute;inset:0}.textLayer ::selection{background:#5599ff80}
#tools{position:fixed;z-index:20;background:var(--bg);border:1px solid #999;padding:8px;border-radius:8px;max-width:95vw}button{cursor:pointer}#question{width:180px}.card{margin:12px;padding:16px;border:1px solid var(--line);border-radius:8px;overflow-wrap:anywhere;cursor:pointer}.card blockquote{margin:8px 0;white-space:pre-wrap}.spinner{display:inline-block;width:14px;height:14px;border:2px solid #aaa;border-top-color:#0969da;border-radius:50%;animation:spin 1s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}pre{overflow:auto}
</style></head><body>
<div id="pages">PDF 불러오는 중…</div><aside id="panel"><p>문장·수식을 선택하면 질문 버튼이 나타납니다.</p></aside>
<div id="tools" hidden><button data-mode="explain">해설</button><button data-mode="research">관련 연구</button><button data-mode="learn">더 알아보기</button><button data-mode="deep">깊게</button><br><input id="question" placeholder="자유 질문" aria-label="자유 질문"><button id="send">질문</button></div>
<script>
async function renderAnswer(el, markdown) {
''' + renderer + '''
}
const pages = document.getElementById('pages'), panel = document.getElementById('panel'), tools = document.getElementById('tools');
let selected = null;
mermaid.initialize({startOnLoad:false,securityLevel:'strict'});
pdfjsLib.GlobalWorkerOptions.workerSrc='https://cdn.jsdelivr.net/npm/pdfjs-dist@3.11.174/build/pdf.worker.min.js';
(async () => {
 try {
 const pdf=await pdfjsLib.getDocument('/pdf').promise; pages.textContent='';
 for(let n=1;n<=pdf.numPages;n++) {
 const page=await pdf.getPage(n), base=page.getViewport({scale:1});
 const viewport=page.getViewport({scale:Math.max(.2,(pages.clientWidth-32)/base.width)});
 const holder=document.createElement('div');holder.className='page';holder.id='page-'+n;holder.dataset.page=n;
 holder.style.width=viewport.width+'px';holder.style.height=viewport.height+'px';holder.style.setProperty('--scale-factor',viewport.scale);
 const canvas=document.createElement('canvas');canvas.width=Math.ceil(viewport.width);canvas.height=Math.ceil(viewport.height);holder.append(canvas);
 const layer=document.createElement('div');layer.className='textLayer';holder.append(layer);pages.append(holder);
 await page.render({canvasContext:canvas.getContext('2d'),viewport}).promise;
 await pdfjsLib.renderTextLayer({textContentSource:await page.getTextContent(),container:layer,viewport,textDivs:[]}).promise;
 }
 } catch(e){pages.textContent='PDF 오류: '+e.message;}
})();
document.addEventListener('mouseup', e => {
 if(tools.contains(e.target)) return;
 const s=window.getSelection();
 const node=s && s.anchorNode, page=node && (node.nodeType===1?node:node.parentElement).closest('.page');
 if(!s || !s.toString().trim() || !page){tools.hidden=true;return;}
 selected={selection:s.toString(),page:Number(page.dataset.page)};
 const rect=s.getRangeAt(0).getBoundingClientRect(); tools.hidden=false;
 tools.style.left=Math.max(0,Math.min(rect.left,innerWidth-tools.offsetWidth))+'px';
 tools.style.top=Math.max(0,Math.min(rect.bottom+6,innerHeight-tools.offsetHeight))+'px';
});
async function submit(mode){
 if(!selected)return;
 const payload={...selected,mode,question:document.getElementById('question').value};tools.hidden=true;
 const card=document.createElement('section');card.className='card';
 const title=document.createElement('strong');title.textContent='p.'+payload.page+' · '+mode+' · '+payload.question;
 const quote=document.createElement('blockquote');quote.textContent=payload.selection;
 const status=document.createElement('p');status.innerHTML='<span class="spinner"></span> 응답 대기 중';
 const body=document.createElement('article');card.append(title,quote,status,body);panel.append(card);card.scrollIntoView({block:'nearest'});
 card.addEventListener('click', e=>{if(!e.target.closest('a'))document.getElementById('page-'+payload.page)?.scrollIntoView({behavior:'smooth'});});
 try{
 const r=await fetch('/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
 const data=await r.json();status.textContent='러너: '+(data.runner || '실패')+(data.failures.length?' · '+data.failures.join(' / '):'');
 if(!r.ok)throw new Error(data.error || '요청 실패');
 await renderAnswer(body,data.markdown);
 }catch(e){status.textContent+=' · '+e.message;}
}
tools.querySelectorAll('[data-mode]').forEach(b=>b.onclick=()=>submit(b.dataset.mode));
document.getElementById('send').onclick=()=>submit('explain');
document.getElementById('question').onkeydown=e=>{if(e.key==='Enter')submit('explain');};
</script></body></html>'''


def create_server(root: Path, conf: dict, pdf: Path, port: int = 0) -> ThreadingHTTPServer:
    pdf = pdf.resolve(strict=True)
    pages = ask.pdf_pages(pdf)
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, status, body, content_type):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == '/':
                self.respond(200, viewer_html().encode(), 'text/html; charset=utf-8')
            elif self.path == '/pdf':
                self.respond(200, pdf.read_bytes(), 'application/pdf')
            else:
                self.respond(404, b'Not found', 'text/plain')

        def do_POST(self):
            if self.path != '/ask':
                self.respond(404, b'Not found', 'text/plain')
                return
            try:
                # Only this viewer may initiate runner requests (prevent cross-site POSTs).
                origin = self.headers.get('Origin')
                expected = f'http://127.0.0.1:{self.server.server_port}'
                if origin and origin != expected:
                    raise ValueError('Origin rejected')
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 100000:
                    raise ValueError('Invalid request size')
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError('JSON object required')
                selection, question = data.get('selection', ''), data.get('question', '')
                page, mode = data.get('page'), data.get('mode', 'explain')
                if not isinstance(selection, str) or not isinstance(question, str):
                    raise ValueError('selection/question must be strings')
                if type(page) is not int or page < 1 or (pages and page > len(pages)):
                    raise ValueError('Invalid page')
                if not isinstance(mode, str) or mode not in ask.MODE_PROMPTS:
                    raise ValueError('Invalid mode')
                with lock:
                    result = ask.ask(root, conf, selection, question, str(pdf), mode=mode, page=page, pages=pages)
                result = {key: result[key] for key in ('markdown', 'runner', 'failures', 'links')}
                status = 200
            except (ValueError, TypeError) as exc:
                status, result = 400, dict(error=str(exc), markdown='', runner='', failures=[str(exc)], links=[])
            except Exception as exc:
                status, result = 502, dict(error=str(exc), markdown='', runner='', failures=[str(exc)], links=[])
            self.respond(status, json.dumps(result, ensure_ascii=False).encode(), 'application/json; charset=utf-8')

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def serve(root: Path, conf: dict, source: str, port: int = 0, open_browser: bool = True):
    pdf = ask.resolve_source(source)
    if pdf is None:
        raise ValueError('PDF 파일을 찾을 수 없습니다')
    with create_server(root, conf, pdf, port) as server:
        url = f'http://127.0.0.1:{server.server_port}/'
        print(f'relay read: {url} (Ctrl+C 종료)', flush=True)
        if open_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
