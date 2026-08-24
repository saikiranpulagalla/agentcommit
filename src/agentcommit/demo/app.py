from __future__ import annotations

from pathlib import Path
import tempfile

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from agentcommit.demo.engine import DEFAULT_REQUEST, DemoEngine


class RunRequest(BaseModel):
    scenario: str = Field(pattern=r"^(happy|stale_replan|crash_recovery|late_capture)$")
    request: str = Field(min_length=1, max_length=2000)


def create_app(*, state_dir: str | Path | None = None) -> FastAPI:
    root = Path(state_dir) if state_dir is not None else Path(tempfile.gettempdir()) / "agentcommit-buildathon-demo"
    engine = DemoEngine(root)
    app = FastAPI(title="AgentCommit Buildathon Demo", version="0.6.0-demo")

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'unsafe-inline' 'self'; style-src 'unsafe-inline' 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "mode": "OFFLINE_DEMO", "real_llm": False, "real_razorpay": False}

    @app.get("/api/config")
    def config() -> dict:
        return {
            "default_request": DEFAULT_REQUEST,
            "scenarios": [
                {"id": "happy", "name": "Happy Path", "description": "Valid plan → commit → captured payment."},
                {"id": "stale_replan", "name": "Stale Product → Replan", "description": "Merchant facts change after planning; old commit is denied and replanned."},
                {"id": "crash_recovery", "name": "Crash / Unknown Order Recovery", "description": "Durable outbox + deterministic receipt recover an ambiguous remote write without duplicate POST."},
                {"id": "late_capture", "name": "Late Capture → Compensation", "description": "Failure/expiry releases inventory; later capture becomes compensation-required."},
            ],
            "evidence_mode": "OFFLINE_REFERENCE+FAKE_RAZORPAY",
        }

    @app.post("/api/run")
    def run(body: RunRequest) -> dict:
        try:
            return engine.run(body.scenario, body.request).to_dict()
        except Exception as exc:
            raise HTTPException(status_code=409, detail=f"{type(exc).__name__}: {exc}") from exc

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _HTML

    return app


app = create_app()


