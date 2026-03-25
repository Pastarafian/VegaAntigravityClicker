"""
VegaClick v16 — Deep Scanner + Fast Clicker Architecture
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

PORT = 9222
POLL_INTERVAL = 0.8
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'settings.json')

KEYWORDS = [
    ('accept all', 'Accept All', '#22c55e', 'Accept all pending code changes'),
    ('allow', 'Allow', '#22c55e', 'Allow tool access for this conversation'),
    ('trust', 'Trust', '#22c55e', 'Trust a workspace or extension'),
    ('approve', 'Approve', '#6366f1', 'Approve a pending action'),
    ('continue', 'Continue', '#22c55e', 'Continue the current operation'),
    ('run', 'Run', '#3b82f6', 'Run a terminal command'),
    ('retry', 'Retry', '#f59e0b', 'Retry a failed operation'),
    ('ok', 'OK', '#64748b', 'Confirm a dialog prompt'),
    ('yes', 'Yes', '#64748b', 'Answer yes to a confirmation'),
    ('apply', 'Apply', '#64748b', 'Apply settings or changes'),
    ('relocate', 'Relocate', '#64748b', 'Relocate a file or resource'),
    ('changes overview', 'Overview', '#a78bfa', 'Open the Changes Overview panel'),
]

PRESETS = {
    'All': {kw: True for kw, _, _, _ in KEYWORDS},
    'Safe': {
        'accept all': True, 'allow': True, 'trust': True,
        'approve': False, 'continue': True, 'run': False,
        'retry': True, 'ok': False, 'yes': False,
        'apply': False, 'relocate': False, 'changes overview': True,
    },
    'Minimal': {
        'accept all': True, 'allow': True, 'trust': False,
        'approve': False, 'continue': False, 'run': False,
        'retry': False, 'ok': False, 'yes': False,
        'apply': False, 'relocate': False, 'changes overview': False,
    },
    'None': {kw: False for kw, _, _, _ in KEYWORDS},
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
        x = self.widget.winfo_rootx() + self.widget.winfo_width() // 2
        y = self.widget.winfo_rooty() - 24
        self.tip = tk.Toplevel(self.widget)
        self.tip.overrideredirect(True)
        self.tip.attributes('-topmost', True)
        lbl = tk.Label(self.tip, text=self.text, font=('Segoe UI', 8),
                       fg='#e6edf3', bg='#1c2128', padx=6, pady=2, relief='solid', bd=1)
        lbl.pack()
        self.tip.update_idletasks()
        tw = self.tip.winfo_reqwidth()
        self.tip.geometry(f'+{x - tw // 2}+{y}')
    def hide(self, e=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None

def load_settings():
    try:
        with open(SETTINGS_FILE, 'r') as f:
            data = json.load(f)
            saved = data.get('enabled', {})
            enabled = {kw: saved.get(kw, True) for kw, _, _, _ in KEYWORDS}
            scan_delay = data.get('scan_delay', 100)
            click_delay = data.get('click_delay', 150)
            preset = data.get('preset', 'All')
            typing_delay = data.get('typing_delay', 5)
            scroll_delay = data.get('scroll_delay', 15)
            return enabled, scan_delay, click_delay, preset, typing_delay, scroll_delay
    except:
        return {kw: True for kw, _, _, _ in KEYWORDS}, 100, 150, 'All', 5, 15

def save_settings(enabled, scan_delay=100, click_delay=150, preset='All', typing_delay=5, scroll_delay=15):
    try:
        with open(SETTINGS_FILE, 'w') as f:
            json.dump({'enabled': enabled, 'scan_delay': scan_delay, 'click_delay': click_delay,
                       'preset': preset, 'typing_delay': typing_delay, 'scroll_delay': scroll_delay}, f, indent=2)
    except:
        pass

command_queue = queue.Queue()

# ═══════════════════════════════════════════════════════════════
# Agentic Bridge — inject prompts into IDE chat
# ═══════════════════════════════════════════════════════════════

INJECT_JS = """
(function() {
    var text = %s;
    var box = document.querySelector('textarea, [contenteditable="true"]') || document.querySelector('input[type="text"]');
    if (!box) return "No input box found";
    if (box.tagName === 'TEXTAREA' || box.tagName === 'INPUT') {
        box.value = text;
        box.dispatchEvent(new Event('input', {bubbles: true}));
    } else {
        box.innerText = text;
        box.dispatchEvent(new Event('input', {bubbles: true}));
    }
    var btn = document.querySelector('button[type="submit"]') || (box.parentElement && box.parentElement.querySelector('button'));
    if (btn) btn.click();
    else box.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true}));
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
                    command_queue.put({'action': 'inject', 'prompt': data.get('prompt', '')})
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(b'{"status": "queued"}')
                else:
                    self.send_response(404); self.end_headers()
            except:
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
                self.wfile.write(b'{"status": "ok", "version": "v16"}')
    try:
        HTTPServer(('127.0.0.1', 4242), Handler).serve_forever()
    except:
        pass

