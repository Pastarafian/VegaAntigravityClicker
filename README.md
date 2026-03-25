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

### Pill UI
- **⏸ / ▶** — Orange pause icon when inactive, green play icon when active
- **◎** — Overlay toggle: enables/disables the ripple effect on clicked buttons
- **✕** — Close VegaClick
- **Click counter** — Flashes on each click to confirm activity
- **Status text** — Shows current state: `Inactive`, `Searching...`, or `Active (Np) Xt`
- Draggable from any part of the pill (except the interactive buttons). Dragging with the drawer open closes it.

### Intelligence
- **Typing Cooldown** — Pauses clicking while you're typing, resumes after the configured delay
- **Scroll Cooldown** — Pauses auto-scroll while you're manually scrolling
- **Post-Click Rescan** — After clicking, waits `Scan` ms, rescans the DOM, waits the remaining gap, then clicks again to catch chained dialogs
- **Danger Check** — Blocks dangerous commands (`rm -rf`, `del /s`, `format`, etc.) from being auto-clicked
- **Stale Heartbeat Detection** — If the scanner stops responding for 8+ seconds, it re-injects automatically

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
pip install websockets
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
  "preset": "Safe"
}
```

> **Note:** `active` (play/pause state) and `overlay` state are **not** persisted — VegaClick always starts paused with overlay enabled.

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
