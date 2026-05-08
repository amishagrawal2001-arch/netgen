# OSTG Server Startup Messages

This document explains common log messages when `ostg-server` starts on a remote (or local) host.

---

## Normal / Informational

| Message | Meaning |
|--------|--------|
| `[STREAM POLL] Stream statistics polling thread started` | Stream stats background thread started. |
| `[AI] Network troubleshooting AI initialized` | AI troubleshooting module loaded. |
| `[AI PYTEST] Pytest generator and runner initialized` | Pytest-related AI features loaded. |
| `Serving Flask app 'run_tgen_server'` | Flask app is running. |
| `Running on http://127.0.0.1:5050` (or `5051`) | Server is listening. Port comes from `PORT` env or `--port` (default **5051**). |
| `Running on http://10.x.x.x:5050` | Server is bound to all interfaces at that IP. |

---

## Warnings (Non-Fatal)

### 1. API key not found

```
WARNING: [AI SETTINGS] API key not found. Cloud AI features will be disabled...
```

- **Meaning:** No OpenAI (or compatible) API key is set. Cloud-based AI (e.g. OpenAI) will not be used.
- **Impact:** Server runs normally. Traffic generation, BGP/OSPF/ISIS, streams, and the REST API all work. Only cloud AI features are disabled.
- **Optional – enable cloud AI:**
  - **Environment variable** (on the server):
    ```bash
    export OPENAI_API_KEY='sk-...'
    ```
  - **Or** set it via the client (Settings → AI) or via API:
    ```bash
    curl -X POST http://<server>:5051/api/ai/settings \
      -H "Content-Type: application/json" \
      -d '{"openai_api_key": "sk-..."}'
    ```
  - **Local AI:** If Ollama is installed and models are present, local LLM features can still work without an API key.

---

### 2. AI Test framework not available

```
WARNING: [AI TEST] Test framework not available
```

- **Meaning:** An optional test framework or dependency for AI-generated tests is missing.
- **Impact:** Server and API work. Only that specific AI test feature may be limited.
- **Optional:** Install pytest and any extra deps the AI test code expects if you need that feature.

---

### 3. Development server

```
WARNING: This is a development server. Do not use it in a production deployment.
Use a production WSGI server instead.
```

- **Meaning:** The app is running with Flask’s built-in server.
- **Impact:** Fine for lab/dev. For production, use a WSGI server (e.g. **gunicorn**):
  ```bash
  pip install gunicorn
  gunicorn -w 4 -b 0.0.0.0:5051 "run_tgen_server:app"
  ```

---

## Port: 5050 vs 5051

- Default in code is **5051** (`PORT` env or `--port`).
- If you see **5050**, something (e.g. systemd unit or startup script) is setting `PORT=5050` or passing `--port 5050`.
- **Client:** Point the OSTG client to the port the server actually prints in “Running on …” (e.g. `http://<server>:5050` or `:5051`).

---

## Quick checklist

| Item | Check |
|------|--------|
| Server reachable | `curl http://<server>:5050/api/ping` or `:5051` → `{"status":"ok"}` |
| API key (optional) | Set `OPENAI_API_KEY` or use `/api/ai/settings` if you need cloud AI. |
| Production | Use gunicorn (or another WSGI server) and reverse proxy if needed. |

---

## Server won't start (systemd)

If `systemctl status ostg-server.service` shows **active (auto-restart)** or **failed**:

1. **See why it exited**
   ```bash
   journalctl -u ostg-server -n 50 --no-pager
   ```
   Look for the last Python traceback (e.g. `ModuleNotFoundError`, `ImportError`, permission errors).

2. **Common causes**
   - **Import / module error** – A dependency is missing on the server. Install with:
     `pip3 install -r /opt/OSTG/requirements.txt` (or re-run the OSTG installer).
   - **Database directory** – Server expects `/opt/OSTG` to exist and be writable:
     `sudo mkdir -p /opt/OSTG && sudo chmod 755 /opt/OSTG`
   - **Wrong ExecStart** – The unit should run the `ostg-server` entry point (or `python3 -m run_tgen_server`). If you edited the unit, restore:
     `ExecStart=/usr/local/bin/ostg-server` (or `ExecStart=/usr/bin/python3 -m run_tgen_server`).

3. **Test run by hand (on the server)**
   ```bash
   cd /opt/OSTG
   /usr/local/bin/ostg-server
   ```
   Any traceback here is the same reason systemd sees; fix that first, then `systemctl start ostg-server`.

---

## Docker FRR image build fails (Alpine CDN unreachable)

If you see **"temporary error (try again later)"** or **"unable to select packages"** when building the FRR image, the server cannot reach the default Alpine CDN (`dl-cdn.alpinelinux.org`).

**Option A – From your Mac (recommended)**  
Copy the updated Dockerfile (with mirror support) to the server and build using an alternate mirror:

```bash
cd /path/to/OSTG
./deploy_frr_dockerfile.sh root@san-ft-ai-srv01
```

**Option B – From your Mac (manual)**  
```bash
scp Dockerfile.frr root@san-ft-ai-srv01:/opt/OSTG/
ssh root@san-ft-ai-srv01 'docker build --build-arg ALPINE_MIRROR=https://ftp.halifax.rwth-aachen.de/alpine -t ostg-frr:latest -f /opt/OSTG/Dockerfile.frr /opt/OSTG'
```

**Option C – Only on the server**  
You must have the **updated** `Dockerfile.frr` on the server (it uses `ARG ALPINE_MIRROR` and retries). After a fresh install from the repo that has the new Dockerfile, run:

```bash
docker build --build-arg ALPINE_MIRROR=https://ftp.halifax.rwth-aachen.de/alpine -t ostg-frr:latest -f /opt/OSTG/Dockerfile.frr /opt/OSTG
```

Other mirrors you can try if one is slow or blocked:  
`https://mirror.accum.se/mirror/alpinelinux.org`, `https://mirrors.aliyun.com/alpine`, `https://mirror.yandex.ru/mirrors/alpine`.
