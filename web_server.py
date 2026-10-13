import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from status_store import status


PAGE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SPIN test bench</title>
  <style>
    :root { color-scheme: dark; --bg:#0b1014; --panel:#141c22; --line:#26343e; --text:#eef5f7; --muted:#91a5ae; --cyan:#51d3d9; --green:#64df9b; --red:#ff7373; --amber:#ffc857; }
    * { box-sizing:border-box; }
    body { margin:0; background:radial-gradient(circle at top right,#17313a 0,transparent 34rem),var(--bg); color:var(--text); font:15px/1.5 ui-sans-serif,system-ui,-apple-system,sans-serif; }
    main { width:min(1180px,calc(100% - 32px)); margin:0 auto; padding:34px 0 56px; }
    header { display:flex; align-items:flex-start; justify-content:space-between; gap:24px; margin-bottom:24px; }
    .eyebrow { color:var(--cyan); font-size:12px; font-weight:800; letter-spacing:.16em; text-transform:uppercase; }
    h1 { margin:4px 0 4px; font-size:clamp(30px,5vw,54px); letter-spacing:-.04em; line-height:1; }
    h2 { margin:0 0 16px; font-size:18px; }
    p { margin:0; color:var(--muted); }
    .live { display:flex; align-items:center; gap:8px; color:var(--muted); white-space:nowrap; }
    .dot { width:9px; height:9px; border-radius:50%; background:var(--red); box-shadow:0 0 14px currentColor; }
    .dot.ok { background:var(--green); }
    .grid { display:grid; grid-template-columns:minmax(0,1.7fr) minmax(280px,.8fr); gap:18px; }
    .panel { background:color-mix(in srgb,var(--panel) 94%,transparent); border:1px solid var(--line); border-radius:18px; padding:20px; box-shadow:0 18px 50px #0004; }
    .summary { display:grid; grid-template-columns:repeat(3,1fr); gap:12px; margin-bottom:18px; }
    .metric { min-height:106px; }
    .label { color:var(--muted); font-size:12px; font-weight:700; letter-spacing:.08em; text-transform:uppercase; }
    .value { margin-top:8px; font-size:22px; font-weight:800; overflow-wrap:anywhere; }
    .sub { margin-top:3px; color:var(--muted); font-size:13px; }
    .badge { display:inline-flex; padding:4px 9px; border-radius:999px; background:#23313a; color:var(--muted); font-size:12px; font-weight:800; text-transform:uppercase; }
    .badge.running,.badge.active { background:#17383c; color:var(--cyan); }
    .badge.passed,.badge.complete { background:#18372a; color:var(--green); }
    .badge.failed { background:#432326; color:var(--red); }
    .steps { display:grid; gap:8px; }
    .step { display:grid; grid-template-columns:32px 1fr auto; gap:12px; align-items:center; padding:12px; border:1px solid var(--line); border-radius:12px; }
    .step.active { border-color:var(--cyan); background:#173038; }
    .step.failed { border-color:var(--red); background:#361f22; }
    .step.complete { opacity:.68; }
    .step-number { width:30px; height:30px; display:grid; place-items:center; border-radius:50%; background:#223039; font-weight:800; }
    .step-title { font-weight:750; }
    .step-description { color:var(--muted); font-size:13px; }
    .progress { height:7px; margin-top:16px; border-radius:99px; overflow:hidden; background:#26343e; }
    .progress > div { height:100%; width:0; background:linear-gradient(90deg,var(--cyan),var(--green)); transition:width .35s ease; }
    ol { margin:0; padding-left:22px; }
    li { padding:0 0 12px 6px; color:var(--muted); }
    li::marker { color:var(--cyan); font-weight:800; }
    .events { display:grid; gap:8px; max-height:330px; overflow:auto; }
    .event { display:grid; grid-template-columns:72px 68px 1fr; gap:8px; font:12px/1.4 ui-monospace,SFMono-Regular,monospace; }
    .event time,.direction { color:var(--muted); }
    .measurements { display:flex; flex-wrap:wrap; gap:8px; margin-top:14px; }
    .measure { padding:6px 9px; border:1px solid var(--line); border-radius:8px; color:var(--cyan); font:13px ui-monospace,monospace; }
    .error { display:none; margin-bottom:16px; padding:10px 14px; background:#432326; color:var(--red); border-radius:10px; }
    @media (max-width:800px) { .grid,.summary { grid-template-columns:1fr; } header { align-items:flex-end; } .event { grid-template-columns:64px 1fr; } .event .direction { display:none; } }
  </style>
</head>
<body>
<main>
  <header><div><div class="eyebrow">OwnTech production</div><h1>SPIN test bench</h1><p>Live controller status and test workflow</p></div><div class="live"><span id="live-dot" class="dot"></span><span id="updated">Connecting…</span></div></header>
  <div id="error" class="error">Dashboard cannot reach the status API.</div>
  <section class="summary">
    <div class="panel metric"><div class="label">Controller</div><div id="controller" class="value">—</div><div id="controller-port" class="sub">—</div></div>
    <div class="panel metric"><div class="label">Test state</div><div class="value"><span id="test-state" class="badge">—</span></div><div id="current-step" class="sub">—</div></div>
    <div class="panel metric"><div class="label">Last message</div><div id="last-message" class="value">—</div><div id="last-event" class="sub">—</div></div>
  </section>
  <div class="grid">
    <div>
      <section class="panel"><h2>Automated sequence</h2><div id="steps" class="steps"></div><div class="progress"><div id="progress"></div></div><div id="measurements" class="measurements"></div></section>
      <section class="panel" style="margin-top:18px"><h2>Recent protocol activity</h2><div id="events" class="events"></div></section>
    </div>
    <aside class="panel"><h2>Operator checklist</h2><ol id="operator-steps"></ol></aside>
  </div>
</main>
<script>
const $ = id => document.getElementById(id);
const escapeHtml = value => String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const timeOnly = value => value ? new Date(value).toLocaleTimeString() : 'No activity yet';

function render(data) {
  $('error').style.display = 'none';
  $('live-dot').classList.add('ok');
  $('updated').textContent = `Updated ${new Date().toLocaleTimeString()}`;
  $('controller').textContent = data.controller.connected ? 'Connected' : 'Disconnected';
  $('controller-port').textContent = data.controller.port || 'Waiting for SPIN-TESTER';
  $('test-state').textContent = data.test.state;
  $('test-state').className = `badge ${data.test.state}`;
  const active = data.steps.find(step => step.state === 'active' || step.state === 'failed');
  $('current-step').textContent = active ? active.title : 'Waiting for a test';
  $('last-message').textContent = data.last_message || '—';
  $('last-event').textContent = timeOnly(data.last_event_at);
  $('progress').style.width = `${data.test.progress ?? 0}%`;
  $('steps').innerHTML = data.steps.map((step,index) => `<div class="step ${escapeHtml(step.state)}"><div class="step-number">${index+1}</div><div><div class="step-title">${escapeHtml(step.title)}</div><div class="step-description">${escapeHtml(step.description)}</div></div><span class="badge ${escapeHtml(step.state)}">${escapeHtml(step.state)}</span></div>`).join('');
  $('operator-steps').innerHTML = data.operator_steps.map(step => `<li>${escapeHtml(step)}</li>`).join('');
  $('events').innerHTML = data.events.length ? data.events.map(event => `<div class="event"><time>${escapeHtml(timeOnly(event.time))}</time><span class="direction">${escapeHtml(event.direction)}</span><span>${escapeHtml(event.message)}</span></div>`).join('') : '<p>No protocol activity yet.</p>';
  $('measurements').innerHTML = Object.entries(data.measurements).map(([key,value]) => `<span class="measure">${escapeHtml(key)} ${escapeHtml(value)}</span>`).join('');
}

async function refresh() {
  try {
    const response = await fetch('/api/status', {cache:'no-store'});
    if (!response.ok) throw new Error(response.status);
    render(await response.json());
  } catch (error) {
    $('error').style.display = 'block';
    $('live-dot').classList.remove('ok');
    $('updated').textContent = 'Offline';
  }
}
refresh(); setInterval(refresh, 1000);
</script>
</body>
</html>"""


class StatusHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/" or self.path.startswith("/?"):
            self._send(PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif self.path == "/api/status":
            payload = json.dumps(status.snapshot(), separators=(",", ":")).encode("utf-8")
            self._send(payload, "application/json")
        elif self.path == "/healthz":
            self._send(b'{"status":"ok"}', "application/json")
        else:
            self._send(b'{"error":"not found"}', "application/json", 404)

    def do_HEAD(self):
        if self.path == "/" or self.path.startswith("/?"):
            self._send(PAGE.encode("utf-8"), "text/html; charset=utf-8", include_body=False)
        else:
            self._send(b'{"error":"not found"}', "application/json", 404, include_body=False)

    def _send(self, payload, content_type, code=200, include_body=True):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'")
        self.end_headers()
        if include_body:
            self.wfile.write(payload)

    def log_message(self, format, *args):
        return


class StatusServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def start_server(host=None, port=None):
    host = host if host is not None else os.environ.get("OWNTESTER_WEB_HOST", "0.0.0.0")
    port = port if port is not None else int(os.environ.get("OWNTESTER_WEB_PORT", "8080"))
    try:
        server = StatusServer((host, port), StatusHandler)
    except OSError as error:
        print(f"Unable to start status web server on {host}:{port}: {error}")
        return None
    thread = threading.Thread(target=server.serve_forever, name="status-web-server", daemon=True)
    thread.start()
    print(f"Status web server listening on http://{host}:{server.server_port}")
    return server
