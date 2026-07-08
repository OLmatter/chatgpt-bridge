# Agent Install Skill for ChatGPT Bridge

This file is written for agents. Read it before installing, updating, or debugging `chatgpt-bridge` for a user.

Goal: get a logged-in ChatGPT browser tab connected to a local backend, then optionally enable an auto-reply supervisor powered by Claude CLI, Codex CLI, or an OpenAI-compatible API.

## 1. Mental model

`chatgpt-bridge` has three moving pieces:

1. `userscript/chatgpt_bridge.user.js` runs inside `https://chatgpt.com/` through Tampermonkey.
2. `local/run_all.py` runs a local backend on `http://127.0.0.1:5000` and receives page snapshots.
3. `local/monitor.py` is a Tkinter GUI monitor and prompt/provider editor.

The browser page polls the backend. The backend does not push directly into the browser. If the page badge says `waiting for service`, the userscript is alive but the backend is not reachable.

## 2. Install for a user

### Backend

Windows PowerShell:

```powershell
cd <repo>\chatgpt-bridge
python -m pip install -r requirements.txt
python local\run_all.py
```

macOS/Linux shell:

```bash
cd <repo>/chatgpt-bridge
python3 -m pip install -r requirements.txt
python3 local/run_all.py
```

The expected backend status endpoint is:

```text
http://127.0.0.1:5000/status
```

A healthy response has `pages_connected`, `pages_alive`, and `supervisor_on`.

### GUI monitor

Windows:

```powershell
cd <repo>\chatgpt-bridge
python local\monitor.py
```

macOS/Linux with a desktop session:

```bash
cd <repo>/chatgpt-bridge
python3 local/monitor.py
```

If there is no graphical desktop, skip the GUI and edit `local/supervisor_config.json` directly.

### Userscript

1. Install Tampermonkey in Chrome or another supported browser.
2. Create a new userscript.
3. Paste the full contents of `userscript/chatgpt_bridge.user.js`.
4. Save it.
5. Open or refresh `https://chatgpt.com/`.
6. Confirm the page shows a green `Bridge: ready` badge.

If the backend or userscript was updated, refresh every open ChatGPT tab so the page runs the latest userscript.

## 3. Provider choices

Open the GUI, click `Prompt...`, then choose Provider.

### Claude CLI

Use this when the machine has `claude` in PATH.

Check:

```bash
claude --help
```

Config value:

```json
{"provider": "claude_cli"}
```

### Codex CLI

Use this when the machine has `codex` in PATH.

Check:

```bash
codex exec --help
```

Config value:

```json
{"provider": "codex_cli"}
```

The backend calls Codex with `codex exec --output-last-message`, so it reads the final assistant text instead of terminal logs.

### OpenAI-compatible API

Use this when the user has an API URL, key, and model but no local CLI.

Required fields in the GUI:

- API Base URL
- Model
- API key

The backend appends `/chat/completions` if the URL does not already end with it.

Config value:

```json
{"provider": "openai_compatible"}
```

Never write API keys into README, issues, commits, or memory. The local key belongs only in ignored `local/supervisor_config.json`.

## 4. Required prompt contract

The supervisor prompt must force this output format:

```text
REPLY
<short message>
```

or:

```text
SKIP
<reason>
```

Keep `{convo}` in the prompt when possible. It marks where recent ChatGPT turns are inserted. If `{convo}` is removed, the backend appends the conversation automatically.

Important default rule: if the conversation hit the ChatGPT maximum length or asks to start a new chat, the correct output is `SKIP`.

The backend also has a hard guard for this state through `conversationLimited`, so it should not rely only on the model following instructions.

## 5. Verify after install or update

### Backend status

PowerShell:

```powershell
Invoke-RestMethod http://127.0.0.1:5000/status
Invoke-RestMethod http://127.0.0.1:5000/all_snapshots
```

Bash:

```bash
curl -s http://127.0.0.1:5000/status
curl -s http://127.0.0.1:5000/all_snapshots
```

Expected:

- `pages_connected >= 1` after a ChatGPT tab is open and refreshed.
- `pages_alive >= 1` if the tab is actively polling.
- The snapshot includes `conversationLimited: true` when ChatGPT shows maximum-length/start-new-chat UI.

