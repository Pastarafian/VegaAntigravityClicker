"""
VegaClick — Deep Scanner + Fast Clicker Architecture
==========================================================
Two-part system:
 1. DEEP SCANNER — walks entire DOM tree, shadow roots, iframes, finds ALL clickable elements
 2. FAST CLICKER — reads scanner results, clicks matching buttons instantly

The scanner feeds window.__vcTargets to the clicker.
"""

import tkinter as tk
import json
import asyncio
import websockets
import urllib.request
import threading
import time
import queue
import os
import subprocess
import sys
from datetime import datetime, timezone

VERSION = "v16"
PORT = 9222
POLL_INTERVAL = 0.8
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'settings.json')

DEBUG_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'debug_logs', f"{time.strftime('%Y-%m-%d')}_vegaclick-debug")
os.makedirs(DEBUG_LOG_DIR, exist_ok=True)
DEBUG_LOG_FILE = os.path.join(DEBUG_LOG_DIR, 'debug.log')

def debug_log(msg):
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
    formatted = f"[{timestamp}] {msg}\n"
    try:
        with open(DEBUG_LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(formatted)
    except:
        pass

KEYWORDS = [
    ('always allow', 'Always Allow', '#22c55e', 'Always allow tool access', 'both'),
    ('allow forever', 'Allow Forever', '#22c55e', 'Allow tool access forever', 'both'),
    ('accept all', 'Accept All', '#22c55e', 'Accept all pending code changes', 'ide'),
    ('allow', 'Allow', '#22c55e', 'Allow tool access for this conversation', 'both'),
    ('trust', 'Trust', '#22c55e', 'Trust a workspace or extension', 'ide'),
    ('approve', 'Approve', '#6366f1', 'Approve a pending action', 'both'),
    ('continue', 'Continue', '#22c55e', 'Continue the current operation', 'both'),
    ('run', 'Run', '#3b82f6', 'Run a terminal command', 'ide'),
    ('retry', 'Retry', '#f59e0b', 'Retry a failed operation', 'both'),
    ('ok', 'OK', '#64748b', 'Confirm a dialog prompt', 'both'),
    ('yes', 'Yes', '#64748b', 'Answer yes to a confirmation', 'both'),
    ('apply', 'Apply', '#64748b', 'Apply settings or changes', 'ide'),
    ('relocate', 'Relocate', '#64748b', 'Relocate a file or resource', 'ide'),
    ('send all', 'Send All', '#3b82f6', 'Send all pending prompts after task cancel', 'ide'),
    ('changes overview', 'Overview', '#a78bfa', 'Open the Changes Overview panel', 'ide'),
    ('needs attention', 'Needs Attention', '#f43f5e', 'Auto-switch to blocked subagent', 'both'),
    ('switch project', 'Switch Project', '#f43f5e', 'Auto-switch to blocked project', 'both'),
    ('switch workspace', 'Switch Workspace', '#f43f5e', 'Auto-switch to blocked workspace', 'both'),
    ('go back', 'Go Back', '#64748b', 'Return to previous chat', 'both'),
]

COUNTABLE_ACTIONS = {'submit', 'retry', 'approve', 'continue', 'run', 'accept all'}

PRESETS = {
    'All': {kw: True for kw, _, _, _, _ in KEYWORDS},
    'Safe': {
        'accept all': True, 'allow': True, 'trust': True,
        'approve': False, 'continue': True, 'run': False,
        'retry': True, 'ok': False, 'yes': False,
        'apply': False, 'relocate': False, 'send all': True, 'changes overview': True,
    },
    'Minimal': {
        'accept all': True, 'allow': True, 'trust': False,
        'approve': False, 'continue': False, 'run': False,
        'retry': False, 'ok': False, 'yes': False,
        'apply': False, 'relocate': False, 'send all': False, 'changes overview': False,
    },
    'None': {kw: False for kw, _, _, _, _ in KEYWORDS},
}

PRESET_DESCS = {
    'All': 'All buttons enabled',
    'Safe': 'Accept All, Allow, Trust, Continue, Retry, Overview',
    'Minimal': 'Accept All and Allow only',
    'None': 'All buttons disabled',
}

class Tooltip:
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip = None
        widget.bind('<Enter>', self.show)
        widget.bind('<Leave>', self.hide)
    def show(self, e=None):
        if self.tip: return
        if not self.text: return
        x = self.widget.winfo_rootx() + self.widget.winfo_width() // 2
        self.tip = tk.Toplevel(self.widget)
        self.tip.overrideredirect(True)
        self.tip.attributes('-topmost', True)
        lbl = tk.Label(self.tip, text=self.text, font=('Segoe UI', 8),
                       fg='#e6edf3', bg='#1c2128', justify='left', padx=6, pady=2, relief='solid', bd=1)
        lbl.pack()
        self.tip.update_idletasks()
        tw = self.tip.winfo_reqwidth()
        th = self.tip.winfo_reqheight()
        y = self.widget.winfo_rooty() - th - 2
        self.tip.geometry(f'+{x - tw // 2}+{y}')
    def hide(self, e=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None

class Toast:
    def __init__(self, parent, text, color='#a78bfa', duration=3500, on_click=None):
        self.root = parent
        self.text = text
        self.color = color
        self.duration = duration
        self.on_click = on_click
        self.win = tk.Toplevel(parent)
        self.win.overrideredirect(True)
        self.win.attributes('-topmost', True)
        self.win.attributes('-alpha', 0.0)
        self.win.configure(bg='#1c2128')
        
        inner = tk.Frame(self.win, bg='#1c2128', highlightthickness=1, highlightbackground='#30363d')
        inner.pack(padx=1, pady=1)
        inner.bind("<Button-1>", self._handle_click)
        
        icon = tk.Label(inner, text="\u2728", fg=color, bg='#1c2128', font=('Segoe UI', 9))
        icon.pack(side='left', padx=(8, 4), pady=4)
        icon.bind("<Button-1>", self._handle_click)
        
        lbl = tk.Label(inner, text=text, fg='#e6edf3', bg='#1c2128', font=('Segoe UI', 9, 'bold'))
        lbl.pack(side='left', padx=(0, 8), pady=4)
        lbl.bind("<Button-1>", self._handle_click)
        
        self.win.update_idletasks()
        w, h = self.win.winfo_reqwidth(), self.win.winfo_reqheight()
        px, py = parent.winfo_x(), parent.winfo_y()
        self.win.geometry(f"+{px + (530 - w) // 2}+{py - h - 10}")
        self.win.configure(cursor="hand2")
        
        self.fade_in()

    def _handle_click(self, e):
        if self.on_click:
            self.on_click()
        self.fade_out()

    def fade_in(self, alpha=0.0):

        if alpha < 0.95:
            alpha += 0.15
            self.win.attributes('-alpha', alpha)
            self.root.after(20, lambda: self.fade_in(alpha))
        else:
            self.root.after(self.duration, self.fade_out)

    def fade_out(self, alpha=0.95):
        if alpha > 0.0:
            alpha -= 0.1
            self.win.attributes('-alpha', alpha)
            self.root.after(20, lambda: self.fade_out(alpha))
        else:
            self.win.destroy()


def load_settings():
    debug_log("Loading settings from disk...")
    try:
        with open(SETTINGS_FILE, 'r') as f:
            data = json.load(f)
            saved = data.get('enabled', {})
            enabled = {kw: saved.get(kw, True) for kw, _, _, _, _ in KEYWORDS}
            scan_delay = data.get('scan_delay', 100)
            click_delay = data.get('click_delay', 150)
            preset = data.get('preset', 'All')
            typing_delay = data.get('typing_delay', 5)
            tab_delay = data.get('tab_delay', 15)
            scroll_delay = data.get('scroll_delay', 15)
            cb_clicks = data.get('cb_clicks', 3)
            cb_seconds = data.get('cb_seconds', 20)
            pill_x = data.get('pill_x', None)
            pill_y = data.get('pill_y', None)
            idle_alert_minutes = data.get('idle_alert_minutes', 5)
            auto_start = data.get('auto_start', False)
            pref_allow = data.get('pref_allow', 'allow in workspace')
            enabled_count = sum(1 for v in enabled.values() if v)
            debug_log(f"Settings loaded: preset={preset} scan={scan_delay}ms click={click_delay}ms typing={typing_delay}s scroll={scroll_delay}s cb={cb_clicks}/{cb_seconds}s idle={idle_alert_minutes}min autostart={auto_start} pref_allow={pref_allow} enabled_kw={enabled_count}/{len(enabled)} pill=({pill_x},{pill_y})")
            return enabled, scan_delay, click_delay, preset, typing_delay, scroll_delay, tab_delay, cb_clicks, cb_seconds, pill_x, pill_y, idle_alert_minutes, auto_start, pref_allow
    except Exception as e:
        debug_log(f"Settings load FAILED ({e}), using defaults")
        return {kw: True for kw, _, _, _, _ in KEYWORDS}, 100, 150, 'All', 5, 15, 3, 20, None, None, 5, False, 'allow in workspace'

def save_settings(enabled, scan_delay=100, click_delay=150, preset='All', typing_delay=5, scroll_delay=15, tab_delay=15,
                  cb_clicks=3, cb_seconds=20, pill_x=None, pill_y=None, idle_alert_minutes=5, auto_start=False, pref_allow='allow in workspace'):
    try:
        data = {
            'enabled': enabled, 'scan_delay': scan_delay, 'click_delay': click_delay,
            'preset': preset, 'typing_delay': typing_delay, 'scroll_delay': scroll_delay, 'tab_delay': tab_delay,
            'cb_clicks': cb_clicks, 'cb_seconds': cb_seconds,
            'idle_alert_minutes': idle_alert_minutes, 'auto_start': auto_start,
            'pref_allow': pref_allow,
        }
        if pill_x is not None:
            data['pill_x'] = pill_x
        if pill_y is not None:
            data['pill_y'] = pill_y
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(data, f, indent=2)
        debug_log(f"Settings saved: preset={preset} scan={scan_delay}ms click={click_delay}ms pill=({pill_x},{pill_y})")
    except Exception as e:
        debug_log(f"Settings save FAILED: {e}")

command_queue = queue.Queue()

# ═══════════════════════════════════════════════════════════════
# Agentic Bridge — inject prompts into IDE chat
# ═══════════════════════════════════════════════════════════════

INJECT_JS = """
(function() {
    var text = %s;
    var box = document.querySelector('textarea, [contenteditable="true"]') || document.querySelector('input[type="text"]');
    if (!box) return "No input box found";
    box.focus();
    if (box.tagName === 'TEXTAREA' || box.tagName === 'INPUT') {
        box.value = text;
        box.dispatchEvent(new Event('input', {bubbles: true}));
        box.dispatchEvent(new Event('change', {bubbles: true}));
    } else {
        box.innerText = text;
        box.dispatchEvent(new Event('input', {bubbles: true}));
        box.dispatchEvent(new Event('change', {bubbles: true}));
    }
    var btn = document.querySelector('button[type="submit"], button[aria-label*="Send"], button[title*="Send"], .send-button, [data-testid*="send"]') || (box.parentElement && box.parentElement.querySelector('button'));
    if (btn) {
        btn.click();
    } else {
        box.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true}));
        box.dispatchEvent(new KeyboardEvent('keypress', {key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true}));
        box.dispatchEvent(new KeyboardEvent('keyup', {key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true}));
    }
    return "Injected prompt";
})()
"""

READ_DOM_JS = """(function(){ return document.body.innerText; })()"""

def start_agentic_bridge():
    from http.server import BaseHTTPRequestHandler, HTTPServer
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args): pass
        def do_POST(self):
            try:
                length = int(self.headers.get('Content-Length', 0))
                data = json.loads(self.rfile.read(length).decode('utf-8')) if length > 0 else {}
                if self.path == '/api/inject':
                    prompt = data.get('prompt', '')
                    debug_log(f"Bridge /api/inject received: {prompt[:80]}")
                    command_queue.put({'action': 'inject', 'prompt': prompt})
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(b'{"status": "queued"}')
                else:
                    debug_log(f"Bridge 404: {self.path}")
                    self.send_response(404); self.end_headers()
            except Exception as e:
                debug_log(f"Bridge POST error: {e}")
                self.send_response(400); self.end_headers()
        def do_GET(self):
            if self.path == '/api/dom':
                res_q = queue.Queue()
                command_queue.put({'action': 'read_dom', 'res_q': res_q})
                try:
                    res = res_q.get(timeout=5)
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"dom": res}).encode('utf-8'))
                except queue.Empty:
                    self.send_response(504); self.end_headers()
            elif self.path == '/api/status':
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "version": VERSION}).encode('utf-8'))
    try:
        class ReusableServer(HTTPServer):
            allow_reuse_address = True
        debug_log("Agentic Bridge starting on 127.0.0.1:4242")
        ReusableServer(('127.0.0.1', 4242), Handler).serve_forever()
    except Exception as e:
        debug_log(f"Agentic Bridge FAILED to start: {e}")

