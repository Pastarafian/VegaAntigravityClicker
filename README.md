# VegaClick

VegaClick is a standalone, lightweight auto-clicker overlay for Antigravity IDE. It runs as a draggable pill on your desktop and automatically clicks action buttons in the agent chat sidebar — Accept All, Allow, Run, Continue, and more — so you can let the AI work unattended.

It operates through a two-part architecture: a **Deep Scanner** (JavaScript injected via CDP) that walks the DOM to find clickable targets, and a **Python overlay** that manages the UI, settings, and CDP communication.

**Works on Windows, Linux, and macOS.**

---

## Features

### Core Clicking
- **DOM Deep Scanner** — Recursively walks the DOM including Shadow DOMs to find action buttons
- **Agent Sidebar Whitelist** — Only clicks elements inside `.antigravity-agent-side-panel` / `#conversation`, preventing false clicks on tabs, editor, and other UI
- **Interactivity Filter** — Only matches actual clickable elements (`<button>`, `role=button`, `cursor:pointer`), ignoring matching text in chat messages
- **Priority Queue** — Buttons are ranked by priority (Accept All > Allow > Trust > Run > ...) and clicked in order
- **Deduplication** — Tracks recently clicked elements to prevent double-clicking

### Settings Drawer
Click the **⚙** icon to open the settings drawer above the pill:

- **Per-Keyword Toggles** — Enable or disable each button type individually (Accept All, Allow, Trust, Approve, Continue, Run, Retry, OK, Yes, Apply, Relocate, Changes Overview)
- **Presets** — Dropdown with `All`, `Safe`, `Minimal`, and `None` configurations. Hover tooltips describe what each preset enables. Selection persists across sessions.
- **Logs** — Opens a separate resizable log window showing timestamped click history (last 200 entries)
- **Reset Clicks** — Resets the click counter to zero (also resets the browser-side counter)
- **Delay Controls** (all persist in `settings.json`):

  | Control | Default | Unit | Description |
  |---|---|---|---|
  | Scan | 100 | ms | Delay after a click before rescanning the DOM |
  | Click | 150 | ms | Total delay before next click attempt (gap = Click − Scan) |
  | Typing | 5 | s | Cooldown after user types before clicking resumes |
  | Scroll | 15 | s | Cooldown after user scrolls before auto-scroll resumes |

- **Circuit Breaker Controls**:

  | Control | Default | Description |
  |---|---|---|
  | Retry | 3 | Max retry clicks before auto-pausing the clicker |
  | Timer | 20 | Time window (seconds) for counting retry clicks |

### Pill UI
| Button | Icon | Description |
|---|---|---|
| Settings | **⚙** | Open the settings drawer |
| Play/Pause | **⏸ / ▶** | Orange pause icon when inactive, green play icon when active |
| Scroll | **⇵** | Toggle auto-scroll on/off (red when paused) |
| Overlay | **◎** | Toggle the ripple effect on clicked buttons |
| Restart | **⟲** | Restart the clicker (reset all state, re-inject scanner) |
| Close | **✕** | Close VegaClick |

- **Click counter** — Shows total clicks, flashes on each click to confirm activity
- **Status text** — Shows current state: `Inactive`, `Searching...`, or page states (`1A` active, `2W` waiting, `1C` complete)
- **Tooltips** — Hover any button for a description
- Draggable from any part of the pill (except the interactive buttons). Dragging with the drawer open closes it.

### Intelligence
- **Circuit Breaker** — Auto-pauses clicking if the agent gets trapped in a retry loop (configurable max clicks + time window)
- **Typing Cooldown** — Pauses clicking while you're typing, resumes after the configured delay
- **Scroll Cooldown** — Pauses auto-scroll while you're manually scrolling
- **Scroll Pause Toggle** — Completely disable auto-scroll from the hotbar without changing the cooldown timer
- **Post-Click Rescan** — After clicking, waits `Scan` ms, rescans the DOM, waits the remaining gap, then clicks again to catch chained dialogs
- **Danger Check** — Blocks dangerous commands (`rm -rf`, `del /s`, `format`, etc.) from being auto-clicked
- **Stale Heartbeat Detection** — If the scanner stops responding for 8+ seconds, it re-injects automatically
- **Process Liveness Guard** — If the hotbar UI crashes, a dead-mans switch natively forces the browser Javascript scanner to unbind itself to prevent a headless runaway clicker
- **Live Antigravity Telemetry** — Extracts live quota/limit stats and current IDE activity status directly from the Antigravity background language server via CDP and proxy hooks

### Agentic Bridge
A local HTTP API at `127.0.0.1:4242` allows external scripts to inject prompts into the IDE chat and read DOM state programmatically.

---

## Setup

### Prerequisites
- Python 3.10+
- Windows, Linux, or macOS
- `websockets` Python library

### Installation

```bash
pip install websockets psutil
```

### Launch

**Windows:**
```cmd
launch.bat
```
Or without a console window:
```cmd
pythonw vegaclick.py
```

**Linux / macOS:**
```bash
python3 vegaclick.py &
```

---

## How It Works

1. VegaClick scans for Antigravity pages on CDP ports `9222–9242`
2. Every ~0.8s it injects `scanner.js` into each matching page via CDP
3. The scanner walks the DOM, finds clickable action buttons inside the agent sidebar, and returns a target list
4. VegaClick reads the results and updates the overlay UI
5. The scanner's built-in clicker fires independently in the browser, respecting all cooldowns and priority rules
6. Settings (enabled toggles, delays, preset) are synced to the browser as `window.__vc*` globals on every tick

---

## Settings Persistence

All settings are saved to `settings.json` in the VegaClick directory:

```json
{
  "enabled": { "accept all": true, "allow": true, "run": false, "..." : "..." },
  "scan_delay": 100,
  "click_delay": 150,
  "typing_delay": 5,
  "scroll_delay": 15,
  "cb_clicks": 3,
  "cb_seconds": 20,
  "preset": "Safe"
}
```

> **Note:** `active` (play/pause state), `overlay`, and `scroll_paused` are **not** persisted — VegaClick always starts paused with overlay enabled and auto-scroll active.

---

## Presets

| Preset | Enabled Buttons |
|---|---|
| **All** | Everything |
| **Safe** | Accept All, Allow, Trust, Continue, Retry, Changes Overview |
| **Minimal** | Accept All, Allow |
| **None** | Nothing |

---

## Cross-Platform Support

| Feature | Windows | Linux / macOS |
|---|---|---|
| Overlay UI (tkinter) | ✅ | ✅ |
| CDP scanning & clicking | ✅ | ✅ |
| Settings persistence | ✅ | ✅ |
| Agentic Bridge API | ✅ | ✅ |
| Dual-instance cleanup | `wmic` + `taskkill` | `ps aux` + `os.kill` |
| Auto-restart Antigravity | PowerShell + `taskkill` | `pgrep` + `killall` |
| Exe discovery | CIM / `LOCALAPPDATA` | `/proc/PID/exe` / `which` |

---

## Requirements

Antigravity (or any Electron-based IDE) must have remote debugging enabled on a port in the `9222–9242` range. VegaClick discovers the correct port automatically.

Launch Antigravity with:
```
--remote-debugging-port=9222
```