### GUI status

The Monitor should show:

- `Generating` for active ChatGPT output.
- `Idle` for idle eligible tabs.
- `Limited` / `已达上限` for max-length conversations.
- `Offline` for stale tabs.

If a page is `Limited`, auto-reply should skip it.

### Provider smoke tests

Codex CLI:

```bash
printf 'Output exactly two lines: REPLY\nOK\n' | codex exec --color never --sandbox read-only --skip-git-repo-check --ignore-rules --output-last-message /tmp/codex_last.txt -
cat /tmp/codex_last.txt
```

Expected:

```text
REPLY
OK
```

On Windows, use a file under `.scratch/` instead of `/tmp/`.

## 6. Restart rules

After changing Python backend code, restart `local/run_all.py`.

After changing `userscript/chatgpt_bridge.user.js`, reinstall or update the Tampermonkey script and refresh ChatGPT tabs.

After changing `local/monitor.py`, close and reopen the Monitor GUI.

If the GUI still shows old labels, verify the process start time and command line. Users often look at an old Monitor window.

## 7. Common problems and fixes

### Backend rejects `codex_cli`

Symptom:

```json
{"ok": false, "error": "provider must be claude_cli or openai_compatible"}
```

Cause: the running backend is old.

Fix: restart `local/run_all.py` from the updated checkout. Verify `git rev-parse --short HEAD` matches the expected commit.

### GUI does not show Codex CLI

Cause: old Monitor process still running.

Fix: close the Monitor window and run `python local/monitor.py` again.

### Page says maximum length, but GUI looks idle

Cause: old GUI or old userscript.

Fix:

1. Update/reinstall the userscript.
2. Refresh the ChatGPT tab.
3. Restart Monitor.
4. Check `/all_snapshots` for `conversationLimited: true`.

### Auto-reply sends to a max-length conversation

This should not happen after the hard guard was added. Check:

- Userscript is updated and reports `conversationLimited`.
- Backend was restarted after update.
- `/all_snapshots` shows the same page as `conversationLimited: true`.

### Repeated duplicate replies or two windows fighting

Likely causes:

- Multiple backend instances on port 5000 or stale browser tabs.
- Old scripts still polling.

Fix:

1. Find the listener on port 5000.
2. Stop old backend processes.
3. Start one backend.
4. Refresh only the intended ChatGPT tabs.

### Provider times out and no reply appears

Recent versions use a fallback reply when the supervisor provider times out. If it still looks stuck:

- Check backend logs.
- Check CLI auth with `claude --help` or `codex exec --help`.
- For API mode, verify URL/key/model.

### `codex exec` outputs logs instead of only the answer

Use `--output-last-message <file>` and read that file. Do not parse terminal logs if a final-message file is available.

### API works in curl but not in GUI

Check whether the API Base URL already includes `/chat/completions`. The backend appends it only when missing. Also check that the model name is accepted by the server.

### Tkinter GUI fails on Linux

Install tkinter for the OS Python, or run headless without GUI.

Common packages:

```bash
sudo apt-get install python3-tk
```

On headless servers, configure `local/supervisor_config.json` directly.

## 8. Files agents should know

- `userscript/chatgpt_bridge.user.js`: browser-side polling and snapshot collection.
- `local/run_all.py`: local backend, supervisor, provider calls.
- `local/monitor.py`: Tkinter GUI and provider/prompt editor.
- `local/supervisor_config.json`: ignored local runtime config, may contain API key.
- `local/monitor_config.json`: ignored GUI language selection.
- `.gitignore`: must ignore local config, locks, scratch files, and provider output files.

## 9. Safe operating rules for agents

- Do not commit secrets or `local/supervisor_config.json`.
- Do not assume the running backend has the latest code; verify or restart it.
- Do not trust GUI state alone; confirm with `/status` and `/all_snapshots`.
- When adding provider support, update backend validation, GUI labels, README, and this file together.
- When testing Codex CLI, prefer `--output-last-message`.
- After material changes, commit, push, and record the change in the user's project memory if their workflow requires it.