# ═══════════════════════════════════════════════════════════════
# Process Cleanup
# ═══════════════════════════════════════════════════════════════
def cleanup_old_processes():
    my_pid = os.getpid()
    kill_patterns = ['ide-autoclicker', 'vegaclaw', 'vegaclick', 'autoclicker']
    try:
        if sys.platform == 'win32':
            result = subprocess.run(
                ['wmic', 'process', 'where', "name='pythonw.exe' or name='python.exe'",
                 'get', 'ProcessId,CommandLine', '/format:list'],
                capture_output=True, text=True, timeout=5
            )
            current_pid = None; current_cmd = None
            for line in result.stdout.split('\n'):
                line = line.strip()
                if line.startswith('CommandLine='): current_cmd = line[12:].lower()
                elif line.startswith('ProcessId='):
                    current_pid = int(line[10:])
                    if current_pid != my_pid and current_cmd and any(p in current_cmd for p in kill_patterns):
                        try: subprocess.run(['taskkill', '/PID', str(current_pid), '/F'], capture_output=True, timeout=3)
                        except: pass
                    current_pid = None; current_cmd = None
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
                        except: pass
    except: pass

# ═══════════════════════════════════════════════════════════════
# VegaClick v16 — DEEP SCANNER + FAST CLICKER JS
# ═══════════════════════════════════════════════════════════════
import os
_scanner_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scanner.js')
with open(_scanner_path, 'r', encoding='utf-8') as _f:
    FINDER_JS = _f.read()

# ═══════════════════════════════════════════════════════════════
# CDP Helpers
# ═══════════════════════════════════════════════════════════════

async def get_targets_async():
    all_targets = []
    async def probe(port):
        try:
            loop = asyncio.get_event_loop()
            data = await asyncio.wait_for(
                loop.run_in_executor(None, lambda: urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=0.3).read()),
                timeout=0.5)
            return json.loads(data)
        except: return []
    results = await asyncio.gather(*[probe(p) for p in range(9222, 9242)], return_exceptions=True)
    for r in results:
        if isinstance(r, list): all_targets.extend(r)
    return all_targets

async def _cdp_eval(ws_url, js_code):
    try:
        async with websockets.connect(ws_url, close_timeout=1) as ws:
            await ws.send(json.dumps({"id":1,"method":"Runtime.evaluate","params":{"expression":js_code,"returnByValue":True}}))
            return json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
    except: return None

# ═══════════════════════════════════════════════════════════════
# UI Pill
# ═══════════════════════════════════════════════════════════════

class VegaClickApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("VegaClick v16")
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
        self.enabled, self.scan_delay, self.click_delay, self.preset, self.typing_delay, self.scroll_delay = load_settings()
        self.drawer = None
        self.toggle_labels = {}
        self.log_entries = []
        self.log_window = None
        self.preset_popup = None

        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"400x30+{sw - 430}+{sh - 70}")

        tk.Label(self.root, text="VegaClick", font=("Segoe UI", 9, "bold"), fg='#00d4ff', bg='#0e1117').pack(side='left', padx=(8,4), pady=4)
        self.settings_btn = tk.Label(self.root, text="⚙", font=("Segoe UI", 9), fg='white', bg='#1c2128', cursor='hand2', width=2)
        self.settings_btn.pack(side='left', padx=(0,8), ipady=1, ipadx=2, pady=4)
        self.settings_btn.bind('<Button-1>', lambda e: self.toggle_settings())

        self.ui_status = tk.Label(self.root, text="...", font=("Consolas", 9), fg=self.status_color, bg='#0e1117', width=16, anchor='w')
        self.ui_status.pack(side='left', padx=(0,4), pady=4)

        self.ui_count = tk.Label(self.root, text="0 clicks", font=("Consolas", 9), fg='#64748b', bg='#0e1117', width=11, anchor='w')
        self.ui_count.pack(side='left', padx=(0,4), pady=4)

        close_btn = tk.Label(self.root, text="✕", font=("Segoe UI", 10), bg='#1c2128', fg='#64748b',
                             width=2, anchor='center', bd=0, relief='flat', padx=4, pady=2)
        close_btn.pack(side='right', padx=(2, 8), pady=4)
        close_btn.bind('<Button-1>', lambda e: self.on_close())

        self.overlay_on = True
        self.overlay_btn = tk.Label(self.root, text="◎", font=("Segoe UI", 10), bg='#2d333b', fg='#a78bfa',
                                    width=2, anchor='center', bd=0, relief='flat', padx=4, pady=2)
        self.overlay_btn.pack(side='right', padx=2, pady=4)
        self.overlay_btn.bind('<Button-1>', lambda e: self.toggle_overlay())

        self.play_btn = tk.Label(self.root, text='⏸', font=("Segoe UI", 10), bg='#1c2128', fg='#f59e0b',
                                 width=2, anchor='center', bd=0, relief='flat', padx=4, pady=2)
        self.play_btn.pack(side='right', padx=2, pady=4)
        self.play_btn.bind('<Button-1>', lambda e: self.toggle_play())

        self.root.bind('<Button-1>', self._start_drag)
        self.root.bind('<B1-Motion>', self._on_drag)

        self.thread = threading.Thread(target=self.worker_loop, daemon=True)
        self.thread.start()
        self.refresh_ui()

    def _start_drag(self, e):
        if e.widget in (self.settings_btn, self.play_btn, self.overlay_btn):
            return
        self._dx, self._dy = e.x, e.y
        self.close_drawer()
    def _on_drag(self, e):
        if e.widget in (self.settings_btn, self.play_btn, self.overlay_btn):
            return
        self.root.geometry(f"+{self.root.winfo_x()+(e.x-self._dx)}+{self.root.winfo_y()+(e.y-self._dy)}")

    def on_close(self):
        self.close_drawer()
        self.root.withdraw()
        self.active = False
        def _force_exit():
            import os
            self.root.destroy()
            os._exit(0)
        self.root.after(1200, _force_exit)

    def prompt_restart(self):
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
            if sys.platform == 'win32':
                # Windows: use PowerShell/CIM to find the exe path
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
                subprocess.Popen([exe_path, "--remote-debugging-port=9222"], shell=True)
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
        self.overlay_btn.configure(bg='#2d333b' if self.overlay_on else '#1c2128', fg='#a78bfa' if self.overlay_on else '#64748b')

    def toggle_play(self):
        self.active = not getattr(self, 'active', True)
        if self.active:
            self.play_btn.configure(bg='#2d333b', text='▶', fg='#22c55e', font=('Segoe UI', 10))
        else:
            self.play_btn.configure(bg='#1c2128', text='⏸', fg='#f59e0b', font=('Segoe UI', 10))

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
        d.overrideredirect(True)
        d.attributes('-topmost', True)
        d.attributes('-alpha', 0.95)
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
        for idx, (kw, display, color, desc) in enumerate(KEYWORDS):
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

        # Row 4: Logs + Typing + Scroll
        logs_btn = tk.Label(grid, text="Logs", font=("Segoe UI", 8, "bold"),
                            fg='#a78bfa', bg='#2d333b', cursor='hand2',
                            width=12, relief='flat', padx=4, pady=3)
        logs_btn.grid(row=4, column=0, padx=3, pady=2, sticky='ew')
        logs_btn.bind('<Button-1>', lambda e: self.open_log_window())
        Tooltip(logs_btn, 'Open click history log window')

        typing_cell = tk.Frame(grid, bg='#1c2128')
        typing_cell.grid(row=4, column=1, padx=3, pady=2, sticky='ew')
        tk.Label(typing_cell, text="Typing:", font=("Segoe UI", 8, "bold"),
                 fg='#00d4ff', bg='#1c2128').pack(side='left', padx=(4,2))
        self.typing_entry = tk.Entry(typing_cell, width=3, font=("Segoe UI", 8, "bold"),
                                    bg='#1c2128', fg='#e6edf3', insertbackground='#e6edf3',
                                    relief='flat', bd=0, highlightthickness=0)
        self.typing_entry.insert(0, str(self.typing_delay))
        self.typing_entry.pack(side='left')
        tk.Label(typing_cell, text="s", font=("Consolas", 8),
                 fg='#64748b', bg='#1c2128').pack(side='left', padx=(0,4))

        scroll_cell = tk.Frame(grid, bg='#1c2128')
        scroll_cell.grid(row=4, column=2, padx=3, pady=2, sticky='ew')
        tk.Label(scroll_cell, text="Scroll:", font=("Segoe UI", 8, "bold"),
                 fg='#00d4ff', bg='#1c2128').pack(side='left', padx=(4,2))
        self.scroll_entry = tk.Entry(scroll_cell, width=3, font=("Segoe UI", 8, "bold"),
                                    bg='#1c2128', fg='#e6edf3', insertbackground='#e6edf3',
                                    relief='flat', bd=0, highlightthickness=0)
        self.scroll_entry.insert(0, str(self.scroll_delay))
        self.scroll_entry.pack(side='left')
        tk.Label(scroll_cell, text="s", font=("Consolas", 8),
                 fg='#64748b', bg='#1c2128').pack(side='left', padx=(0,4))

        self.typing_entry.bind('<KeyRelease>', lambda e: self._save_delays())
        self.scroll_entry.bind('<KeyRelease>', lambda e: self._save_delays())

        # Row 5: Reset + Scan delay + Click delay
        reset_btn = tk.Label(grid, text="Reset Clicks", font=("Segoe UI", 8, "bold"),
                             fg='#f59e0b', bg='#1c2128', cursor='hand2',
                             width=12, relief='flat', padx=4, pady=3)
        reset_btn.grid(row=5, column=0, padx=3, pady=2, sticky='ew')
        reset_btn.bind('<Button-1>', lambda e: self.reset_clicks())
        Tooltip(reset_btn, 'Reset the click counter to 0')

        scan_cell = tk.Frame(grid, bg='#1c2128')
        scan_cell.grid(row=5, column=1, padx=3, pady=2, sticky='ew')
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
        click_cell.grid(row=5, column=2, padx=3, pady=2, sticky='ew')
        tk.Label(click_cell, text="Click:", font=("Segoe UI", 8, "bold"),
                 fg='#00d4ff', bg='#1c2128').pack(side='left', padx=(4,2))
        self.click_entry = tk.Entry(click_cell, width=4, font=("Segoe UI", 8, "bold"),
                                   bg='#1c2128', fg='#e6edf3', insertbackground='#e6edf3',
                                   relief='flat', bd=0, highlightthickness=0)
        self.click_entry.insert(0, str(self.click_delay))
        self.click_entry.pack(side='left')
        tk.Label(click_cell, text="ms", font=("Consolas", 8),
                 fg='#64748b', bg='#1c2128').pack(side='left', padx=(0,4))

        self.scan_entry.bind('<KeyRelease>', lambda e: self._save_delays())
        self.click_entry.bind('<KeyRelease>', lambda e: self._save_delays())

        # Position above pill
        d.update_idletasks()
        drawer_h = d.winfo_reqheight()
        pill_x = self.root.winfo_x()
        pill_y = self.root.winfo_y()
        d.geometry(f"400x{drawer_h}+{pill_x}+{pill_y - drawer_h - 2}")

        self.drawer = d

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
        except ValueError:
            pass
        try:
            self.scroll_delay = max(0, int(self.scroll_entry.get()))
        except ValueError:
            pass
        save_settings(self.enabled, self.scan_delay, self.click_delay, self.preset, self.typing_delay, self.scroll_delay)

    def click_toggle(self, kw):
        self.enabled[kw] = not self.enabled.get(kw, True)
        save_settings(self.enabled, self.scan_delay, self.click_delay, self.preset, self.typing_delay, self.scroll_delay)
        if kw in self.toggle_labels:
            lbl, color = self.toggle_labels[kw]
            on = self.enabled[kw]
            lbl.configure(fg='#00d4ff' if on else '#64748b',
                          bg='#2d333b' if on else '#1c2128')

    def flash_click(self):
        self.ui_count.configure(bg='#2d333b')
        self.root.after(100, lambda: self.ui_count.configure(bg='#0e1117'))

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
        self.preset = name
        preset_toggles = PRESETS[name]
        for kw in self.enabled:
            self.enabled[kw] = preset_toggles.get(kw, True)
        save_settings(self.enabled, self.scan_delay, self.click_delay, self.preset, self.typing_delay, self.scroll_delay)
        for kw, (lbl, color) in self.toggle_labels.items():
            on = self.enabled[kw]
            lbl.configure(fg='#00d4ff' if on else '#64748b',
                          bg='#2d333b' if on else '#1c2128')
        if hasattr(self, 'preset_btn'):
            self.preset_btn.configure(text=f'\u25be {name}')
        self.close_preset_popup()

    def refresh_ui(self):
        if not self.active: st, sc = "Inactive", "#64748b"
        else: st, sc = self.status_text, self.status_color
        self.ui_status.configure(text=st, fg=sc)
        if hasattr(self, 'cooldown') and self.cooldown > 0 and self.active:
            self.ui_count.configure(text=f"{self.cooldown/1000.0:.1f}s wait", fg="#f59e0b")
        else:
            self.ui_count.configure(text=f"{self.total_clicks} clicks", fg="#64748b")
        self.root.after(200, self.refresh_ui)

    def worker_loop(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        threading.Thread(target=start_agentic_bridge, daemon=True).start()

        while True:
            try:
                targets = loop.run_until_complete(get_targets_async())
                
                # WHITELIST: Only inject into Antigravity chat pages
                # Skip iframes (sidebar with Rules/MCP/Allowlist), Launchpad, etc.
                pages = [t for t in targets 
                         if t.get('type') == 'page' 
                         and t.get('webSocketDebuggerUrl')
                         and 'Antigravity' in t.get('title', '')]
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
                else:
                    self.search_ticks = 0
                    if self.active:
                        max_cd = 0
                        for p in pages:
                            ws = p.get('webSocketDebuggerUrl')
                            if not ws: continue
                            try:
                                # Force-clear stale heartbeat so new code always injects
                                loop.run_until_complete(_cdp_eval(ws, "if(window.__vc && Date.now()-window.__vchb>8000){window.__vc=false}"))

                                if getattr(self, '_pending_reset', False):
                                    loop.run_until_complete(_cdp_eval(ws, "window.__vcc=0"))
                                    self._pending_reset = False
                                ov_js = f"window.__vcoverlay={'true' if self.overlay_on else 'false'}"
                                loop.run_until_complete(_cdp_eval(ws, ov_js))

                                en_js = f"window.__vcEnabled={json.dumps(self.enabled)}"
                                loop.run_until_complete(_cdp_eval(ws, en_js))

                                delay_js = f"window.__vcScanDelay={self.scan_delay};window.__vcClickDelay={self.click_delay};window.__vcTypingDelay={self.typing_delay * 1000};window.__vcScrollDelay={self.scroll_delay * 1000}"
                                loop.run_until_complete(_cdp_eval(ws, delay_js))

                                res = loop.run_until_complete(_cdp_eval(ws, FINDER_JS))
                                if res:
                                    val = res.get('result',{}).get('result',{}).get('value','{}')
                                    try: status = json.loads(val)
                                    except: status = {}
                                    if isinstance(status, dict):
                                        c = status.get('c', 0)
                                        m = status.get('m', '')
                                        inv = status.get('inv', 0)
                                        cd = status.get('cd', 0)
                                        max_cd = max(max_cd, cd)
                                        if c > 0: self.total_clicks = max(self.total_clicks, c)
                                        if m and m != self.last_msg:
                                            self.last_msg = m
                                            print(f"[CLICK] {m}")
                                            self.root.after(0, self.flash_click)
                                            self.root.after(0, lambda msg=m: self.add_log(msg))
                                        self.scan_targets = inv
                                        self.status_text = f"Active ({len(pages)}p) {inv}t"
                                        self.status_color = "#22c55e"
                            except: pass

                            while not command_queue.empty():
                                try:
                                    cmd = command_queue.get_nowait()
                                    if cmd['action'] == 'inject':
                                        js = INJECT_JS % json.dumps(cmd['prompt'])
                                        loop.run_until_complete(_cdp_eval(ws, js))
                                    elif cmd['action'] == 'read_dom':
                                        res = loop.run_until_complete(_cdp_eval(ws, READ_DOM_JS))
                                        val = res.get('result',{}).get('result',{}).get('value','') if res else ''
                                        cmd['res_q'].put(val)
                                except queue.Empty: break
                        self.cooldown = max_cd
                    else:
                        self.status_text = "Inactive"
                        self.status_color = "#64748b"
                        for p in pages:
                            ws = p.get('webSocketDebuggerUrl')
                            if not ws: continue
                            try:
                                disable_js = "if(window.__vcObs)try{window.__vcObs.disconnect()}catch(e){};if(window.__vcInt)clearInterval(window.__vcInt);if(window.__vcScanInt)clearInterval(window.__vcScanInt);window.__vc=false;"
                                loop.run_until_complete(_cdp_eval(ws, disable_js))
                            except: pass

                            # Still process command queue when inactive
                            while not command_queue.empty():
                                try:
                                    cmd = command_queue.get_nowait()
                                    if cmd['action'] == 'inject':
                                        js = INJECT_JS % json.dumps(cmd['prompt'])
                                        loop.run_until_complete(_cdp_eval(ws, js))
                                    elif cmd['action'] == 'read_dom':
                                        res = loop.run_until_complete(_cdp_eval(ws, READ_DOM_JS))
                                        val = res.get('result',{}).get('result',{}).get('value','') if res else ''
                                        cmd['res_q'].put(val)
                                except queue.Empty: break
            except: pass
            time.sleep(POLL_INTERVAL)

    def run(self):
        self.root.mainloop()

if __name__ == "__main__":
    cleanup_old_processes()
    VegaClickApp().run()
