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

import socket
try:
    _vc_lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _vc_lock_socket.bind(('127.0.0.1', 9842))
except socket.error:
    print("VegaClick is already running! Exiting.")
    sys.exit(0)


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
    ('send all', 'Send All', '#3b82f6', 'Send all pending prompts after task cancel'),
    ('changes overview', 'Overview', '#a78bfa', 'Open the Changes Overview panel'),
]

PRESETS = {
    'All': {kw: True for kw, _, _, _ in KEYWORDS},
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
            cb_clicks = data.get('cb_clicks', 3)
            cb_seconds = data.get('cb_seconds', 20)
            pill_x = data.get('pill_x', None)
            pill_y = data.get('pill_y', None)
            idle_alert_minutes = data.get('idle_alert_minutes', 5)
            auto_start = data.get('auto_start', False)
            return enabled, scan_delay, click_delay, preset, typing_delay, scroll_delay, cb_clicks, cb_seconds, pill_x, pill_y, idle_alert_minutes, auto_start
    except:
        return {kw: True for kw, _, _, _ in KEYWORDS}, 100, 150, 'All', 5, 15, 3, 20, None, None, 5, False

def save_settings(enabled, scan_delay=100, click_delay=150, preset='All', typing_delay=5, scroll_delay=15,
                  cb_clicks=3, cb_seconds=20, pill_x=None, pill_y=None, idle_alert_minutes=5, auto_start=False):
    try:
        data = {
            'enabled': enabled, 'scan_delay': scan_delay, 'click_delay': click_delay,
            'preset': preset, 'typing_delay': typing_delay, 'scroll_delay': scroll_delay,
            'cb_clicks': cb_clicks, 'cb_seconds': cb_seconds,
            'idle_alert_minutes': idle_alert_minutes, 'auto_start': auto_start,
        }
        if pill_x is not None:
            data['pill_x'] = pill_x
        if pill_y is not None:
            data['pill_y'] = pill_y
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(data, f, indent=2)
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
        self.enabled, self.scan_delay, self.click_delay, self.preset, self.typing_delay, self.scroll_delay, self.cb_clicks, self.cb_seconds, self.pill_x, self.pill_y, self.idle_alert_minutes, self.auto_start = load_settings()
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

        self.ui_status = tk.Text(self.root, height=1, width=22, font=("Consolas", 9),
                                  bg='#0e1117', relief='flat', bd=0,
                                  highlightthickness=0, state='disabled',
                                  cursor='arrow', takefocus=0)
        self.ui_status.pack(side='left', padx=(0,4), pady=4)
        self.ui_status.tag_configure('green', foreground='#22c55e')
        self.ui_status.tag_configure('amber', foreground='#f59e0b')
        self.ui_status.tag_configure('blue', foreground='#00d4ff')
        self.ui_status.tag_configure('gray', foreground='#64748b')
        self.ui_status.tag_configure('red', foreground='#ef4444')
        self.ui_status.tag_configure('default', foreground='#e6edf3')
        
        self.ui_status_tt = Tooltip(self.ui_status, 'Inactive')

        self.ui_count = tk.Label(self.root, text="0 clicks", font=("Consolas", 9), fg='#64748b', bg='#0e1117', anchor='w')
        self.ui_count.pack(side='left', padx=(0,0), pady=4)
        
        # Quota labels — individual widgets, packed left after clicks
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
        self.scroll_btn = tk.Label(self.root, text="\u21f5", font=("Segoe UI", 10), bg='#1c2128', fg='#64748b',
                                    width=2, anchor='center', bd=0, relief='flat', padx=4, pady=2)
        self.scroll_btn.pack(side='right', padx=2, pady=4)
        self.scroll_btn.bind('<Button-1>', lambda e: self.toggle_scroll())
        Tooltip(self.scroll_btn, 'Toggle auto-scroll')

        self.play_btn = tk.Label(self.root, text='\u23f8', font=("Segoe UI", 10), bg='#1c2128', fg='#f59e0b',
                                 width=2, anchor='center', bd=0, relief='flat', padx=4, pady=2)
        self.play_btn.pack(side='right', padx=2, pady=4)
        self.play_btn.bind('<Button-1>', lambda e: self.toggle_play())
        Tooltip(self.play_btn, 'Pause / resume clicker (Ctrl+Shift+/)')

        self.root.bind('<Button-1>', self._start_drag)
        self.root.bind('<B1-Motion>', self._on_drag)
        self.root.bind('<ButtonRelease-1>', self._end_drag)

        self.thread = threading.Thread(target=self.worker_loop, daemon=True)
        self.thread.start()
        
        self.quota_thread = threading.Thread(target=self._fetch_quota_worker, daemon=True)
        self.quota_thread.start()
        
        self._register_global_hotkey()
        self.refresh_ui()

    def _fetch_quota_worker(self):
        import psutil, re, subprocess, ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

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
                                    
                                    frac = q.get('remainingFraction', 1.0)
                                    pct = int(frac * 100)
                                    pct_color = 'high' if pct >= 80 else 'med' if pct >= 40 else 'low'
                                    
                                    rst = q.get('resetTime', '')
                                    # Calculate countdown to reset
                                    countdown = ''
                                    if rst and 'T' in rst:
                                        try:
                                            from datetime import datetime, timezone
                                            reset_dt = datetime.fromisoformat(rst.replace('Z', '+00:00'))
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
                                    
                                    st = rst.replace('T', ' ').replace('Z', ' UTC') if 'T' in rst else (rst or 'N/A')
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
        if e.widget in (self.settings_btn, self.play_btn, self.overlay_btn, self.restart_btn, self.scroll_btn):
            return
        self._dx, self._dy = e.x, e.y
        self.close_drawer()
    def _on_drag(self, e):
        if e.widget in (self.settings_btn, self.play_btn, self.overlay_btn, self.restart_btn, self.scroll_btn):
            return
        self.root.geometry(f"+{self.root.winfo_x()+(e.x-self._dx)}+{self.root.winfo_y()+(e.y-self._dy)}")

    def _end_drag(self, e):
        """Save pill position to settings after dragging."""
        new_x = self.root.winfo_x()
        new_y = self.root.winfo_y()
        if new_x != self.pill_x or new_y != self.pill_y:
            self.pill_x = new_x
            self.pill_y = new_y
            self._save_all()

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

    def restart_clicker(self):
        """Reset all clicker state and force re-injection of the scanner JS on next poll cycle."""
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

    def toggle_highlight(self):
        self.highlight_on = not self.highlight_on
        self.highlight_btn.configure(bg='#2d333b' if self.highlight_on else '#1c2128', fg='#00d4ff' if self.highlight_on else '#64748b')

    def toggle_scroll(self):
        self.scroll_paused = not self.scroll_paused
        if self.scroll_paused:
            self.scroll_btn.configure(bg='#2d333b', fg='#ef4444')
        else:
            self.scroll_btn.configure(bg='#1c2128', fg='#64748b')

    def toggle_play(self):
        self.active = not getattr(self, 'active', True)
        if self.active:
            self.play_btn.configure(bg='#2d333b', text='\u25b6', fg='#22c55e', font=('Segoe UI', 10))
            # Reset idle tracking when resuming
            self._last_busy_time = time.time()
            self._idle_alerted = False
        else:
            self.play_btn.configure(bg='#1c2128', text='\u23f8', fg='#f59e0b', font=('Segoe UI', 10))



    def _save_all(self):
        """Convenience: save all current settings to disk."""
        save_settings(
            self.enabled, self.scan_delay, self.click_delay, self.preset,
            self.typing_delay, self.scroll_delay, self.cb_clicks, self.cb_seconds,
            self.pill_x, self.pill_y, self.idle_alert_minutes, self.auto_start
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

        idle_cell = tk.Frame(grid, bg='#1c2128')
        idle_cell.grid(row=base_row + 1, column=1, padx=3, pady=2, sticky='ew')
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
        self.scroll_entry.bind('<KeyRelease>', lambda e: self._save_delays())
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



        # Legend row
        row8 = tk.Frame(grid, bg='#0e1117')
        row8.grid(row=base_row + 3, column=0, columnspan=3, padx=3, pady=(4, 2), sticky='ew')
        
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
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        threading.Thread(target=start_agentic_bridge, daemon=True).start()
        # Telemetry is now natively provided by CDP via the FINDER_JS responses

        while True:
            # Kill entire process if the hotbar UI is dead — prevents headless runaway clicker
            try:
                if not self.root.winfo_exists():
                    os._exit(0)
            except Exception:
                os._exit(0)

            try:
                targets = loop.run_until_complete(get_targets_async())
                
                # WHITELIST: Inject into Antigravity main pages AND the isolated 'antigravity-panel' Webview iframes
                pages = [t for t in targets 
                         if t.get('type') in ('page', 'iframe') 
                         and t.get('webSocketDebuggerUrl')
                         and ('Antigravity' in t.get('title', '') or 'antigravity-panel' in t.get('url', ''))]
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
                        a_count = 0  # Active (agent thinking)
                        w_count = 0  # Waiting (buttons present)
                        c_count = 0  # Complete (idle)
                        for p in pages:
                            ws = p.get('webSocketDebuggerUrl')
                            if not ws: continue
                            try:
                                # Force-clear stale heartbeat so new code always injects
                                # Handle clicker restart: nuke all JS state to force fresh injection
                                if getattr(self, '_pending_clicker_reset', False):
                                    loop.run_until_complete(_cdp_eval(ws, "window.__vc=false;window.__vcc=0;window.__vcm='';window.__vcClicked={};window.__vcClickLog=[];window.__vcTargets=[];window.__vcAllMatched=0;window.__vcDotsAt=0;if(window.__vcObs)try{window.__vcObs.disconnect()}catch(e){};if(window.__vcDotsObs)try{window.__vcDotsObs.disconnect()}catch(e){};if(window.__vcInt)clearInterval(window.__vcInt);if(window.__vcScanInt)clearInterval(window.__vcScanInt)"))
                                    self._pending_clicker_reset = False

                                # Send python heartbeat
                                loop.run_until_complete(_cdp_eval(ws, "window.__vchb=Date.now()"))

                                if getattr(self, '_pending_reset', False):
                                    loop.run_until_complete(_cdp_eval(ws, "window.__vcc=0"))
                                    self._pending_reset = False
                                    
                                # DIAGNOSTIC DUMP:
                                if not getattr(self, '_did_dump', False):
                                    self._did_dump = True
                                    try:
                                        dump_code = '''
                                        (function() {
                                            var ls = JSON.stringify(localStorage);
                                            var ks = Object.keys(window).filter(k => k.includes('state') || k.includes('data') || k.includes('vc') || k.includes('__')).join(', ');
                                            return ls + " ||| " + ks;
                                        })()
                                        '''
                                        res = loop.run_until_complete(_cdp_eval(ws, dump_code))
                                        with open('c:/tmp/vega_dump.txt', 'w', encoding='utf-8') as f:
                                            f.write(str(res))
                                    except Exception as e:
                                        pass
                                ov_js = f"window.__vcoverlay={'true' if self.overlay_on else 'false'};window.__vcHighlightOn={'true' if self.highlight_on else 'false'};"
                                loop.run_until_complete(_cdp_eval(ws, ov_js))

                                en_js = f"window.__vcEnabled={json.dumps(self.enabled)}"
                                loop.run_until_complete(_cdp_eval(ws, en_js))

                                scroll_paused_js = 'true' if self.scroll_paused else 'false'
                                delay_js = f"window.__vcScanDelay={self.scan_delay};window.__vcClickDelay={self.click_delay};window.__vcTypingDelay={self.typing_delay * 1000};window.__vcScrollDelay={self.scroll_delay * 1000};window.__vcCBClicks={self.cb_clicks};window.__vcCBSeconds={self.cb_seconds * 1000};window.__vcScrollPaused={scroll_paused_js}"
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
                                        dots = status.get('dots', False)
                                        all_matched = status.get('all', 0)
                                        max_cd = max(max_cd, cd)
                                        
                                        tel = status.get('tel', {})
                                            
                                        if c > 0: self.total_clicks = max(self.total_clicks, c)
                                        if m and m != self.last_msg:
                                            self.last_msg = m
                                            # Clear message in JS so it's not re-consumed on next poll
                                            loop.run_until_complete(_cdp_eval(ws, "window.__vcm=''"))
                                            print(f"[CLICK] {m}")
                                            if "[CIRCUIT BREAKER]" in m and getattr(self, 'active', False):
                                                self.toggle_play()
                                                self.status_text = "PAUSED (Loop Limit)"
                                                self.status_color = "#ef4444"
                                            self.root.after(0, self.flash_click)
                                            self.root.after(0, lambda msg=m: self.add_log(msg))


                                        self.scan_targets = inv
                                        # Classify page state
                                        if dots:
                                            a_count += 1
                                        elif all_matched > 0:
                                            w_count += 1
                                        else:
                                            c_count += 1
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
                        # Store page states for colored rendering
                        self._pages_total = len(pages)
                        self._page_states = (a_count, w_count, c_count)

                        # Idle alert: if any page is busy (active or waiting), reset timer
                        if a_count > 0 or w_count > 0:
                            self._last_busy_time = time.time()
                            self._idle_alerted = False
                        elif self.idle_alert_minutes > 0 and not self._idle_alerted:
                            idle_seconds = time.time() - self._last_busy_time
                            if idle_seconds >= self.idle_alert_minutes * 60:
                                self._idle_alerted = True
                                self.root.after(0, self._play_idle_alert)
                                self.root.after(0, lambda: self.add_log(f'Idle alert — agent idle for {self.idle_alert_minutes}min'))
                        self.status_text = 'states'  # Signal to refresh_ui to use _page_states
                    else:
                        self.status_text = "Inactive"
                        self.status_color = "#64748b"
                        a_count = 0; w_count = 0; c_count = 0
                        for p in pages:
                            ws = p.get('webSocketDebuggerUrl')
                            if not ws: continue
                            try:
                                disable_js = "if(window.__vcObs)try{window.__vcObs.disconnect()}catch(e){};if(window.__vcDotsObs)try{window.__vcDotsObs.disconnect()}catch(e){};if(window.__vcInt)clearInterval(window.__vcInt);if(window.__vcScanInt)clearInterval(window.__vcScanInt);if(window.__vcThr)clearTimeout(window.__vcThr);window.__vcScrollPaused=true;window.__vc=false;if(window.__vcKD)document.removeEventListener('keydown',window.__vcKD,true);if(window.__vcWH)document.removeEventListener('wheel',window.__vcWH,true);if(window.__vcTM)document.removeEventListener('touchmove',window.__vcTM,true);"
                                loop.run_until_complete(_cdp_eval(ws, disable_js))
                                
                                # Lightweight state probe — detect Stop button (Active) or action buttons (Waiting)
                                probe_js = "(function(){var btns=document.querySelectorAll('button,[role=button]');var hasStop=false,hasAction=false;for(var i=0;i<btns.length;i++){var t=(btns[i].textContent||'').trim().toLowerCase();var r=btns[i].getBoundingClientRect();if(r.width<1||r.height<1)continue;if(t==='stop'||t==='stop generating'||t==='cancel')hasStop=true;if(t==='accept all'||t==='allow'||t==='run'||t==='approve'||t==='continue'||t==='retry'||t==='trust')hasAction=true;}return hasStop?'A':hasAction?'W':'C';})()"
                                res = loop.run_until_complete(_cdp_eval(ws, probe_js))
                                state = res.get('result', {}).get('result', {}).get('value', 'C') if res else 'C'
                                if state == 'A': a_count += 1
                                elif state == 'W': w_count += 1
                                else: c_count += 1
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
                        self._pages_total = len(pages)
                        self._page_states = (a_count, w_count, c_count)
            except: pass
            time.sleep(POLL_INTERVAL)

    def run(self):
        try:
            self.root.mainloop()
        finally:
            # Tk mainloop exited (crash or close) — force-kill to stop worker thread
            os._exit(0)

if __name__ == "__main__":
    cleanup_old_processes()
    VegaClickApp().run()