# ═══════════════════════════════════════════════════════════════
# Process Cleanup
# ═══════════════════════════════════════════════════════════════
def cleanup_old_processes():
    """Kill all stale VegaClick/autoclicker processes before this instance starts."""
    debug_log(f"cleanup_old_processes: starting (my_pid={os.getpid()})")
    my_pid = os.getpid()
    kill_patterns = ['ide-autoclicker', 'vegaclaw', 'vegaclick', 'autoclicker']
    killed_count = 0
    
    # Fast path: Try using psutil natively (100x faster than spawning PowerShell)
    try:
        import psutil
        for p in psutil.process_iter(['pid', 'cmdline']):
            try:
                if p.info['pid'] == my_pid: continue
                cmdline = p.info.get('cmdline')
                if not cmdline: continue
                cmd_lower = ' '.join(cmdline).lower()
                if ('python' in cmd_lower or 'vegaclick' in cmd_lower) and any(kp in cmd_lower for kp in kill_patterns):
                    debug_log(f"Killing stale process PID={p.info['pid']}")
                    p.kill()
                    killed_count += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
        debug_log(f"cleanup_old_processes: killed {killed_count} (psutil path)")
        if killed_count > 0:
            time.sleep(0.5)
        return killed_count
    except Exception:
        pass

    # Slow path fallback: Use OS-native commands
    try:
        if sys.platform == 'win32':
            # Use PowerShell Get-CimInstance (wmic is deprecated and unreliable)
            ps_cmd = (
                "Get-CimInstance Win32_Process | "
                "Where-Object { $_.Name -match 'python' } | "
                "ForEach-Object { \"$($_.ProcessId)|$($_.CommandLine)\" }"
            )
            result = subprocess.run(
                ['powershell', '-NoProfile', '-Command', ps_cmd],
                capture_output=True, text=True, timeout=8
            )
            for line in result.stdout.strip().split('\n'):
                line = line.strip()
                if '|' not in line:
                    continue
                pid_str, cmd = line.split('|', 1)
                try:
                    pid = int(pid_str.strip())
                except ValueError:
                    continue
                cmd_lower = cmd.lower()
                if pid != my_pid and any(p in cmd_lower for p in kill_patterns):
                    try:
                        subprocess.run(
                            ['taskkill', '/PID', str(pid), '/F'],
                            capture_output=True, timeout=3
                        )
                        killed_count += 1
                    except Exception:
                        pass
        else:
            # Linux/macOS: use ps + grep
            result = subprocess.run(
                ['ps', 'aux'], capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.split('\n'):
                lower = line.lower()
                if any(p in lower for p in kill_patterns) and ('python' in lower):
                    parts = line.split()
                    if len(parts) >= 2:
                        try:
                            pid = int(parts[1])
                            if pid != my_pid:
                                os.kill(pid, 9)
                                killed_count += 1
                        except Exception:
                            pass
    except Exception:
        pass

    if killed_count > 0:
        # Wait for OS to fully release sockets held by killed processes
        time.sleep(1.0)
    return killed_count

# ═══════════════════════════════════════════════════════════════
# VegaClick — DEEP SCANNER + FAST CLICKER JS
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# CDP Helpers
# ═══════════════════════════════════════════════════════════════

async def get_targets_async():
    all_targets = []
    async def probe(port):
        try:
            loop = asyncio.get_event_loop()
            import urllib.request
            data = await asyncio.wait_for(
                loop.run_in_executor(None, lambda: urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=0.3).read()),
                timeout=0.5)
            return json.loads(data)
        except: return []
        
    ports_to_try = list(range(9222, 9242))
    import os
    appdata = os.environ.get('APPDATA', '')
    if appdata:
        dtp = os.path.join(appdata, 'Antigravity', 'DevToolsActivePort')
        if os.path.exists(dtp):
            try:
                with open(dtp, 'r') as f:
                    port_str = f.readline().strip()
                    if port_str.isdigit():
                        ports_to_try.append(int(port_str))
            except: pass

    results = await asyncio.gather(*[probe(p) for p in set(ports_to_try)], return_exceptions=True)
    for r in results:
        if isinstance(r, list): all_targets.extend(r)
    return all_targets

async def _cdp_eval(ws_url, js_code):
    try:
        async with websockets.connect(ws_url, close_timeout=1) as ws:
            await ws.send(json.dumps({"id":1,"method":"Runtime.evaluate","params":{"expression":js_code,"returnByValue":True}}))
            return json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
    except Exception as e:
        # Only log actual connection failures, not routine timeouts
        err_str = str(e)
        if 'Connect call failed' in err_str or 'refused' in err_str:
            debug_log(f"CDP connection failed: {err_str[:120]}")
        return None

# ═══════════════════════════════════════════════════════════════
# UI Pill
# ═══════════════════════════════════════════════════════════════

