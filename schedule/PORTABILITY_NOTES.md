# Portability Notes

## Python dependencies

`pywinauto` is installed only on Windows:

```text
pywinauto==0.6.9; platform_system == "Windows"
```

This keeps `pip install -r backend/requirements.txt` usable on non-Windows machines. The QQ window watcher itself still requires Windows, because it reads the local QQ desktop UI through Windows UI Automation.

## QQ watcher backend address

The QQ watcher sends messages to the schedule backend endpoint:

```text
POST {SCHEDULE_API_BASE}/api/integrations/qq/messages
```

By default, `SCHEDULE_API_BASE` is:

```text
http://127.0.0.1:8000
```

When moving the watcher to another machine, container, VM, or remote backend, set the backend address explicitly instead of relying on the local default:

```powershell
$env:SCHEDULE_API_BASE = "http://192.168.1.10:8000"
.\start_qq_watcher.ps1 -ApiBase $env:SCHEDULE_API_BASE
```

You can also pass the address directly to the Python watcher:

```powershell
cd backend
python -m app.qq_watcher --api-base "http://192.168.1.10:8000"
```

`backend/app/qq_sync.py` does not call the local schedule backend directly. It handles QQ messages after the backend receives them. Its optional LLM parser uses `QQ_SYNC_LLM_API_BASE`, which defaults to `https://api.openai.com/v1`.

## API key header

The backend protects integration writes with the `X-API-Key` header. The QQ watcher sends:

```text
X-API-Key: {SCHEDULE_API_KEY}
```

The watcher and backend must use the same key. For local development the default is `dev-schedule-key`, but on another machine you should set a real shared value:

```powershell
$env:SCHEDULE_API_KEY = "replace-with-your-shared-key"
.\start_qq_watcher.ps1 -ApiKey $env:SCHEDULE_API_KEY
```

If requests are rejected with 401 or 403, first check that `SCHEDULE_API_BASE` points to the correct backend and that the watcher key matches the backend `SCHEDULE_API_KEY`.
