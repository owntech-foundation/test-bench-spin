import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import common
from status_store import status


PAGE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SPIN test bench</title>
  <style>
    :root { color-scheme: dark; --bg:#101417; --panel:#171c20; --line:#343c42; --text:#e8ecee; --muted:#98a3a9; --cyan:#55c4ca; --green:#63c98c; --red:#e66f6f; }
    * { box-sizing:border-box; }
    body { margin:0; background:var(--bg); color:var(--text); font:14px/1.5 ui-sans-serif,system-ui,-apple-system,sans-serif; }
    main { width:min(1180px,calc(100% - 32px)); margin:0 auto; padding:28px 0 48px; }
    header { display:flex; align-items:flex-end; justify-content:space-between; gap:24px; margin-bottom:20px; padding-bottom:18px; border-bottom:1px solid var(--line); }
    .eyebrow { color:var(--muted); font-size:11px; font-weight:700; letter-spacing:.12em; text-transform:uppercase; }
    h1 { margin:3px 0; font-size:clamp(28px,4vw,40px); letter-spacing:-.015em; line-height:1.1; }
    h2 { margin:0 0 16px; font-size:18px; }
    p { margin:0; color:var(--muted); }
    .live { display:flex; align-items:center; gap:8px; color:var(--muted); white-space:nowrap; }
    .dot { width:9px; height:9px; border-radius:1px; background:var(--red); }
    .dot.ok { background:var(--green); }
    .grid { display:grid; grid-template-columns:minmax(0,1.7fr) minmax(280px,.8fr); gap:18px; }
    .panel { background:var(--panel); border:1px solid var(--line); border-radius:2px; padding:18px; }
    .summary { display:grid; grid-template-columns:repeat(3,1fr); gap:12px; margin-bottom:18px; }
    .metric { min-height:100px; }
    .label { color:var(--muted); font-size:12px; font-weight:700; letter-spacing:.08em; text-transform:uppercase; }
    .value { margin-top:7px; font-size:20px; font-weight:700; overflow-wrap:anywhere; }
    .sub { margin-top:3px; color:var(--muted); font-size:13px; }
    .badge { display:inline-flex; padding:3px 7px; border:1px solid var(--line); border-radius:2px; background:#20272c; color:var(--muted); font-size:11px; font-weight:700; letter-spacing:.04em; text-transform:uppercase; }
    .badge.running,.badge.active { background:#17383c; color:var(--cyan); }
    .badge.passed,.badge.complete { background:#18372a; color:var(--green); }
    .badge.failed { background:#432326; color:var(--red); }
    .steps { display:grid; gap:8px; }
    .step { display:grid; grid-template-columns:32px 1fr auto; gap:12px; align-items:center; padding:11px 12px; border:1px solid var(--line); border-radius:1px; }
    .step.active { border-left:4px solid var(--cyan); background:#1a282c; }
    .step.failed { border-left:4px solid var(--red); background:#2b2022; }
    .step.complete { opacity:.68; }
    .step-number { width:28px; height:28px; display:grid; place-items:center; border:1px solid var(--line); border-radius:1px; background:#20272c; font-weight:700; }
    .step-title { font-weight:750; }
    .step-description { color:var(--muted); font-size:13px; }
    .progress { height:6px; margin-top:16px; overflow:hidden; background:#293137; }
    .progress > div { height:100%; width:0; background:var(--cyan); transition:width .25s ease; }
    ol { margin:0; padding-left:22px; }
    li { padding:0 0 12px 6px; color:var(--muted); }
    li::marker { color:var(--cyan); font-weight:800; }
    .events { display:grid; gap:8px; max-height:330px; overflow:auto; }
    .event { display:grid; grid-template-columns:72px 68px 1fr; gap:8px; font:12px/1.4 ui-monospace,SFMono-Regular,monospace; }
    .event time,.direction { color:var(--muted); }
    .measurements { display:flex; flex-wrap:wrap; gap:8px; margin-top:14px; }
    .measure { padding:5px 8px; border:1px solid var(--line); border-radius:1px; color:var(--cyan); font:13px ui-monospace,monospace; }
    .error { display:none; margin-bottom:16px; padding:10px 14px; border:1px solid var(--red); background:#2b2022; color:var(--red); }
    .diagnostics { display:grid; gap:8px; }
    .relay-row { display:flex; align-items:center; justify-content:space-between; gap:8px; border-top:1px solid var(--line); padding-top:8px; }
    .relay-actions { display:flex; gap:6px; }
    button { border:1px solid var(--line); border-radius:1px; background:#20272c; color:var(--text); padding:6px 9px; cursor:pointer; }
    button:hover { border-color:var(--cyan); color:var(--cyan); }
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
    <aside>
      <section class="panel"><h2>Relay diagnostics</h2><div class="diagnostics">
        <div class="relay-row"><span>PB9 · USB VBUS/GND</span><span class="relay-actions"><button data-relay="PB9" data-enabled="true">ON</button><button data-relay="PB9" data-enabled="false">OFF</button></span></div>
        <div class="relay-row"><span>PC13 · USB D+/D−</span><span class="relay-actions"><button data-relay="PC13" data-enabled="true">ON</button><button data-relay="PC13" data-enabled="false">OFF</button></span></div>
        <div class="relay-row"><span>PB14 · DUT power</span><span class="relay-actions"><button data-relay="PB14" data-enabled="true">ON</button><button data-relay="PB14" data-enabled="false">OFF</button></span></div>
        <button id="read-usb-voltage" type="button">Read USB 3.3 V</button>
        <div id="diagnostic-result" class="sub" aria-live="polite">No diagnostic command sent.</div>
      </div></section>
      <section class="panel" style="margin-top:18px"><h2>Operator checklist</h2><ol id="operator-steps"></ol><button id="reset-controller" type="button">Stop handshake and reset SPIN</button><p id="reset-result" class="sub" aria-live="polite"></p></section>
    </aside>
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
  const relays = data.diagnostics?.relays || {};
  document.querySelectorAll('[data-relay]').forEach(button => {
    const relay = button.dataset.relay;
    button.disabled = Boolean(relays[relay]) === (button.dataset.enabled !== 'true');
  });
}

document.querySelectorAll('[data-relay]').forEach(button => button.addEventListener('click', async () => {
  const result = $('diagnostic-result');
  result.textContent = `Setting ${button.dataset.relay}…`;
  try {
    const response = await fetch('/api/diagnostics/relay', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({relay:button.dataset.relay, enabled:button.dataset.enabled === 'true'})});
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || response.status);
    result.textContent = 'Relay command sent.';
  } catch (error) { result.textContent = `Relay command failed: ${error.message}`; }
}));

$('read-usb-voltage').addEventListener('click', async () => {
  const result = $('diagnostic-result');
  result.textContent = 'Reading USB voltage…';
  try {
    const response = await fetch('/api/diagnostics/voltage', {method:'POST'});
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || response.status);
    result.textContent = 'Voltage request sent; watch the measurement below.';
  } catch (error) { result.textContent = `Voltage request failed: ${error.message}`; }
});

$('reset-controller').addEventListener('click', async () => {
  const result = $('reset-result');
  result.textContent = 'Requesting reset…';
  try {
    const response = await fetch('/api/controller/reset', {method:'POST'});
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || response.status);
    result.textContent = 'Reset requested.';
  } catch (error) {
    result.textContent = `Reset failed: ${error.message}`;
  }
});

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

    def do_POST(self):
        if self.path in ("/api/diagnostics/relay", "/api/diagnostics/voltage"):
            if not status.snapshot()["controller"]["connected"]:
                self._send(b'{"error":"controller is not connected"}', "application/json", 409)
                return
            if self.path.endswith("/voltage"):
                command = "R_USB_V"
            else:
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    payload = json.loads(self.rfile.read(length) or b"{}")
                    relay = str(payload.get("relay", "")).upper()
                    enabled = payload.get("enabled")
                    command = {"PB9": "R_RLY_PB9_", "PC13": "R_RLY_PC13_", "PB14": "R_RLY_PB14_"}[relay] + ("ON" if enabled is True else "OFF" if enabled is False else "")
                    if command.endswith("_"):
                        raise ValueError("enabled must be boolean")
                except (ValueError, KeyError, json.JSONDecodeError):
                    self._send(b'{"error":"invalid relay request"}', "application/json", 400)
                    return
            accepted = common.request_controller_command(command)
            payload = json.dumps({"status": "accepted" if accepted else "busy", "command": command}).encode("utf-8")
            self._send(payload, "application/json", 202 if accepted else 429)
            return

        if self.path == "/api/controller/reset":
            if not status.snapshot()["controller"]["connected"]:
                self._send(b'{"error":"controller is not connected"}', "application/json", 409)
                return
            accepted = common.request_controller_reset()
            payload = json.dumps({"status": "accepted" if accepted else "already_pending"}).encode("utf-8")
            self._send(payload, "application/json", 202)
        else:
            self._send(b'{"error":"not found"}', "application/json", 404)

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
