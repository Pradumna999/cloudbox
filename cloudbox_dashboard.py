"""CloudBox starter dashboard: controls one pre-created VirtualBox VM.

Security design:
- Binds to localhost only; publish it with Tailscale Serve, never port-forward it.
- HTTP Basic authentication requires a password provided through an environment variable.
- Only a fixed VM name is controllable; no user-supplied shell commands are executed.
- Actions use POST plus a custom header to reduce browser-based cross-site request forgery.
- Only start and graceful ACPI shutdown are exposed; there is no hard-power-off endpoint.

This is a small trusted-friend prototype, not a production multi-tenant cloud control plane.
"""

from __future__ import annotations

import os
import re
import html
import secrets
import subprocess
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

APP_USER = os.environ.get("CLOUDBOX_USER", "friend1")
APP_PASSWORD = os.environ.get("CLOUDBOX_PASSWORD", "")
VM_NAME = os.environ.get("CLOUDBOX_VM_NAME", "Friend1-VM")
TERMINAL_URL = os.environ.get("CLOUDBOX_TERMINAL_URL", "").strip()
VBOXMANAGE = Path(
    os.environ.get(
        "VBOXMANAGE",
        r"C:\Program Files\Oracle\VirtualBox\VBoxManage.exe",
    )
)

if not APP_PASSWORD:
    raise RuntimeError(
        "Set CLOUDBOX_PASSWORD in the PowerShell session before starting CloudBox."
    )
if not VBOXMANAGE.is_file():
    raise RuntimeError(
        f"VBoxManage was not found at {VBOXMANAGE}. Set VBOXMANAGE to its actual path."
    )

app = FastAPI(title="CloudBox", docs_url=None, redoc_url=None, openapi_url=None)
security = HTTPBasic()


def require_auth(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    user_ok = secrets.compare_digest(credentials.username, APP_USER)
    pass_ok = secrets.compare_digest(credentials.password, APP_PASSWORD)
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=401,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def vbox(args: list[str]) -> str:
    try:
        result = subprocess.run(
            [str(VBOXMANAGE), *args],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail="VirtualBox command timed out") from exc
    except OSError as exc:
        raise HTTPException(status_code=502, detail="Could not run VirtualBox") from exc
    if result.returncode != 0:
        # Avoid returning host paths or command output to the browser.
        raise HTTPException(
            status_code=502,
            detail="VirtualBox operation failed. Check the dashboard console on the host.",
        )
    return result.stdout


def get_vm_state() -> str:
    output = vbox(["showvminfo", VM_NAME, "--machinereadable"])
    match = re.search(r'^VMState="([^"]+)"\s*$', output, flags=re.MULTILINE)
    if not match:
        raise HTTPException(
            status_code=502,
            detail="Could not read the VM state. Confirm the configured VM name.",
        )
    return match.group(1).lower()


def check_action_header(request: Request) -> None:
    # Browsers must preflight a cross-origin fetch with this custom header; this app
    # does not enable CORS. This is defense in depth, not a replacement for auth.
    if request.headers.get("x-cloudbox-action") != "local-dashboard":
        raise HTTPException(status_code=403, detail="Invalid action request")


@app.get("/", response_class=HTMLResponse)
def home(_: str = Depends(require_auth)) -> str:
    terminal_link = (
        f'<a id="terminal" href="{html.escape(TERMINAL_URL, quote=True)}" target="_blank" rel="noopener noreferrer">Open browser terminal</a>'
        if TERMINAL_URL.startswith("https://")
        else '<span class="muted">Browser terminal URL not configured yet.</span>'
    )
    page = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CloudBox</title>
  <style>
    :root { color-scheme: light dark; font-family: system-ui, sans-serif; }
    body { max-width: 760px; margin: 4rem auto; padding: 0 1.2rem; }
    .panel { border: 1px solid #8886; border-radius: 18px; padding: 1.5rem; }
    .muted { opacity: .75; }
    button { border: 0; border-radius: 10px; padding: .8rem 1.1rem; margin: .35rem .3rem .35rem 0; cursor: pointer; font: inherit; }
    button:disabled { opacity: .45; cursor: not-allowed; }
    #start { background: #167d43; color: white; }
    #stop { background: #9b2f2f; color: white; }
    #message { min-height: 1.5em; }
    code { overflow-wrap: anywhere; }
  </style>
</head>
<body>
  <h1>☁️ CloudBox</h1>
  <p class="muted">Private cloud lab · one Ubuntu VM</p>
  <section class="panel">
    <h2>Friend VM</h2>
    <p>Status: <strong id="state">Loading…</strong></p>
    <button id="start" onclick="act('start')">Start VM</button>
    <button id="stop" onclick="act('stop')">Request graceful shutdown</button>
    <p id="message" role="status" aria-live="polite"></p>
    <p class="muted">Shutdown asks Ubuntu to power off cleanly. It can take a little while. No forced power-off button is provided.</p>
    <p><!-- TERMINAL_LINK --></p>
  </section>
  <script>
    async function refresh() {
      const r = await fetch('/api/status', { credentials: 'same-origin' });
      if (!r.ok) { document.getElementById('state').textContent = 'Unavailable'; return; }
      const d = await r.json();
      document.getElementById('state').textContent = d.state;
      document.getElementById('start').disabled = !d.can_start;
      document.getElementById('stop').disabled = !d.can_stop;
    }
    async function act(action) {
      const message = document.getElementById('message');
      message.textContent = 'Sending request…';
      try {
        const r = await fetch('/api/' + action, {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'X-CloudBox-Action': 'local-dashboard' }
        });
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail || 'Request failed');
        message.textContent = d.message;
        await refresh();
      } catch (e) { message.textContent = e.message || 'Request failed'; }
    }
    refresh();
    setInterval(refresh, 8000);
  </script>
</body>
</html>"""
    return page.replace("<!-- TERMINAL_LINK -->", terminal_link)


@app.get("/api/status")
def status(_: str = Depends(require_auth)) -> dict[str, Any]:
    state = get_vm_state()
    return {
        "vm": VM_NAME,
        "state": state,
        "can_start": state in {"poweroff", "aborted"},
        "can_stop": state == "running",
    }


@app.post("/api/start")
def start(request: Request, _: str = Depends(require_auth)) -> dict[str, str]:
    check_action_header(request)
    state = get_vm_state()
    if state == "running":
        return {"message": "VM is already running."}
    if state not in {"poweroff", "aborted"}:
        raise HTTPException(status_code=409, detail=f"VM state is {state}; start is unavailable.")
    vbox(["startvm", VM_NAME, "--type", "headless"])
    return {"message": "Start command sent. The VM may need a minute to boot."}


@app.post("/api/stop")
def stop(request: Request, _: str = Depends(require_auth)) -> dict[str, str]:
    check_action_header(request)
    state = get_vm_state()
    if state != "running":
        return {"message": f"VM is not running (state: {state})."}
    vbox(["controlvm", VM_NAME, "acpipowerbutton"])
    return {"message": "Graceful shutdown requested. Wait for Ubuntu to finish shutting down."}