class VegaClickApp:
    def __init__(self):
        debug_log("Initializing VegaClickApp...")
        self.root = tk.Tk()
        self.root.title(f"VegaClick {VERSION}")
        self.root.overrideredirect(True)
        self.root.attributes('-topmost', True)
        self.root.attributes('-alpha', 0.95)
        self.root.configure(bg='#0e1117')

        self.active = False
        self.total_clicks = 0
        self.cooldown = 0
        self.status_text = "Searching..."
        self.status_color = "#f59e0b"
        self.last_msg = ""
        self.pages_connected = 0
        self.scan_targets = 0
        self.search_ticks = 0
        self.enabled, self.scan_delay, self.click_delay, self.preset, self.typing_delay, self.scroll_delay, self.tab_delay, self.cb_clicks, self.cb_seconds, self.pill_x, self.pill_y, self.idle_alert_minutes, self.auto_start, self.pref_allow = load_settings()
        debug_log(f"UI pill geometry: 530x30 at ({self.pill_x},{self.pill_y}), screen=({self.root.winfo_screenwidth()}x{self.root.winfo_screenheight()})")
        self.drawer = None
        self.toggle_labels = {}
        self.log_entries = []
        self.log_window = None
        self.preset_popup = None
        self._flash_until = 0
        self._page_states = (0, 0, 0)  # (active, waiting, complete)
        self._pages_total = 0
        self._last_busy_time = time.time()  # Idle alert tracking
        self._idle_alerted = False           # Prevents repeated alerts
        self._active_toasts = []
        self.telemetry = None

        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()

        # Use saved position if available, otherwise default to bottom-right
        px = self.pill_x if self.pill_x is not None else sw - 640
        py = self.pill_y if self.pill_y is not None else sh - 80
        self.root.geometry(f"530x30+{px}+{py}")

        tk.Label(self.root, text="VegaClick", font=("Segoe UI", 9, "bold"), fg='#00d4ff', bg='#0e1117').pack(side='left', padx=(8,4), pady=4)
        self.settings_btn = tk.Label(self.root, text="\u2699", font=("Segoe UI", 9), fg='white', bg='#1c2128', cursor='hand2', width=2)
        self.settings_btn.pack(side='left', padx=(0,8), ipady=1, ipadx=2, pady=4)
        self.settings_btn.bind('<Button-1>', lambda e: self.toggle_settings())
        Tooltip(self.settings_btn, 'Open settings drawer')

        self.ui_status = tk.Text(self.root, height=1, width=14, font=("Consolas", 9),
                                  bg='#0e1117', relief='flat', bd=0,
                                  highlightthickness=0, state='disabled',
                                  cursor='arrow', takefocus=0)
        self.ui_status.pack(side='left', padx=(0,2), pady=4)
        self.ui_status.tag_configure('green', foreground='#22c55e')
        self.ui_status.tag_configure('amber', foreground='#f59e0b')
        self.ui_status.tag_configure('blue', foreground='#00d4ff')
        self.ui_status.tag_configure('gray', foreground='#64748b')
        self.ui_status.tag_configure('red', foreground='#ef4444')
        self.ui_status.tag_configure('default', foreground='#e6edf3')
        
        self.ui_status_tt = Tooltip(self.ui_status, 'Inactive')

        self.ui_count = tk.Label(self.root, text="0 clicks", font=("Consolas", 9), fg='#64748b', bg='#0e1117', anchor='w')
        self.ui_count.pack(side='left', padx=(0,0), pady=4)

        # Pack right-side buttons BEFORE quota labels so they always reserve their space
        close_btn = tk.Label(self.root, text="\u2715", font=("Segoe UI", 10), bg='#1c2128', fg='#64748b',
                             width=2, anchor='center', bd=0, relief='flat', padx=4, pady=2)
        close_btn.pack(side='right', padx=(2, 8), pady=4)
        close_btn.bind('<Button-1>', lambda e: self.on_close())
        Tooltip(close_btn, 'Close VegaClick')

        self.restart_btn = tk.Label(self.root, text="\u27f2", font=("Segoe UI", 10), bg='#1c2128', fg='#64748b',
                                    width=2, anchor='center', bd=0, relief='flat', padx=4, pady=2)
        self.restart_btn.pack(side='right', padx=2, pady=4)
        self.restart_btn.bind('<Button-1>', lambda e: self.restart_clicker())
        Tooltip(self.restart_btn, 'Restart the clicker (reset state and re-inject scanner)')

        self.overlay_on = True
        self.overlay_btn = tk.Label(self.root, text="\u25ce", font=("Segoe UI", 10), bg='#2d333b', fg='#a78bfa',
                                    width=2, anchor='center', bd=0, relief='flat', padx=4, pady=2)
        self.overlay_btn.pack(side='right', padx=2, pady=4)
        self.overlay_btn.bind('<Button-1>', lambda e: self.toggle_overlay())
        Tooltip(self.overlay_btn, 'Toggle click ripple overlay')

        self.highlight_on = True
        self.highlight_btn = tk.Label(self.root, text="\u25a3", font=("Segoe UI", 10), bg='#2d333b', fg='#00d4ff',
                                      width=2, anchor='center', bd=0, relief='flat', padx=4, pady=2)
        self.highlight_btn.pack(side='right', padx=2, pady=4)
        self.highlight_btn.bind('<Button-1>', lambda e: self.toggle_highlight())
        Tooltip(self.highlight_btn, 'Toggle clicker highlight boxes')

        self.scroll_paused = False
        self.scroll_btn = tk.Label(self.root, text="\u21f5", font=("Segoe UI", 10), bg='#2d333b', fg='#00d4ff',
                                    width=2, anchor='center', bd=0, relief='flat', padx=4, pady=2)
        self.scroll_btn.pack(side='right', padx=2, pady=4)
        self.scroll_btn.bind('<Button-1>', lambda e: self.toggle_scroll())
        self.scroll_tt = Tooltip(self.scroll_btn, 'Auto-scroll: Active')

        self.switcher_on = False
        self.switcher_btn = tk.Label(self.root, text="⇄", font=("Segoe UI", 10), bg='#2d333b', fg='#64748b',
                                    width=2, anchor='center', bd=0, relief='flat', padx=4, pady=2)
        self.switcher_btn.pack(side='right', padx=2, pady=4)
        self.switcher_btn.bind('<Button-1>', lambda e: self.toggle_switcher())
        self.switcher_tt = Tooltip(self.switcher_btn, 'Auto-switch Project: Inactive')

        self.play_btn = tk.Label(self.root, text='\u23f8', font=("Segoe UI", 10), bg='#1c2128', fg='#f59e0b',
                                 width=2, anchor='center', bd=0, relief='flat', padx=4, pady=2)
        self.play_btn.pack(side='right', padx=2, pady=4)
        self.play_btn.bind('<Button-1>', lambda e: self.toggle_play())
        Tooltip(self.play_btn, 'Pause / resume clicker (Ctrl+Shift+/)')

        # Quota labels — packed left AFTER right buttons are reserved
        self._quota_colors = {'G': '#22c55e', 'F': '#3b82f6', 'C': '#f59e0b'}
        self._pct_colors = {'high': '#22c55e', 'med': '#f59e0b', 'low': '#ef4444'}
        self.quota_labels = {}
        self.quota_tooltips = {}
        for key in ['G', 'F', 'C']:
            name_lbl = tk.Label(self.root, text=key, font=('Consolas', 8, 'bold'),
                                fg=self._quota_colors[key], bg='#0e1117', anchor='w')
            name_lbl.pack(side='left', padx=(6, 0), pady=4)
            pct_lbl = tk.Label(self.root, text='', font=('Consolas', 8, 'bold'),
                               fg='#64748b', bg='#0e1117', anchor='w')
            pct_lbl.pack(side='left', padx=(1, 0), pady=4)
            tt = Tooltip(name_lbl, f'{key}: Loading...')
            self.quota_labels[key] = (name_lbl, pct_lbl)
            self.quota_tooltips[key] = tt
            pct_lbl.bind('<Enter>', lambda e, t=tt: t.show(e))
            pct_lbl.bind('<Leave>', lambda e, t=tt: t.hide(e))

        self.root.bind('<Button-1>', self._start_drag)
        self.root.bind('<B1-Motion>', self._on_drag)
        self.root.bind('<ButtonRelease-1>', self._end_drag)

        self.thread = threading.Thread(target=self.worker_loop, daemon=True)
        self.thread.start()
        debug_log("Worker thread started")
        
        self.quota_thread = threading.Thread(target=self._fetch_quota_worker, daemon=True)
        self.quota_thread.start()
        debug_log("Quota thread started")
        
        self._register_global_hotkey()
        debug_log("Global hotkey registered")
        self.refresh_ui()
        debug_log("Init complete — UI refresh loop started")

    def _fetch_quota_worker(self):
        import psutil, re, subprocess, ssl
        ctx = ssl.create_default_context()

        pid = None
        csrf_token = None
        ports = []

        while True:
            try:
                if not csrf_token or not pid or not psutil.pid_exists(pid):
                    pid = None; csrf_token = None; ports = []
                    for p in psutil.process_iter(['cmdline', 'pid']):
                        try:
                            cmdline = p.info.get('cmdline')
                            if not cmdline: continue
                            cmd = ' '.join(cmdline).lower()
                            if 'language_server' in cmd and 'antigravity' in cmd:
                                m = re.search(r'--csrf_token(?:=|\s+)([a-f0-9\-]+)', cmd)
                                if m:
                                    pid = p.info['pid']
                                    csrf_token = m.group(1)
                                    try:
                                        ports = [c.laddr.port for c in p.net_connections() if c.status == 'LISTEN']
                                    except Exception:
                                        pass
                                    break
                        except: pass

                if csrf_token and ports:
                    success = False
                    for port in set(ports):
                        try:
                            url = f'https://127.0.0.1:{port}/exa.language_server_pb.LanguageServerService/GetUserStatus'
                            req = urllib.request.Request(url, data=b'{"metadata": {}}', headers={
                                'X-Codeium-Csrf-Token': csrf_token, 'Content-Type': 'application/json'}, method="POST")
                            res = urllib.request.urlopen(req, context=ctx, timeout=2)
                            data = json.loads(res.read().decode('utf-8'))
                            
                            if 'cascadeModelConfigData' in data.get('userStatus', {}):
                                cmcs = data['userStatus']['cascadeModelConfigData'].get('clientModelConfigs', [])
                                colored_models = []
                                seen_names = set()
                                for cmc in cmcs:
                                    label = cmc.get('label', '')
                                    q = cmc.get('quotaInfo', {})
                                    name = '?'
                                    friendly = label
                                    model_color = 'default'
                                    if 'Flash' in label: name = 'F'; friendly = 'Flash'; model_color = 'flash'
                                    elif 'Gemini' in label: name = 'G'; friendly = 'Gemini'; model_color = 'gemini'
                                    elif 'Claude' in label: name = 'C'; friendly = 'Claude'; model_color = 'claude'
                                    elif 'GPT-OSS' in label: continue
                                    else: name = label[:3]
                                    
                                    if name in seen_names: continue
                                    seen_names.add(name)
                                    
                                    if 'premium' in q:
                                        frac = q['premium'].get('remainingFraction', 0.0 if 'resetTime' in q['premium'] else 1.0)
                                        reset = q['premium'].get('resetTime', '')
                                    else:
                                        frac = q.get('remainingFraction', 0.0 if 'resetTime' in q else 1.0)
                                        reset = q.get('resetTime', '')
                                        
                                    pct = int(frac * 100)
                                    pct_color = 'high' if pct >= 80 else 'med' if pct >= 40 else 'low'
                                    
                                    # Calculate countdown to reset
                                    countdown = ''
                                    if reset and 'T' in reset:
                                        try:
                                            # datetime imported at module level
                                            from datetime import datetime, timezone
                                            reset_dt = datetime.fromisoformat(reset.replace('Z', '+00:00'))
                                            now = datetime.now(timezone.utc)
                                            delta = reset_dt - now
                                            total_secs = max(0, int(delta.total_seconds()))
                                            hrs, rem = divmod(total_secs, 3600)
                                            mins = rem // 60
                                            if hrs > 0:
                                                countdown = f"{hrs}h {mins}m"
                                            elif mins > 0:
                                                countdown = f"{mins}m"
                                            else:
                                                countdown = "< 1m"
                                        except Exception:
                                            countdown = ''
                                    
                                    st = reset.replace('T', ' ').replace('Z', ' UTC') if reset and 'T' in reset else (reset or 'N/A')
                                    tooltip_text = f"{friendly}\nRemaining: {pct}%"
                                    if countdown:
                                        tooltip_text += f"\nResets in: {countdown}"
                                    tooltip_text += f"\nReset: {st}"
                                    
                                    colored_models.append({
                                        'name': name, 'model_color': model_color,
                                        'pct_str': f"{pct}%", 'pct_color': pct_color,
                                        'tooltip_text': tooltip_text
                                    })
                                
                                self.telemetry = {
                                    'colored_models': colored_models
                                }
                                success = True
                                break
                        except Exception:
                            pass
                    
                    if not success:
                        try:
                            p = psutil.Process(pid)
                            ports = [c.laddr.port for c in p.net_connections() if c.status == 'LISTEN']
                        except Exception:
                            pid = None
            except Exception as e:
                pass

            time.sleep(2.0)

    def _start_drag(self, e):
        if e.widget in (self.settings_btn, self.play_btn, self.overlay_btn, self.highlight_btn, self.restart_btn, self.scroll_btn, self.switcher_btn):
            return
        self._dx, self._dy = e.x_root, e.y_root
        self._orig_x, self._orig_y = self.root.winfo_x(), self.root.winfo_y()
        self.close_drawer()
    def _on_drag(self, e):
        if e.widget in (self.settings_btn, self.play_btn, self.overlay_btn, self.highlight_btn, self.restart_btn, self.scroll_btn, self.switcher_btn):
            return
        nx = self._orig_x + (e.x_root - self._dx)
        ny = self._orig_y + (e.y_root - self._dy)
        self.root.geometry(f"{'+' if nx >= 0 else ''}{nx}{'+' if ny >= 0 else ''}{ny}")

    def _end_drag(self, e):
        """Save pill position to settings after dragging."""
        new_x = self.root.winfo_x()
        new_y = self.root.winfo_y()
        if new_x != self.pill_x or new_y != self.pill_y:
            debug_log(f"Pill dragged: ({self.pill_x},{self.pill_y}) -> ({new_x},{new_y})")
            self.pill_x = new_x
            self.pill_y = new_y
            self._save_all()

    def on_close(self):
        debug_log("Closing VegaClickApp")
        self.close_drawer()
        self.root.withdraw()
        self.active = False
        def _force_exit():
            import os
            debug_log("Forcing OS exit")
            self.root.destroy()
            os._exit(0)
        self.root.after(1200, _force_exit)

    def prompt_restart(self):
        debug_log("Prompting user to restart Antigravity")
        import tkinter.messagebox as mb
        self.active = False
        self.play_btn.configure(bg='#1c2128')
        ans = mb.askyesno(
            "Connection Timeout", 
            "Antigravity hasn't loaded up in the correct mode to enable CDP access.\n\nWould you like to restart Antigravity with CDP (debug) settings enabled automatically?"
        )
        if ans:
            self.restart_antigravity()
        else:
            self.search_ticks = 0

    def restart_clicker(self):
        """Reset all clicker state and force re-injection of the scanner JS on next poll cycle."""
        debug_log("restart_clicker: resetting all state")
        # Clear JS-side state on all connected pages so scanner re-injects fresh
        self._pending_clicker_reset = True

        # Reset Python-side counters
        self.total_clicks = 0
        self.cooldown = 0
        self.last_msg = ""
        self.scan_targets = 0
        self.search_ticks = 0
        self._page_states = (0, 0, 0)
        self._flash_until = 0

        # Clear log
        self.log_entries.clear()
        if self.log_window:
            try:
                if self.log_window.winfo_exists():
                    self.log_text.configure(state='normal')
                    self.log_text.delete('1.0', 'end')
                    self.log_text.configure(state='disabled')
            except:
                pass

        # Flash status briefly to confirm the restart
        self.status_text = "Restarting clicker..."
        self.status_color = "#3b82f6"
        self.flash_click()

        # Ensure clicker is active after restart
        if not self.active:
            self.active = True
            self.play_btn.configure(bg='#2d333b', text='\u25b6', fg='#22c55e', font=('Segoe UI', 10))

        debug_log("restart_clicker: complete, active=True")
        self.add_log("Clicker restarted")

    def show_toast(self, text, color='#a78bfa', on_click=None):
        """Show a transient notification toast above the pill."""
        Toast(self.root, text, color, on_click=on_click)

    def focus_ide(self):
        """Bring the Antigravity IDE window to the foreground."""
        if sys.platform != 'win32':
            return
        try:
            import ctypes
            user32 = ctypes.windll.user32
            
            # Type signatures for Win32 API
            WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
            
            def enum_cb(hwnd, lparam):
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buf, length + 1)
                    title = buf.value
                    if "Antigravity" in title and "VegaClick" not in title:
                        # Restore if minimized
                        user32.ShowWindow(hwnd, 9) # SW_RESTORE
                        user32.SetForegroundWindow(hwnd)
                        return False # Found it
                return True

            user32.EnumWindows(WNDENUMPROC(enum_cb), 0)
        except Exception:
            pass



    def restart_antigravity(self):
        import subprocess
        import os
        import time
        import tkinter.messagebox as mb
        
        # Disable search ticks momentarily to avoid duplicate alerts while restarting
        self.search_ticks = 0
        self.status_text = "Restarting..."
        self.status_color = "#3b82f6"
        self.refresh_ui()

        try:
            exe_path = None
            if sys.platform == 'win32':
                # Fast path: psutil lookup
                try:
                    import psutil
                    for p in psutil.process_iter(['name', 'exe']):
                        if p.info.get('name', '').lower() == 'antigravity.exe':
                            exe_path = p.info.get('exe')
                            if exe_path: break
                except Exception:
                    pass

                if not exe_path:
                    # Slow path: use PowerShell/CIM to find the exe path
                    cmd = 'Get-CimInstance Win32_Process -Filter "Name=\'Antigravity.exe\'" | Select-Object -ExpandProperty ExecutablePath | Select-Object -First 1'
                    res = subprocess.run(["powershell", "-Command", cmd], capture_output=True, text=True, timeout=10)
                    exe_path = res.stdout.strip()
                
                if not exe_path or not os.path.exists(exe_path):
                    local_appdata = os.getenv('LOCALAPPDATA', '')
                    fallback = os.path.join(local_appdata, 'Programs', 'Antigravity', 'Antigravity.exe')
                    if os.path.exists(fallback):
                        exe_path = fallback
                    else:
                        mb.showerror("Error", "Could not locate Antigravity.exe path automatically.\nPlease restart it manually.")
                        return

                subprocess.run(["taskkill", "/F", "/IM", "Antigravity.exe"], capture_output=True)
                time.sleep(1.5)
                subprocess.Popen([exe_path, "--remote-debugging-port=9222"])
            else:
                # Linux/macOS: use pgrep/which/readlink to find and restart
                exe_path = None

                # Try to find the running process exe
                try:
                    pid_res = subprocess.run(['pgrep', '-f', '[Aa]ntigravity'], capture_output=True, text=True, timeout=5)
                    pids = pid_res.stdout.strip().split('\n')
                    for pid in pids:
                        pid = pid.strip()
                        if pid:
                            exe_link = f'/proc/{pid}/exe'
                            if os.path.exists(exe_link):
                                exe_path = os.readlink(exe_link)
                                break
                except: pass

                # Fallback: check common install locations
                if not exe_path:
                    for candidate in [
                        os.path.expanduser('~/.local/share/Antigravity/antigravity'),
                        '/opt/Antigravity/antigravity',
                        '/usr/bin/antigravity',
                        '/usr/local/bin/antigravity',
                    ]:
                        if os.path.exists(candidate):
                            exe_path = candidate
                            break

                # Fallback: use `which`
                if not exe_path:
                    try:
                        which_res = subprocess.run(['which', 'antigravity'], capture_output=True, text=True, timeout=5)
                        found = which_res.stdout.strip()
                        if found and os.path.exists(found):
                            exe_path = found
                    except: pass

                if not exe_path:
                    mb.showerror("Error", "Could not locate Antigravity binary automatically.\nPlease restart it manually.")
                    return

                # Kill existing Antigravity processes
                try:
                    subprocess.run(['killall', '-9', 'antigravity'], capture_output=True, timeout=5)
                    subprocess.run(['killall', '-9', 'Antigravity'], capture_output=True, timeout=5)
                except: pass
                time.sleep(1.5)
                subprocess.Popen([exe_path, "--remote-debugging-port=9222"])

            self.active = True
            self.play_btn.configure(bg='#2d333b', text='\u25b6', fg='#22c55e', font=('Segoe UI', 10))

        except Exception as e:
            mb.showerror("Restart Error", f"Failed to restart Antigravity: {e}")

    def toggle_overlay(self):
        self.overlay_on = not self.overlay_on
        debug_log(f"toggle_overlay: overlay_on={self.overlay_on}")
        self.overlay_btn.configure(bg='#2d333b' if self.overlay_on else '#1c2128', fg='#a78bfa' if self.overlay_on else '#64748b')

    def toggle_highlight(self):
        self.highlight_on = not self.highlight_on
        debug_log(f"toggle_highlight: highlight_on={self.highlight_on}")
        self.highlight_btn.configure(bg='#2d333b' if self.highlight_on else '#1c2128', fg='#00d4ff' if self.highlight_on else '#64748b')

    def toggle_scroll(self):
        debug_log(f"toggle_scroll: from {self.scroll_paused} to {not self.scroll_paused}")
        self.scroll_paused = not self.scroll_paused
        if not self.scroll_paused:
            self.scroll_btn.configure(bg='#2d333b', fg='#00d4ff')
            if hasattr(self, 'scroll_tt'): self.scroll_tt.text = 'Auto-scroll: Active'
        else:
            self.scroll_btn.configure(bg='#1c2128', fg='#64748b')
            if hasattr(self, 'scroll_tt'): self.scroll_tt.text = 'Auto-scroll: Paused'

    def toggle_switcher(self):
        self.switcher_on = not getattr(self, 'switcher_on', False)
        if self.switcher_on:
            self.switcher_btn.configure(bg='#2d333b', fg='#f43f5e')
            if hasattr(self, 'switcher_tt'): self.switcher_tt.text = 'Auto-switch Project: Active'
        else:
            self.switcher_btn.configure(bg='#1c2128', fg='#64748b')
            if hasattr(self, 'switcher_tt'): self.switcher_tt.text = 'Auto-switch Project: Inactive'

    def toggle_play(self):
        self.active = not getattr(self, 'active', True)
        debug_log(f"toggle_play: active set to {self.active}")
        if self.active:
            self.play_btn.configure(bg='#2d333b', text='\u25b6', fg='#22c55e', font=('Segoe UI', 10))
            # Reset idle tracking when resuming
            self._last_busy_time = time.time()
            self._idle_alerted = False
        else:
            self.play_btn.configure(bg='#1c2128', text='\u23f8', fg='#f59e0b', font=('Segoe UI', 10))



    def _save_all(self):
        """Convenience: save all current settings to disk."""
        debug_log(f"_save_all: preset={self.preset} scan={self.scan_delay} click={self.click_delay} typing={self.typing_delay} scroll={self.scroll_delay} tab={self.tab_delay} cb={self.cb_clicks}/{self.cb_seconds} idle={self.idle_alert_minutes}min")
        save_settings(
            self.enabled, self.scan_delay, self.click_delay, self.preset,
            self.typing_delay, self.scroll_delay, self.tab_delay, self.cb_clicks, self.cb_seconds,
            self.pill_x, self.pill_y, self.idle_alert_minutes, self.auto_start, self.pref_allow
        )

    def _register_global_hotkey(self):
        """Register Ctrl+Shift+/ as a global hotkey to toggle pause. Windows only (uses Win32 API, no deps)."""
        if sys.platform != 'win32':
            return
        def _hotkey_listener():
            try:
                import ctypes
                from ctypes import wintypes
                user32 = ctypes.windll.user32
                MOD_CONTROL = 0x0002
                MOD_SHIFT = 0x0004
                VK_OEM_2 = 0xBF  # Forward slash
                HOTKEY_ID = 1
            except Exception:
                return
            if not user32.RegisterHotKey(None, 1, MOD_CONTROL | MOD_SHIFT, VK_OEM_2):
                return
            try:
                msg = wintypes.MSG()
                while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
                    if msg.message == 0x0312:  # WM_HOTKEY
                        self.root.after(0, self.toggle_play)
            except Exception:
                pass
            finally:
                try:
                    user32.UnregisterHotKey(None, 1)
                except Exception:
                    pass
        threading.Thread(target=_hotkey_listener, daemon=True).start()

    def _play_idle_alert(self):
        """Play a system sound to signal agent idle. Non-blocking."""
        debug_log(f"Idle alert triggered after {self.idle_alert_minutes}min of inactivity")
        def _beep():
            try:
                if sys.platform == 'win32':
                    import winsound
                    winsound.Beep(800, 300)
                    time.sleep(0.15)
                    winsound.Beep(600, 300)
                else:
                    print('\a', end='', flush=True)
            except Exception:
                pass
        threading.Thread(target=_beep, daemon=True).start()

    def toggle_auto_start(self):
        """Toggle whether VegaClick launches on system startup."""
        self.auto_start = not self.auto_start
        debug_log(f"toggle_auto_start: auto_start={self.auto_start}")
        try:
            if sys.platform == 'win32':
                import winreg
                key_path = r'Software\Microsoft\Windows\CurrentVersion\Run'
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
                if self.auto_start:
                    # Use pythonw.exe to avoid console window
                    exe = sys.executable.replace('python.exe', 'pythonw.exe')
                    script = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'vegaclick.py'))
                    winreg.SetValueEx(key, 'VegaClick', 0, winreg.REG_SZ, f'"{exe}" "{script}"')
                else:
                    try:
                        winreg.DeleteValue(key, 'VegaClick')
                    except FileNotFoundError:
                        pass
                winreg.CloseKey(key)
            else:
                desktop_path = os.path.expanduser('~/.config/autostart/vegaclick.desktop')
                if self.auto_start:
                    os.makedirs(os.path.dirname(desktop_path), exist_ok=True)
                    script = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'vegaclick.py'))
                    with open(desktop_path, 'w') as f:
                        f.write(f'[Desktop Entry]\nType=Application\nName=VegaClick\nExec=python3 {script}\nHidden=false\n')
                else:
                    if os.path.exists(desktop_path):
                        os.remove(desktop_path)
        except Exception:
            pass
        self._save_all()
        # Update UI if drawer is open
        if hasattr(self, '_autostart_lbl') and self._autostart_lbl:
            try:
                self._autostart_lbl.configure(
                    fg='#22c55e' if self.auto_start else '#64748b',
                    bg='#2d333b' if self.auto_start else '#1c2128'
                )
            except Exception:
                pass

    def toggle_settings(self):
        if self.drawer and self.drawer.winfo_exists():
            self.close_drawer()
        else:
            self.open_drawer()

    def close_drawer(self):
        self.close_preset_popup()
        if self.drawer:
            try:
                if self.drawer.winfo_exists():
                    self.drawer.destroy()
            except:
                pass
        self.drawer = None

    def open_drawer(self):
        self.close_drawer()
        d = tk.Toplevel(self.root)
        self.drawer = d  # Immediately assign to prevent orphaned windows if update_idletasks processes a double-click
        d.withdraw()  # Hide initially
        d.attributes('-alpha', 0.0) # Completely transparent during layout
        d.overrideredirect(True)
        d.attributes('-topmost', True)
        d.configure(bg='#0e1117')

        # Header
        header = tk.Frame(d, bg='#0e1117')
        header.pack(fill='x', padx=8, pady=(8, 4))
        tk.Label(header, text="Click Toggles", font=("Segoe UI", 9, "bold"),
                 fg='#64748b', bg='#0e1117').pack(side='left')

        self.preset_btn = tk.Label(header, text=f"\u25be {self.preset}", font=("Segoe UI", 8, "bold"),
                                   fg='#a78bfa', bg='#1c2128', cursor='hand2', padx=4, pady=1)
        self.preset_btn.pack(side='right')
        self.preset_btn.bind('<Button-1>', lambda e: self.open_preset_dropdown())
        Tooltip(self.preset_btn, 'Select a preset configuration')

        # Toggle grid
        grid = tk.Frame(d, bg='#0e1117')
        grid.pack(fill='x', padx=6, pady=(0, 8))

        self.toggle_labels = {}
        for idx, (kw, display, color, desc, ctx) in enumerate(KEYWORDS):
            row = idx // 3
            col = idx % 3
            on = self.enabled.get(kw, True)
            lbl = tk.Label(grid, text=display, font=("Segoe UI", 8, "bold"),
                           fg='#00d4ff' if on else '#64748b',
                           bg='#2d333b' if on else '#1c2128',
                           width=12, cursor='hand2',
                           relief='flat', padx=4, pady=3)
            lbl.grid(row=row, column=col, padx=3, pady=2, sticky='ew')
            lbl.bind('<Button-1>', lambda e, k=kw: self.click_toggle(k))
            Tooltip(lbl, desc)
            self.toggle_labels[kw] = (lbl, color)

        for c in range(3):
            grid.columnconfigure(c, weight=1)

        # Row 4 fill: Reset Clicks + Logs alongside last keyword row
        last_kw_row = (len(KEYWORDS) - 1) // 3
        last_kw_col = (len(KEYWORDS) - 1) % 3
        if last_kw_col < 1:
            reset_btn = tk.Label(grid, text="Reset Clicks", font=("Segoe UI", 8, "bold"),
                                 fg='#f59e0b', bg='#1c2128', cursor='hand2',
                                 width=12, relief='flat', padx=4, pady=3)
            reset_btn.grid(row=last_kw_row, column=1, padx=3, pady=2, sticky='ew')
            reset_btn.bind('<Button-1>', lambda e: self.reset_clicks())
            Tooltip(reset_btn, 'Reset the click counter to 0')
        if last_kw_col < 2:
            logs_btn = tk.Label(grid, text="Logs", font=("Segoe UI", 8, "bold"),
                                fg='#a78bfa', bg='#2d333b', cursor='hand2',
                                width=12, relief='flat', padx=4, pady=3)
            col_for_logs = 2 if last_kw_col < 2 else 1
            logs_btn.grid(row=last_kw_row, column=col_for_logs, padx=3, pady=2, sticky='ew')
            logs_btn.bind('<Button-1>', lambda e: self.open_log_window())
            Tooltip(logs_btn, 'Open click history log window')
        base_row = last_kw_row + 1

        # Input row 1: Scan | Click | Scroll
        scan_cell = tk.Frame(grid, bg='#1c2128')
        scan_cell.grid(row=base_row, column=0, padx=3, pady=2, sticky='ew')
        tk.Label(scan_cell, text="Scan:", font=("Segoe UI", 8, "bold"),
                 fg='#00d4ff', bg='#1c2128').pack(side='left', padx=(4,2))
        self.scan_entry = tk.Entry(scan_cell, width=4, font=("Segoe UI", 8, "bold"),
                                  bg='#1c2128', fg='#e6edf3', insertbackground='#e6edf3',
                                  relief='flat', bd=0, highlightthickness=0)
        self.scan_entry.insert(0, str(self.scan_delay))
        self.scan_entry.pack(side='left')
        tk.Label(scan_cell, text="ms", font=("Consolas", 8),
                 fg='#64748b', bg='#1c2128').pack(side='left', padx=(0,4))

        click_cell = tk.Frame(grid, bg='#1c2128')
        click_cell.grid(row=base_row, column=1, padx=3, pady=2, sticky='ew')
        tk.Label(click_cell, text="Click:", font=("Segoe UI", 8, "bold"),
                 fg='#00d4ff', bg='#1c2128').pack(side='left', padx=(4,2))
        self.click_entry = tk.Entry(click_cell, width=4, font=("Segoe UI", 8, "bold"),
                                   bg='#1c2128', fg='#e6edf3', insertbackground='#e6edf3',
                                   relief='flat', bd=0, highlightthickness=0)
        self.click_entry.insert(0, str(self.click_delay))
        self.click_entry.pack(side='left')
        tk.Label(click_cell, text="ms", font=("Consolas", 8),
                 fg='#64748b', bg='#1c2128').pack(side='left', padx=(0,4))

        scroll_cell = tk.Frame(grid, bg='#1c2128')
        scroll_cell.grid(row=base_row, column=2, padx=3, pady=2, sticky='ew')
        tk.Label(scroll_cell, text="Scroll:", font=("Segoe UI", 8, "bold"),
                 fg='#00d4ff', bg='#1c2128').pack(side='left', padx=(4,2))
        self.scroll_entry = tk.Entry(scroll_cell, width=3, font=("Segoe UI", 8, "bold"),
                                    bg='#1c2128', fg='#e6edf3', insertbackground='#e6edf3',
                                    relief='flat', bd=0, highlightthickness=0)
        self.scroll_entry.insert(0, str(self.scroll_delay))
        self.scroll_entry.pack(side='left')
        tk.Label(scroll_cell, text="s", font=("Consolas", 8),
                 fg='#64748b', bg='#1c2128').pack(side='left', padx=(0,4))

        # Input row 2: Typing | Idle | Retry
        typing_cell = tk.Frame(grid, bg='#1c2128')
        typing_cell.grid(row=base_row + 1, column=0, padx=3, pady=2, sticky='ew')
        tk.Label(typing_cell, text="Typing:", font=("Segoe UI", 8, "bold"),
                 fg='#00d4ff', bg='#1c2128').pack(side='left', padx=(4,2))
        self.typing_entry = tk.Entry(typing_cell, width=3, font=("Segoe UI", 8, "bold"),
                                    bg='#1c2128', fg='#e6edf3', insertbackground='#e6edf3',
                                    relief='flat', bd=0, highlightthickness=0)
        self.typing_entry.insert(0, str(self.typing_delay))

        self.typing_entry.pack(side='left')
        tk.Label(typing_cell, text="s", font=("Consolas", 8),
                 fg='#64748b', bg='#1c2128').pack(side='left', padx=(0,4))

        tab_cell = tk.Frame(grid, bg='#1c2128')
        tab_cell.grid(row=base_row + 1, column=1, padx=3, pady=2, sticky='ew')
        tk.Label(tab_cell, text="Tab:", font=("Segoe UI", 8, "bold"),
                 fg='#00d4ff', bg='#1c2128').pack(side='left', padx=(4,2))
        self.tab_entry = tk.Entry(tab_cell, width=3, font=("Segoe UI", 8, "bold"),
                                    bg='#1c2128', fg='#e6edf3', insertbackground='#e6edf3',
                                    relief='flat', bd=0, highlightthickness=0)
        self.tab_entry.insert(0, str(self.tab_delay))
        self.tab_entry.pack(side='left')
        tk.Label(tab_cell, text="s", font=("Consolas", 8),
                 fg='#64748b', bg='#1c2128').pack(side='left', padx=(0,4))

        idle_cell = tk.Frame(grid, bg='#1c2128')
        idle_cell.grid(row=base_row + 1, column=2, padx=3, pady=2, sticky='ew')
        idle_lbl = tk.Label(idle_cell, text="Idle:", font=("Segoe UI", 8, "bold"),
                 fg='#f59e0b', bg='#1c2128')
        idle_lbl.pack(side='left', padx=(4,2))
        Tooltip(idle_lbl, 'Play a sound when agent is idle for this many minutes (0 to disable)')
        self.idle_entry = tk.Entry(idle_cell, width=2, font=("Segoe UI", 8, "bold"),
                                  bg='#1c2128', fg='#e6edf3', insertbackground='#e6edf3',
                                  relief='flat', bd=0, highlightthickness=0)
        self.idle_entry.insert(0, str(self.idle_alert_minutes))
        self.idle_entry.pack(side='left')
        tk.Label(idle_cell, text="min", font=("Consolas", 8),
                 fg='#64748b', bg='#1c2128').pack(side='left', padx=(0,4))
        self.idle_entry.bind('<KeyRelease>', lambda e: self._save_delays())

        retry_cell = tk.Frame(grid, bg='#1c2128')
        retry_cell.grid(row=base_row + 2, column=0, padx=3, pady=2, sticky='ew')
        retry_lbl = tk.Label(retry_cell, text="Retry:", font=("Segoe UI", 8, "bold"),
                 fg='#ef4444', bg='#1c2128')
        retry_lbl.pack(side='left', padx=(4,2))
        Tooltip(retry_lbl, 'Max retry clicks before pausing clicker')
        self.cb_clicks_entry = tk.Entry(retry_cell, width=2, font=("Segoe UI", 8, "bold"),
                                        bg='#1c2128', fg='#e6edf3', insertbackground='#e6edf3',
                                        relief='flat', bd=0, highlightthickness=0)
        self.cb_clicks_entry.insert(0, str(self.cb_clicks))
        self.cb_clicks_entry.pack(side='left')
        tk.Label(retry_cell, text="tries", font=("Consolas", 8),
                 fg='#64748b', bg='#1c2128').pack(side='left', padx=(0,4))

        # Input row 3: Retry | Timer
        timer_cell = tk.Frame(grid, bg='#1c2128')
        timer_cell.grid(row=base_row + 2, column=1, padx=3, pady=2, sticky='ew')
        timer_lbl = tk.Label(timer_cell, text="Timer:", font=("Segoe UI", 8, "bold"),
                 fg='#ef4444', bg='#1c2128')
        timer_lbl.pack(side='left', padx=(4,2))
        Tooltip(timer_lbl, 'Time window for counting retry clicks (seconds)')
        self.cb_secs_entry = tk.Entry(timer_cell, width=2, font=("Segoe UI", 8, "bold"),
                                      bg='#1c2128', fg='#e6edf3', insertbackground='#e6edf3',
                                      relief='flat', bd=0, highlightthickness=0)
        self.cb_secs_entry.insert(0, str(self.cb_seconds))
        self.cb_secs_entry.pack(side='left')
        tk.Label(timer_cell, text="s", font=("Consolas", 8),
                 fg='#64748b', bg='#1c2128').pack(side='left', padx=(0,4))

        restart_ide_btn = tk.Label(grid, text="Restart IDE", font=("Segoe UI", 8, "bold"),
                                   fg='#3b82f6', bg='#1c2128', cursor='hand2',
                                   width=12, relief='flat', padx=4, pady=3)
        restart_ide_btn.grid(row=base_row + 2, column=2, padx=3, pady=2, sticky='ew')
        restart_ide_btn.bind('<Button-1>', lambda e: self.restart_ide())
        Tooltip(restart_ide_btn, 'Restart Antigravity/Cursor with debugging active')


        self.typing_entry.bind('<KeyRelease>', lambda e: self._save_delays())
        self.tab_entry.bind('<KeyRelease>', lambda e: self._save_delays())
        self.scroll_entry.bind('<KeyRelease>', lambda e: self._save_delays())
        self.scan_entry.bind('<KeyRelease>', lambda e: self._save_delays())
        self.click_entry.bind('<KeyRelease>', lambda e: self._save_delays())
        self.cb_clicks_entry.bind('<KeyRelease>', lambda e: self._save_delays())
        self.cb_secs_entry.bind('<KeyRelease>', lambda e: self._save_delays())

        # Auto-Start
        self._autostart_lbl = tk.Label(grid, text="Auto-Start", font=("Segoe UI", 8, "bold"),
                                       fg='#22c55e' if self.auto_start else '#64748b',
                                       bg='#2d333b' if self.auto_start else '#1c2128',
                                       cursor='hand2', width=12, relief='flat', padx=4, pady=3)
        self._autostart_lbl.grid(row=base_row + 1, column=2, padx=3, pady=2, sticky='ew')
        self._autostart_lbl.bind('<Button-1>', lambda e: self.toggle_auto_start())
        Tooltip(self._autostart_lbl, 'Launch VegaClick on system startup')



        # Auto-Allow Preference
        pref_cell = tk.Frame(grid, bg='#1c2128')
        pref_cell.grid(row=base_row + 3, column=0, columnspan=3, padx=3, pady=2, sticky='ew')
        tk.Label(pref_cell, text="Auto-Allow Pref:", font=("Segoe UI", 8, "bold"), fg='#a78bfa', bg='#1c2128').pack(side='left', padx=(4,2))
        
        self.pref_btn = tk.Label(pref_cell, text=f"\u25be {self.pref_allow.title()}", font=("Segoe UI", 8, "bold"), fg='#e6edf3', bg='#2d333b', cursor='hand2', padx=4, pady=2)
        self.pref_btn.pack(side='left', fill='x', expand=True, padx=(2,4))
        self.pref_btn.bind('<Button-1>', lambda e: self.toggle_pref_allow())
        Tooltip(self.pref_btn, 'Cycle preferred auto-allow option (Once / Workspace / Every Time)')

        # Legend row
        row8 = tk.Frame(grid, bg='#0e1117')
        row8.grid(row=base_row + 4, column=0, columnspan=3, padx=3, pady=(4, 2), sticky='ew')
        
        a_lbl = tk.Label(row8, text="A", font=("Consolas", 8, "bold"),

                 fg='#22c55e', bg='#0e1117')
        a_lbl.pack(side='left', padx=(4,0))
        Tooltip(a_lbl, 'Active \u2014 agent is thinking/generating')
        tk.Label(row8, text="= Active", font=("Segoe UI", 8),
                 fg='#64748b', bg='#0e1117').pack(side='left', padx=(1,4))
        w_lbl = tk.Label(row8, text="W", font=("Consolas", 8, "bold"),
                 fg='#f59e0b', bg='#0e1117')
        w_lbl.pack(side='left')
        Tooltip(w_lbl, 'Waiting \u2014 buttons present, awaiting clicks')
        tk.Label(row8, text="= Waiting", font=("Segoe UI", 8),
                 fg='#64748b', bg='#0e1117').pack(side='left', padx=(1,4))
        c_lbl = tk.Label(row8, text="I", font=("Consolas", 8, "bold"),
                 fg='#ef4444', bg='#0e1117')
        c_lbl.pack(side='left')
        Tooltip(c_lbl, 'Inactive \u2014 IDE idle, nothing to click')
        tk.Label(row8, text="= Inactive", font=("Segoe UI", 8),
                 fg='#64748b', bg='#0e1117').pack(side='left', padx=(1,2))

        # Position the hotkey indicator on the far right of the legend row
        hotkey_lbl = tk.Label(row8, text="⏸  Ctrl+Shift+/", font=("Consolas", 8),
                              fg='#64748b', bg='#0e1117')
        hotkey_lbl.pack(side='right', padx=(0, 4))
        Tooltip(hotkey_lbl, 'Global hotkey to pause / resume clicker')




        # Position above pill
        d.update_idletasks()
        drawer_h = d.winfo_reqheight()
        pill_x = self.root.winfo_x()
        pill_y = self.root.winfo_y()
        d.geometry(f"530x{drawer_h}+{pill_x}+{pill_y - drawer_h - 2}")
        d.deiconify()  # Show now that geometry is set
        d.attributes('-alpha', 0.95) # Restore transparency now that it's positioned

    def toggle_pref_allow(self):
        opts = ['allow once', 'allow in workspace', 'allow every time']
        try:
            idx = opts.index(self.pref_allow)
        except ValueError:
            idx = 0
        self.pref_allow = opts[(idx + 1) % len(opts)]
        if hasattr(self, 'pref_btn'):
            self.pref_btn.configure(text=f"\u25be {self.pref_allow.title()}")
        self._save_all()

    def _save_delays(self):
        try:
            self.scan_delay = max(0, int(self.scan_entry.get()))
        except ValueError:
            pass
        try:
            self.click_delay = max(0, int(self.click_entry.get()))
        except ValueError:
            pass
        try:
            self.typing_delay = max(0, int(self.typing_entry.get()))
            self.tab_delay = max(0, int(self.tab_entry.get()))
        except ValueError:
            pass
        try:
            self.scroll_delay = max(0, int(self.scroll_entry.get()))
        except ValueError:
            pass
        try:
            self.cb_clicks = max(1, int(self.cb_clicks_entry.get()))
        except ValueError:
            pass
        try:
            self.cb_seconds = max(1, int(self.cb_secs_entry.get()))
        except ValueError:
            pass
        try:
            self.idle_alert_minutes = max(0, int(self.idle_entry.get()))
            self._idle_alerted = False
        except (ValueError, AttributeError):
            pass
        self._save_all()

    def click_toggle(self, kw):
        self.enabled[kw] = not self.enabled.get(kw, True)
        debug_log(f"click_toggle: {kw} -> {self.enabled[kw]}")
        self._save_all()
        if kw in self.toggle_labels:
            lbl, color = self.toggle_labels[kw]
            on = self.enabled[kw]
            lbl.configure(fg='#00d4ff' if on else '#64748b',
                          bg='#2d333b' if on else '#1c2128')

    def flash_click(self):
        self._flash_until = time.time() + 0.15

    def restart_ide(self):
        def _bg():
            import subprocess
            cmd = """
            $procs = Get-Process -Name "Antigravity", "Cursor", "Code" -ErrorAction SilentlyContinue
            if ($procs) {
                $path = $procs[0].Path
                Stop-Process -Name "Antigravity" -Force -ErrorAction SilentlyContinue
                Stop-Process -Name "Cursor" -Force -ErrorAction SilentlyContinue
                Stop-Process -Name "Code" -Force -ErrorAction SilentlyContinue
                Start-Sleep -Seconds 1
                if ($path) {
                    Start-Process -FilePath $path -ArgumentList "--remote-debugging-port=9222"
                } else {
                    Start-Process -FilePath "antigravity" -ArgumentList "--remote-debugging-port=9222"
                }
            } else {
                Start-Process -FilePath "antigravity" -ArgumentList "--remote-debugging-port=9222"
            }
            """
            subprocess.run(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", cmd])
        import threading
        threading.Thread(target=_bg, daemon=True).start()
        Toast(self.root, "Restarting IDE...", color="#3b82f6")

    def reset_clicks(self):
        self.total_clicks = 0
        self._pending_reset = True

    def open_log_window(self):
        if self.log_window:
            try:
                if self.log_window.winfo_exists():
                    self.log_window.lift()
                    return
            except:
                pass
        w = tk.Toplevel(self.root)
        w.title("VegaClick Logs")
        w.geometry("400x300")
        w.configure(bg='#0e1117')
        w.attributes('-topmost', True)
        self.log_text = tk.Text(w, bg='#0e1117', fg='#e6edf3', font=("Consolas", 9),
                                relief='flat', wrap='word', state='disabled',
                                insertbackground='#e6edf3')
        scrollbar = tk.Scrollbar(w, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        self.log_text.pack(fill='both', expand=True, padx=4, pady=4)
        for entry in self.log_entries:
            self._append_log_text(entry)
        self.log_window = w

    def add_log(self, msg):
        timestamp = time.strftime('%H:%M:%S')
        entry = f"[{timestamp}] {msg}"
        self.log_entries.append(entry)
        if len(self.log_entries) > 200:
            self.log_entries = self.log_entries[-200:]
        if self.log_window:
            try:
                if self.log_window.winfo_exists():
                    self._append_log_text(entry)
            except:
                pass

    def _append_log_text(self, text):
        try:
            self.log_text.configure(state='normal')
            self.log_text.insert('end', text + '\n')
            self.log_text.see('end')
            self.log_text.configure(state='disabled')
        except:
            pass

    def open_preset_dropdown(self):
        if self.preset_popup:
            try:
                if self.preset_popup.winfo_exists():
                    self.close_preset_popup()
                    return
            except:
                pass
        self.close_preset_popup()
        popup = tk.Toplevel(self.root)
        popup.overrideredirect(True)
        popup.attributes('-topmost', True)
        popup.configure(bg='#1c2128')
        for name, desc in PRESET_DESCS.items():
            lbl = tk.Label(popup, text=name, font=("Segoe UI", 8, "bold"),
                           fg='#a78bfa', bg='#1c2128', cursor='hand2',
                           padx=8, pady=4, anchor='w', width=10)
            lbl.pack(fill='x')
            lbl.bind('<Button-1>', lambda e, n=name: self.apply_preset(n))
            lbl.bind('<Enter>', lambda e, l=lbl: l.configure(bg='#2d333b'))
            lbl.bind('<Leave>', lambda e, l=lbl: l.configure(bg='#1c2128'))
            Tooltip(lbl, desc)
        popup.update_idletasks()
        x = self.preset_btn.winfo_rootx()
        y = self.preset_btn.winfo_rooty() - popup.winfo_reqheight()
        popup.geometry(f'+{x}+{y}')
        self.preset_popup = popup

    def close_preset_popup(self):
        if self.preset_popup:
            try:
                if self.preset_popup.winfo_exists():
                    self.preset_popup.destroy()
            except:
                pass
        self.preset_popup = None

    def apply_preset(self, name):
        debug_log(f"apply_preset: {self.preset} -> {name}")
        self.preset = name
        preset_toggles = PRESETS[name]
        for kw in self.enabled:
            self.enabled[kw] = preset_toggles.get(kw, True)
        self._save_all()
        for kw, (lbl, color) in self.toggle_labels.items():
            on = self.enabled[kw]
            lbl.configure(fg='#00d4ff' if on else '#64748b',
                          bg='#2d333b' if on else '#1c2128')
        if hasattr(self, 'preset_btn'):
            self.preset_btn.configure(text=f'\u25be {name}')
        self.close_preset_popup()

    def refresh_ui(self):
        try:
            self.root.attributes('-topmost', True)
        except:
            pass
        self.ui_status.configure(state='normal')
        self.ui_status.delete('1.0', 'end')
        
        tt_text = 'Unknown'
        # Status dot
        if self.status_text.startswith('PAUSED'):
            self.ui_status.insert('end', '● ', 'amber')
            tt_text = self.status_text
        elif self.active:
            if getattr(self, '_pages_total', 0) == 0:
                self.ui_status.insert('end', '● ', 'amber')
                tt_text = 'Searching...'
            else:
                self.ui_status.insert('end', '● ', 'green')
                tt_text = 'Active'
        else:
            self.ui_status.insert('end', '● ', 'red')
            tt_text = 'Inactive'
            
        if hasattr(self, 'ui_status_tt'):
            self.ui_status_tt.text = tt_text
        
        # Permanent IDE Activity Readings
        a, w, c = getattr(self, '_page_states', (0, 0, 0))
        segments = []
        if a > 0: segments.append((f'{a}A', 'green'))
        if w > 0: segments.append((f'{w}W', 'amber'))
        if c > 0: segments.append((f'{c}I', 'red'))
        
        if segments:
            for i, (text, tag) in enumerate(segments):
                if i > 0: self.ui_status.insert('end', '-', 'gray')
                self.ui_status.insert('end', text, tag)
        else:
            self.ui_status.insert('end', '0I', 'red')
        
        chars = len(self.ui_status.get('1.0', 'end-1c'))
        self.ui_status.configure(state='disabled', width=chars)
        flash_bg = '#2d333b' if time.time() < self._flash_until else '#0e1117'
        if hasattr(self, 'cooldown') and self.cooldown > 0 and self.active:
            t = f"{self.cooldown/1000.0:.1f}s wait"
            self.ui_count.configure(text=t, fg="#f59e0b", bg=flash_bg, width=len(t))
        else:
            t = f"{self.total_clicks} clicks"
            self.ui_count.configure(text=t, fg="#64748b", bg=flash_bg, width=len(t))
            
        # Update individual quota labels
        if hasattr(self, 'telemetry') and self.telemetry and 'colored_models' in self.telemetry:
            tel_hash = hash(str(self.telemetry))
            if getattr(self, '_last_telemetry_hash', None) != tel_hash:
                self._last_telemetry_hash = tel_hash
                for item in self.telemetry['colored_models']:
                    key = item['name']
                    if key in self.quota_labels:
                        name_lbl, pct_lbl = self.quota_labels[key]
                        pct_color = self._pct_colors.get(item['pct_color'], '#64748b')
                        pct_lbl.configure(text=item['pct_str'], fg=pct_color)
                        if key in self.quota_tooltips:
                            self.quota_tooltips[key].text = item.get('tooltip_text', f'{key}: --')
            
        self.root.after(200, self.refresh_ui)

    # Telemetry is now automatically extracted via CDP in JS and populated into self.telemetry.


    def worker_loop(self):
        debug_log("Worker loop starting")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        threading.Thread(target=start_agentic_bridge, daemon=True).start()
        debug_log("Agentic bridge thread spawned")
        loop.run_until_complete(self.async_worker_loop())

    async def async_worker_loop(self):
        import websockets
        import re
        active_connections = {}
        ide_flags = {}
        
        auto_scroll_js = """
        (function() {
            var panels = document.querySelectorAll('#conversation');
            for(var i=0; i<panels.length; i++){
                var el = panels[i];
                if(el.closest('.left-sidebar, aside, .sidebar, .part.sidebar')) continue;
                if(el.scrollHeight <= el.clientHeight + 80) continue;
                var cs = window.getComputedStyle(el);
                if(cs.overflowY !== 'auto' && cs.overflowY !== 'scroll') continue;
                var rect = el.getBoundingClientRect();
                if(rect.width < 150 || rect.height < 150) continue;
                var distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
                if(distFromBottom > 50){
                    el.scrollTop = el.scrollHeight;
                }
            }
        })()
        """

        while True:
            try:
                targets = await get_targets_async()
                pages = [t for t in targets 
                         if t.get('type') in ('page', 'iframe') 
                         and t.get('webSocketDebuggerUrl')
                         and ('Antigravity' in t.get('title', '') or 'antigravity-panel' in t.get('url', '') or '127.0.0.1' in t.get('url', ''))]
                self.pages_connected = len(pages)

                if not pages:
                    if self.active:
                        self.status_text = "Searching..."
                        self.status_color = "#f59e0b"
                        self.search_ticks += 1
                        if self.search_ticks == 25:
                            self.root.after(0, self.prompt_restart)
                    else:
                        self.status_text = "Inactive"
                        self.status_color = "#64748b"
                        
                    for ws in list(active_connections.values()):
                        await ws.close()
                    active_connections.clear()
                else:
                    self.search_ticks = 0
                    a_count = 0; w_count = 0; c_count = 0
                    max_cd = 0
                    max_tab_cd = 0

                    if self.active:
                        for p in pages:
                            ws_url = p.get('webSocketDebuggerUrl')
                            if not ws_url: continue
                            
                            if ws_url not in active_connections:
                                try:
                                    ws = await websockets.connect(ws_url, max_size=10_000_000, close_timeout=1)
                                    await ws.send(json.dumps({"id": 1, "method": "DOM.enable"}))
                                    await ws.send(json.dumps({"id": 2, "method": "Accessibility.enable"}))

                                    tracker_js = '''(function(){
                                        if(window._vc_tracker) return;
                                        window._vc_tracker = { type:0, scroll:0, click:0 };
                                        document.addEventListener('keydown', e => { if(e.isTrusted && (e.key.length===1||e.key==='Backspace'||e.key==='Enter')) window._vc_tracker.type = Date.now(); }, true);
                                        document.addEventListener('wheel', e => { if(e.isTrusted) window._vc_tracker.scroll = Date.now(); }, true);
                                        document.addEventListener('touchmove', e => { if(e.isTrusted) window._vc_tracker.scroll = Date.now(); }, true);
                                        document.addEventListener('mousedown', e => { if(e.isTrusted) window._vc_tracker.click = Date.now(); }, true);
                                    })()'''
                                    await ws.send(json.dumps({"id": 4, "method": "Runtime.evaluate", "params": {"expression": tracker_js}}))
                                    
                                    await ws.send(json.dumps({"id": 3, "method": "Runtime.evaluate", "params": {"expression": "!!document.querySelector('.monaco-workbench')", "returnByValue": True}}))
                                    is_ide = False
                                    while True:
                                        try:
                                            resp = await asyncio.wait_for(ws.recv(), timeout=1.0)
                                            data = json.loads(resp)
                                            if data.get("id") == 3:
                                                is_ide = data.get("result", {}).get("result", {}).get("value", False)
                                                break
                                        except:
                                            break
                                            
                                    ide_flags[ws_url] = is_ide
                                    active_connections[ws_url] = ws
                                except Exception:
                                    continue
                                    
                            ws = active_connections[ws_url]
                            
                            try:
                                # Tracker query
                                tracker_query = '''(function(){
                                    if(!window._vc_tracker) return {type:0, scroll:0, click:0};
                                    var n = Date.now();
                                    return {
                                        type: n - window._vc_tracker.type,
                                        scroll: n - window._vc_tracker.scroll,
                                        click: n - window._vc_tracker.click
                                    };
                                })()'''
                                await ws.send(json.dumps({"id": 98, "method": "Runtime.evaluate", "params": {"expression": tracker_query, "returnByValue": True}}))

                                # Auto scroll
                                if not self.scroll_paused:
                                    await ws.send(json.dumps({"id": 5, "method": "Runtime.evaluate", "params": {"expression": auto_scroll_js}}))

                                # Process agentic bridge queues
                                while not command_queue.empty():
                                    try:
                                        cmd = command_queue.get_nowait()
                                        if cmd['action'] == 'inject':
                                            js = INJECT_JS % json.dumps(cmd['prompt'])
                                            await ws.send(json.dumps({"id": 6, "method": "Runtime.evaluate", "params": {"expression": js}}))
                                        elif cmd['action'] == 'read_dom':
                                            await ws.send(json.dumps({"id": 7, "method": "Runtime.evaluate", "params": {"expression": READ_DOM_JS, "returnByValue": True}}))
                                    except Exception: break

                                # Get chat bounds to prevent clicking sidebar tasks
                                bounds_js = """(function(){
                                    var chat = document.querySelector('#conversation, .conversation, .chat-container, .chat, main, .antigravity-agent-side-panel');
                                    if(!chat) return {l: 0, r: window.innerWidth * 0.75, t: 0, b: window.innerHeight};
                                    var r = chat.getBoundingClientRect();
                                    return {l: r.left - 20, r: r.right + 20, t: r.top - 20, b: r.bottom + 20}; // adding 20px padding
                                })()"""
                                await ws.send(json.dumps({"id": 99, "method": "Runtime.evaluate", "params": {"expression": bounds_js, "returnByValue": True}}))
                                
                                if getattr(self, 'switcher_on', False) and getattr(self, 'tab_cooldown', 0) <= 0:
                                    blue_dot_js = """(function(){
                                        try {
                                            var els = document.querySelectorAll('*');
                                            var debug_log = "DEBUG: Found " + els.length + " els.\\n";
                                            var checked = 0;
                                            for(var i=0; i<els.length; i++) {
                                                var el = els[i];
                                                var cname = (el.className && typeof el.className === 'string') ? el.className.toLowerCase() : '';
                                                var aria = (el.getAttribute('aria-label') || '').toLowerCase();
                                                
                                                if (el.tagName && el.tagName.toLowerCase() === 'circle') {
                                                    var fill = window.getComputedStyle(el).fill || '';
                                                    var fmatch = fill.match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)/);
                                                    if (fmatch && parseInt(fmatch[3]) > 120 && parseInt(fmatch[3]) > parseInt(fmatch[1])*1.2) {
                                                        // It's a blue circle SVG
                                                    } else { continue; }
                                                } else {
                                                    var isBadge = false;
                                                    if (cname.includes('unread') || cname.includes('notification-badge') || aria.includes('unread') || aria.includes('attention')) {
                                                        isBadge = true;
                                                    }
                                                    if (!isBadge) {
                                                        var style = window.getComputedStyle(el);
                                                        var after = window.getComputedStyle(el, '::after');
                                                        var before = window.getComputedStyle(el, '::before');
                                                        var styles = [style, after, before];
                                                        for(var k=0; k<styles.length; k++) {
                                                            if (!styles[k]) continue;
                                                            var w = parseFloat(styles[k].width) || 0;
                                                            var h = parseFloat(styles[k].height) || 0;
                                                            if (w >= 4 && w <= 24 && h >= 4 && h <= 24) {
                                                                var bg = styles[k].backgroundColor || '';
                                                                var match = bg.match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)/);
                                                                if (match && parseInt(match[3]) > 120 && parseInt(match[3]) > parseInt(match[1])*1.2) {
                                                                    isBadge = true;
                                                                    break;
                                                                }
                                                            }
                                                        }
                                                    }
                                                    if (!isBadge) continue;
                                                }
                                                
                                                checked++;
                                                debug_log += "\\nFound candidate: " + el.tagName + " " + cname + " " + aria + "\\nHTML: " + el.outerHTML.substring(0, 200) + "\\n";
                                                
                                                // Avoid clicking the toggle button itself if it's the ⇄ button
                                                var parent = el.closest('button, a, [role="button"], [role="treeitem"], li, .monaco-list-row, .cursor-pointer');
                                                if (parent && parent.innerText && parent.innerText.includes('⇄')) continue;
                                                if (parent && parent.innerText && parent.innerText.includes('Antigravity')) continue; // Ignore chat header
                                                if (el.closest('.antigravity-chat-header')) continue;
                                                
                                                // Ignore chat history items (previous chats) which may have unread badges
                                                if (el.closest('[class*="history" i], [aria-label*="history" i], [class*="previous" i], [aria-label*="previous" i]')) continue;
                                                if (parent && parent.innerText && parent.innerText.match(/\\b\\d+[mdh]\\s*$/)) continue;
                                                
                                                var targetEl = el;
                                                if (parent) targetEl = parent;
                                                
                                                var rRect = targetEl.getBoundingClientRect();
                                                if (rRect.width === 0 || rRect.height === 0) continue;
                                                var x = rRect.left + rRect.width/2;
                                                var y = rRect.top + rRect.height/2;
                                                
                                                var down = new MouseEvent('mousedown', {bubbles: true, cancelable: true, clientX: x, clientY: y, view: window});
                                                var up = new MouseEvent('mouseup', {bubbles: true, cancelable: true, clientX: x, clientY: y, view: window});
                                                var click = new MouseEvent('click', {bubbles: true, cancelable: true, clientX: x, clientY: y, view: window});
                                                var pdown = new PointerEvent('pointerdown', {bubbles: true, cancelable: true, clientX: x, clientY: y, view: window});
                                                var pup = new PointerEvent('pointerup', {bubbles: true, cancelable: true, clientX: x, clientY: y, view: window});
                                                
                                                targetEl.dispatchEvent(pdown);
                                                targetEl.dispatchEvent(down);
                                                targetEl.dispatchEvent(pup);
                                                targetEl.dispatchEvent(up);
                                                targetEl.dispatchEvent(click);
                                                if(typeof targetEl.click === 'function') { targetEl.click(); }
                                                
                                                return {__clicked: targetEl.outerHTML, __dot: el.outerHTML};
                                            }
                                            return debug_log;
                                        } catch (e) {
                                            return "ERROR: " + e.message + "\\n" + e.stack;
                                        }
                                    })()"""
                                    await ws.send(json.dumps({"id": 100, "method": "Runtime.evaluate", "params": {"expression": blue_dot_js, "returnByValue": True}}))

                                agent_state_js = """(function(){
                                    var loaders = document.querySelectorAll('[aria-label="Loading"], .animate-dot-bounce');
                                    return loaders.length > 0;
                                })()"""
                                await ws.send(json.dumps({"id": 101, "method": "Runtime.evaluate", "params": {"expression": agent_state_js, "returnByValue": True}}))

                                # Get AX Tree
                                await ws.send(json.dumps({"id": 3, "method": "Accessibility.getFullAXTree"}))
                                nodes = []
                                chat_bounds = None
                                clicked_dot = False
                                is_agent_loading = False
                                while True:
                                    try:
                                        resp = await asyncio.wait_for(ws.recv(), timeout=2.0)
                                        data = json.loads(resp)
                                        
                                        if data.get("id") == 7:
                                            val = data.get('result',{}).get('result',{}).get('value','')
                                            for q_cmd in command_queue.queue:
                                                if q_cmd.get('action') == 'read_dom':
                                                    q_cmd['res_q'].put(val)
                                                    
                                        if data.get("id") == 99:
                                            chat_bounds = data.get("result", {}).get("result", {}).get("value")
                                            
                                        if data.get("id") == 100:
                                            res_val = data.get("result", {}).get("result", {}).get("value")
                                            if isinstance(res_val, dict) and "__dump" in res_val:
                                                import os
                                                with open(os.path.join(os.path.dirname(__file__), "sidebar_debug.html"), "w", encoding="utf-8") as f:
                                                    f.write(res_val["__dump"])
                                            elif isinstance(res_val, dict) and "__clicked" in res_val:
                                                import os
                                                with open(os.path.join(DEBUG_LOG_DIR, "clicked_debug.html"), "w", encoding="utf-8") as f:
                                                    f.write("CLICKED TARGET:\\n" + res_val["__clicked"] + "\\n\\nTHE DOT:\\n" + res_val["__dot"])
                                                clicked_dot = True
                                            elif isinstance(res_val, str) and (res_val.startswith("ERROR:") or res_val.startswith("DEBUG:")):
                                                import os
                                                with open(os.path.join(DEBUG_LOG_DIR, "clicked_debug.html"), "w", encoding="utf-8") as f:
                                                    f.write(res_val)
                                                clicked_dot = False
                                            else:
                                                clicked_dot = res_val
                                            
                                        if data.get("id") == 98:
                                            res_val = data.get("result", {}).get("result", {}).get("value")
                                            if res_val:
                                                t_left = max(0, self.typing_delay - res_val.get('type', 9999999)/1000.0)
                                                s_left = max(0, self.scroll_delay - res_val.get('scroll', 9999999)/1000.0)
                                                c_left = max(0, self.tab_delay - res_val.get('click', 9999999)/1000.0)
                                                user_wait = max(t_left, s_left, c_left)
                                                if user_wait > 0:
                                                    user_cooldown = user_wait * 1000
                                                    if user_cooldown > max_cd:
                                                        max_cd = user_cooldown
                                                
                                                tab_wait = max(t_left, c_left)
                                                if tab_wait > 0:
                                                    t_cd = tab_wait * 1000
                                                    if t_cd > max_tab_cd:
                                                        max_tab_cd = t_cd

                                        if data.get("id") == 101:
                                            res_val = data.get("result", {}).get("result", {}).get("value")
                                            if res_val:
                                                is_agent_loading = True

                                        if data.get("id") == 3:
                                            nodes = data.get("result", {}).get("nodes", [])
                                            break
                                    except asyncio.TimeoutError:
                                        break

                                dots = is_agent_loading
                                all_matched = 0
                                actionable = []
                                
                                blocklist = ['delete','remove','uninstall','format','reset','sign out','log out','close','discard','reject','deny','dismiss','erase','drop','run and debug','go back','go forward','more actions','always run','running','runner','run extension','run_cli','rescue run','rescue','allowlist','restart','reload','rules','mcp','feedback','star','scheduled tasks', '.md', '.py', '.js', '.json', '.html']
                                
                                if not hasattr(self, 'processed_nodes'):
                                    self.processed_nodes = set()
                                    
                                current_number = None
                                nodes_since_number = 0
                                    
                                for node in nodes:
                                    role = node.get("role", {}).get("value")
                                    name = node.get("name", {}).get("value", "")
                                    node_id = node.get("backendDOMNodeId")
                                    
                                    is_ide = ide_flags.get(ws_url, False)
                                    allowed_roles = ["StaticText", "button", "link", "ListMarker", "InlineTextBox", "radio", "option", "menuitem", "menuitemradio", "checkbox", "switch"] if is_ide else ["button", "link", "radio", "option", "menuitem", "menuitemradio", "checkbox", "switch"]
                                    if name and role in allowed_roles:
                                        name_lower = name.lower().strip()
                                        
                                        if name_lower in ['stop generating']:
                                            dots = True
                                        
                                        if any(b == name_lower for b in blocklist) or any(b in name_lower for b in ['.md', '.py', '.json', 'scheduled tasks', 'background tasks', 'voice', 'record', 'mic', 'audio', 'start', 'stop']):
                                            continue
                                            
                                        kw_match = None
                                        for (k_kw, _, _, _, ctx) in KEYWORDS:
                                            # Extra exact matching for subagent and return to avoid false positives
                                            if k_kw in ['needs attention', 'go back', 'switch project', 'switch workspace'] and name_lower != k_kw and not name_lower.endswith(k_kw):
                                                continue
                                                
                                            if k_kw in ['switch project', 'switch workspace'] and not getattr(self, 'switcher_on', False):
                                                continue
                                                
                                            if name_lower == k_kw or re.search(r'\b' + re.escape(k_kw) + r'\b', name_lower):
                                                if ctx == 'ide' and not is_ide:
                                                    continue
                                                kw_match = k_kw
                                                break
                                                
                                        if not kw_match and ('submit' in name_lower or 'send' in name_lower):
                                            kw_match = 'submit'
                                            
                                        if kw_match and (kw_match == 'submit' or self.enabled.get(kw_match, True)):
                                            if hasattr(self, 'processed_nodes') and node_id in self.processed_nodes:
                                                continue
                                                
                                            if kw_match == 'allow' and hasattr(self, 'pref_allow'):
                                                clean_name = re.sub(r'^[\[\]\(\)\s\d\.]+', '', name_lower).strip()
                                                if clean_name != 'allow' and self.pref_allow.lower() not in name_lower:
                                                    continue
                                                    
                                            actionable.append({"name": name, "id": node_id, "kw": kw_match})
                                            if kw_match != 'submit':
                                                all_matched += 1
                                                
                                # Add logic to prevent clicking "go back" unless we recently approved something
                                if not hasattr(self, 'return_pending'):
                                    self.return_pending = 0
                                    
                                has_actions = any(t['kw'] in ['allow', 'always allow', 'allow forever', 'approve', 'yes', 'ok'] for t in actionable)
                                if has_actions and not is_ide:
                                    self.return_pending = time.time()
                                    
                                # Remove 'go back' if we don't have a pending return (within last 10 seconds), or if there are still actions to take
                                if any(t['kw'] == 'go back' for t in actionable):
                                    if time.time() - self.return_pending > 10 or has_actions:
                                        actionable = [t for t in actionable if t['kw'] != 'go back']
                                    else:
                                        # Once we click 'go back', we clear the flag
                                        self.return_pending = 0

                                if dots:
                                    if hasattr(self, 'processed_nodes'):
                                        self.processed_nodes.clear()
                                        
                                if not actionable:
                                    # DO NOT reset total_clicks here, it ruins the stats
                                    if hasattr(self, 'processed_nodes'):
                                        self.processed_nodes.clear()

                                if actionable and getattr(self, 'cooldown', 0) <= 0:
                                    def rank(t):
                                        if t['kw'] == 'submit': return 1
                                        
                                        # Advanced allow preference handling for radio buttons
                                        if t['kw'] in ('allow', 'always allow', 'allow forever'):
                                            name = t['name'].lower()
                                            
                                            is_workspace = 'project' in name or 'workspace' in name
                                            is_every_time = ('always' in name or 'forever' in name or 'permanently' in name) and not is_workspace
                                            is_once = not is_workspace and not is_every_time
                                            
                                            pref = getattr(self, 'pref_allow', '').lower()
                                            
                                            if pref == 'allow in workspace' and is_workspace: return 2
                                            if pref == 'allow every time' and is_every_time: return 2
                                            if pref == 'allow once' and is_once: return 2
                                            
                                            return -1
                                            
                                        if t['kw'] == 'accept all': return 2
                                        return 0
                                    actionable.sort(key=rank, reverse=True)
                                    for target in actionable:
                                        if hasattr(self, 'processed_nodes'):
                                            if target["id"] in self.processed_nodes:
                                                continue
                                            self.processed_nodes.add(target["id"])
                                        await ws.send(json.dumps({
                                            "id": 104,
                                            "method": "DOM.resolveNode",
                                            "params": {"backendNodeId": target["id"]}
                                        }))
                                        
                                        obj_id = None
                                        while True:
                                            try:
                                                res_resp = await asyncio.wait_for(ws.recv(), timeout=1.0)
                                                res_data = json.loads(res_resp)
                                                if res_data.get("id") == 104:
                                                    obj_id = res_data.get("result", {}).get("object", {}).get("objectId")
                                                    break
                                            except asyncio.TimeoutError:
                                                break
                                                
                                        if not obj_id:
                                            continue
                                            
                                        js_click = """function() {
                                            var node = this.nodeType === 3 ? this.parentElement : this;
                                            var isSubagent = ('__KW__' === 'needs attention');
                                            var isSubmit = ('__KW__' === 'submit');
                                            if (!node.getBoundingClientRect) return {s: "hidden"};
                                            var r = node.getBoundingClientRect();
                                            if (r.width === 0 && r.height === 0) return {s: "hidden"};
                                            if (!isSubagent && node.closest('.left-sidebar, aside, .sidebar, .part.sidebar')) return {s: "sidebar"};
                                            if (!isSubagent && !isSubmit && node.closest('.chat-input, .composer, .input-area, .bottom-bar, [data-testid="composer"], form')) return {s: "composer"};
                                            node.scrollIntoView({block: 'center'});
                                            var r2 = node.getBoundingClientRect();
                                            var x = r2.left + r2.width/2;
                                            var y = r2.top + r2.height/2;
                                            
                                            // Dispatch full mouse event sequence for React/custom UI elements
                                            var down = new MouseEvent('mousedown', {bubbles: true, cancelable: true, clientX: x, clientY: y, view: window});
                                            var up = new MouseEvent('mouseup', {bubbles: true, cancelable: true, clientX: x, clientY: y, view: window});
                                            var click = new MouseEvent('click', {bubbles: true, cancelable: true, clientX: x, clientY: y, view: window});
                                            
                                            node.dispatchEvent(down);
                                            node.dispatchEvent(up);
                                            node.dispatchEvent(click);
                                            if(typeof node.click === 'function') { node.click(); }
                                            
                                            return {s: "clicked", x: x, y: y};
                                        }""".replace('__KW__', target['kw'])
                                        
                                        await ws.send(json.dumps({
                                            "id": 105,
                                            "method": "Runtime.callFunctionOn",
                                            "params": {
                                                "objectId": obj_id,
                                                "functionDeclaration": js_click,
                                                "returnByValue": True
                                            }
                                        }))
                                        
                                        click_res = None
                                        while True:
                                            try:
                                                click_resp = await asyncio.wait_for(ws.recv(), timeout=1.0)
                                                click_data = json.loads(click_resp)
                                                if click_data.get("id") == 105:
                                                    click_res = click_data.get("result", {}).get("result", {}).get("value")
                                                    
                                                    if target['kw'] in COUNTABLE_ACTIONS:
                                                        self.total_clicks += 1
                                                        if self.cb_clicks > 0 and self.total_clicks >= self.cb_clicks:
                                                            # Tripped circuit breaker
                                                            self.total_clicks = 0
                                                            
                                                            def trigger_cb():
                                                                self.toggle_play(force=False)
                                                                if self.tray_icon and getattr(self, 'play_active', False):
                                                                    self.tray_icon.notify(f"Paused after {self.cb_clicks} clicks", "VegaClick Auto-Pause")
                                                                    self.icon_state = "paused"
                                                                    self.update_tray_icon()
                                                            self.master.after(0, trigger_cb)
                                                            debug_log(f"Circuit breaker tripped after {self.cb_clicks} clicks")
                                                    
                                                    is_submit = (target['kw'] == 'submit')
                                                    if is_submit:
                                                        self.master.after(0, lambda: self.master.event_generate("<<ClickSubmit>>"))
                                                        
                                                    break
                                            except Exception as e:
                                                break
                                                
                                        if not click_res or click_res.get("s") != "clicked":
                                            self.last_msg = f"Ignored {target['kw']} ({click_res.get('s') if click_res else 'timeout'})"
                                            self.root.after(0, lambda msg=self.last_msg: self.add_log(msg))
                                            continue
                                            
                                        x, y = click_res.get("x", 0), click_res.get("y", 0)
                                        
                                        # Removed Input.dispatchMouseEvent to prevent clicking sticky footers over the target
                                        
                                        self.last_msg = f"Clicked {target['kw']} ({target['name'][:15]})"
                                        
                                        # Circuit breaker log
                                        if target['kw'] == 'retry':
                                            cbWindow = self.cb_seconds * 1000
                                            now = time.time() * 1000
                                            if not hasattr(self, '_vcClickLog'): self._vcClickLog = []
                                            self._vcClickLog = [cx for cx in self._vcClickLog if now - cx['t'] < cbWindow]
                                            self._vcClickLog.append({'k': 'retry', 't': now})
                                            if len(self._vcClickLog) >= self.cb_clicks:
                                                self.last_msg = "[CIRCUIT BREAKER] Loop detected on retry"
                                                self._vcClickLog = []
                                                self.root.after(0, self.toggle_play)
                                                self.status_text = "PAUSED (Loop Limit)"
                                                self.status_color = "#ef4444"
                                                
                                        self.root.after(0, self.flash_click)
                                        self.root.after(0, lambda msg=self.last_msg: self.add_log(msg))
                                        
                                        if self.overlay_on:
                                            ripple_js = f"""
                                            (function(){{
                                                var dot = document.createElement('div');
                                                dot.style.cssText = 'position:fixed;pointer-events:none;z-index:999999;border-radius:50%;left:{x-16}px;top:{y-16}px;width:32px;height:32px;border:3px solid rgba(168,85,247,0.9);background:rgba(168,85,247,0.3);transition:transform 0.5s ease-out, opacity 0.5s ease-out;transform:scale(0.5);opacity:1';
                                                document.body.appendChild(dot);
                                                requestAnimationFrame(function() {{ dot.style.transform = 'scale(2.5)'; dot.style.opacity = '0'; }});
                                                setTimeout(function() {{ dot.remove() }}, 600);
                                            }})()
                                            """
                                            await ws.send(json.dumps({"id": 5, "method": "Runtime.evaluate", "params": {"expression": ripple_js}}))
                                        
                                        break
                                                
                                    max_cd = self.click_delay
                                    await asyncio.sleep(self.click_delay / 1000.0)

                                if dots: a_count += 1
                                elif all_matched > 0: w_count += 1
                                else: c_count += 1
                                
                            except websockets.exceptions.ConnectionClosed:
                                del active_connections[ws_url]
                            except Exception:
                                pass

                        self.cooldown = max_cd
                        self.tab_cooldown = max_tab_cd
                        self._pages_total = len(pages)
                        self._page_states = (a_count, w_count, c_count)
                        
                        if a_count > 0 or w_count > 0:
                            self._last_busy_time = time.time()
                            self._idle_alerted = False
                        elif self.idle_alert_minutes > 0 and not self._idle_alerted:
                            idle_seconds = time.time() - self._last_busy_time
                            if idle_seconds >= self.idle_alert_minutes * 60:
                                self._idle_alerted = True
                                self.root.after(0, self._play_idle_alert)
                                self.root.after(0, lambda: self.add_log(f'Idle alert - agent idle for {self.idle_alert_minutes}min'))
                        self.status_text = 'states'
                    else:
                        self.status_text = "Inactive"
                        self.status_color = "#64748b"
            except Exception:
                pass
                
            await asyncio.sleep(POLL_INTERVAL)

    def run(self):
        debug_log("Entering Tk mainloop")
        try:
            self.root.mainloop()
        finally:
            debug_log("Tk mainloop exited — force killing")
            # Tk mainloop exited (crash or close) — force-kill to stop worker thread
            os._exit(0)

if __name__ == "__main__":
    debug_log("="*60)
    debug_log(f"VegaClick {VERSION} starting — PID={os.getpid()} Python={sys.version.split()[0]} Platform={sys.platform}")
    debug_log(f"Script path: {os.path.abspath(__file__)}")
    debug_log(f"Log file: {DEBUG_LOG_FILE}")
    debug_log("="*60)

    killed = cleanup_old_processes()
    if killed:
        debug_log(f"Cleaned up {killed} old process(es)")
        print(f"Cleaned up {killed} old VegaClick process(es)")

    import socket
    try:
        _vc_lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _vc_lock_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        _vc_lock_socket.bind(('127.0.0.1', 9842))
        debug_log("Socket lock acquired on 127.0.0.1:9842")
    except socket.error:
        debug_log("Socket lock FAILED — another instance is running. Exiting.")
        print("VegaClick is already running! Exiting.")
        sys.exit(0)

    def __global_exception_handler(exctype, value, tb):
        import traceback
        import os
        tb_str = ''.join(traceback.format_exception(exctype, value, tb))
        debug_log(f"FATAL UNHANDLED EXCEPTION:\n{tb_str}")
        log_path = os.path.join(os.environ.get('TEMP', 'c:/tmp'), 'vegaclick_fatal.log')
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(tb_str + "\n")
    sys.excepthook = __global_exception_handler

    debug_log("Constructing VegaClickApp...")
    VegaClickApp().run()