_HTML = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>AgentCommit — Safe Commit for Agentic Commerce</title>
<style>
:root{--bg:#081019;--panel:#0e1823;--line:#223244;--text:#e9f0f7;--muted:#92a4b7;--good:#4ade80;--warn:#facc15;--bad:#fb7185;--ai:#60a5fa;--pay:#c084fc;--accent:#2dd4bf}
*{box-sizing:border-box}body{margin:0;font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:radial-gradient(circle at top,#122337 0,#081019 44%);color:var(--text)}
main{max-width:1180px;margin:auto;padding:28px 20px 60px}.top{display:flex;justify-content:space-between;gap:20px;align-items:flex-start}.eyebrow{color:var(--accent);font-weight:700;letter-spacing:.12em;font-size:12px}h1{font-size:44px;line-height:1.04;margin:10px 0 8px}.thesis{font-size:20px;color:#bfd0df;margin:0}.badge{border:1px solid #7c5a12;background:#30230a;color:#fde68a;padding:8px 12px;border-radius:999px;font-size:12px;font-weight:800;white-space:nowrap}
.grid{display:grid;grid-template-columns:360px 1fr;gap:18px;margin-top:28px}.panel{background:rgba(14,24,35,.94);border:1px solid var(--line);border-radius:16px;padding:18px;box-shadow:0 20px 50px rgba(0,0,0,.18)}label{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}textarea{width:100%;min-height:150px;margin-top:8px;background:#09131d;border:1px solid #2b4056;border-radius:12px;color:var(--text);padding:12px;resize:vertical;line-height:1.5}select,button{width:100%;border-radius:10px;padding:11px 12px;font:inherit}select{margin-top:8px;background:#09131d;border:1px solid #2b4056;color:var(--text)}button{margin-top:14px;border:0;background:linear-gradient(135deg,#14b8a6,#2563eb);color:white;font-weight:800;cursor:pointer}button:disabled{opacity:.55;cursor:wait}.note{color:var(--muted);font-size:12px;line-height:1.5;margin-top:14px}.summary{padding:14px;border:1px solid var(--line);border-radius:12px;margin-bottom:14px}.summary strong{display:block;font-size:18px}.summary small{color:var(--muted)}
.timeline{display:flex;flex-direction:column;gap:10px}.event{display:grid;grid-template-columns:34px 90px 1fr;gap:10px;align-items:start;border-left:2px solid var(--line);padding:4px 0 8px 14px}.step{width:26px;height:26px;border-radius:50%;background:#162638;display:grid;place-items:center;font-size:12px;color:#b9cadd}.kind{font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.06em;padding-top:5px}.event h3{margin:0 0 3px;font-size:15px}.event p{margin:0;color:var(--muted);font-size:13px;line-height:1.45}.event pre{margin:8px 0 0;white-space:pre-wrap;color:#a9bfd2;font-size:11px;background:#09131d;border-radius:8px;padding:8px;overflow:auto}.kind-allow .kind{color:var(--good)}.kind-deny .kind{color:var(--bad)}.kind-warn .kind,.kind-inject .kind{color:var(--warn)}.kind-ai .kind{color:var(--ai)}.kind-payment .kind{color:var(--pay)}.kind-compensate .kind{color:#fb923c}.empty{color:var(--muted);padding:30px;text-align:center;border:1px dashed #2b4056;border-radius:12px}
.final{margin-top:14px;background:#09131d;border:1px solid #1e3347;padding:12px;border-radius:12px}.footer{margin-top:24px;color:var(--muted);font-size:12px;text-align:center}@media(max-width:800px){.grid{grid-template-columns:1fr}.top{flex-direction:column}h1{font-size:34px}}
</style></head>
<body><main>
<div class="top"><div><div class="eyebrow">RAZORPAY AI GROWTH & AGENTIC COMMERCE</div><h1>AgentCommit</h1><p class="thesis">The plan may be stale. The commit must not be.</p></div><div class="badge">OFFLINE DEMO — NOT REAL MONEY</div></div>
<div class="grid"><section class="panel"><label>Buyer request</label><textarea id="request"></textarea><label style="display:block;margin-top:14px">Failure scenario</label><select id="scenario"></select><button id="run">Run transaction</button><div class="note">This page intentionally uses the deterministic reference compiler and a fake Razorpay-shaped gateway. The financial/state checks are the real AgentCommit kernel. Real LLM and Razorpay Test Mode evidence remain separate certification gates.</div></section>
<section class="panel"><div id="result"><div class="empty">Run a scenario to see the state-aware commit timeline.</div></div></section></div>
<div class="footer">AgentCommit Buildathon demo • offline evidence is labeled explicitly</div>
</main><script>
const req=document.getElementById('request'), sel=document.getElementById('scenario'), btn=document.getElementById('run'), out=document.getElementById('result');
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]));
fetch('/api/config').then(r=>r.json()).then(c=>{req.value=c.default_request;c.scenarios.forEach(s=>{const o=document.createElement('option');o.value=s.id;o.textContent=s.name+' — '+s.description;sel.appendChild(o)})});
btn.onclick=async()=>{btn.disabled=true;btn.textContent='Running…';out.innerHTML='<div class="empty">Executing deterministic demo flow…</div>';try{const r=await fetch('/api/run',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({scenario:sel.value,request:req.value})});const j=await r.json();if(!r.ok)throw new Error(j.detail||'run failed');let html=`<div class="summary"><strong>${esc(j.status)}</strong><small>${esc(j.summary)} · ${esc(j.mode)}</small></div><div class="timeline">`;for(const e of j.events){html+=`<div class="event kind-${esc(e.kind)}"><div class="step">${e.step}</div><div class="kind">${esc(e.kind)}</div><div><h3>${esc(e.title)}</h3><p>${esc(e.detail)}</p>${Object.keys(e.data||{}).length?`<pre>${esc(JSON.stringify(e.data,null,2))}</pre>`:''}</div></div>`}html+=`</div><div class="final"><b>Final state</b><pre>${esc(JSON.stringify(j.final,null,2))}</pre></div>`;out.innerHTML=html}catch(e){out.innerHTML=`<div class="summary"><strong>DEMO ERROR</strong><small>${esc(e.message)}</small></div>`}finally{btn.disabled=false;btn.textContent='Run transaction'}};
</script></body></html>'''
