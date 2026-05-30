import sys
import re

with open('vegaclick.py', 'r', encoding='utf-8') as f:
    text = f.read()

# 1. load_settings
text = text.replace("typing_delay = data.get('typing_delay', 5)", "typing_delay = data.get('typing_delay', 5)\n            tab_delay = data.get('tab_delay', 15)")
text = text.replace("preset, typing_delay, scroll_delay, cb_clicks", "preset, typing_delay, scroll_delay, tab_delay, cb_clicks")

# 2. save_settings
text = text.replace("preset='All', typing_delay=5, scroll_delay=15", "preset='All', typing_delay=5, scroll_delay=15, tab_delay=15")
text = text.replace("'preset': preset, 'typing_delay': typing_delay, 'scroll_delay': scroll_delay", "'preset': preset, 'typing_delay': typing_delay, 'scroll_delay': scroll_delay, 'tab_delay': tab_delay")

# 3. GUI init variables
text = text.replace("self.typing_delay, self.scroll_delay, self.cb_clicks", "self.typing_delay, self.scroll_delay, self.tab_delay, self.cb_clicks")

# 4. Add Tab to UI
# We add it next to typing
tab_ui = """
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
"""
# Replacing the idle_cell with tab_cell, and moving idle_cell to column 2
text = text.replace("""        self.typing_entry.pack(side='left')
        tk.Label(typing_cell, text="s", font=("Consolas", 8),
                 fg='#64748b', bg='#1c2128').pack(side='left', padx=(0,4))

        idle_cell = tk.Frame(grid, bg='#1c2128')
        idle_cell.grid(row=base_row + 1, column=1, padx=3, pady=2, sticky='ew')""", tab_ui + """
        idle_cell = tk.Frame(grid, bg='#1c2128')
        idle_cell.grid(row=base_row + 1, column=2, padx=3, pady=2, sticky='ew')""")

# Bind KeyRelease
text = text.replace("self.typing_entry.bind('<KeyRelease>', lambda e: self._save_delays())", "self.typing_entry.bind('<KeyRelease>', lambda e: self._save_delays())\n        self.tab_entry.bind('<KeyRelease>', lambda e: self._save_delays())")

# _save_delays parsing
text = text.replace("self.typing_delay = max(0, int(self.typing_entry.get()))", "self.typing_delay = max(0, int(self.typing_entry.get()))\n            self.tab_delay = max(0, int(self.tab_entry.get()))")

# _save_all log
text = text.replace("typing={self.typing_delay} scroll={self.scroll_delay}", "typing={self.typing_delay} scroll={self.scroll_delay} tab={self.tab_delay}")

# 5. Injection of tracker logic in loop (around DOM.enable)
tracker_inject = """                                    await ws.send(json.dumps({"id": 1, "method": "DOM.enable"}))
                                    await ws.send(json.dumps({"id": 2, "method": "Accessibility.enable"}))

                                    tracker_js = '''(function(){
                                        if(window._vc_tracker) return;
                                        window._vc_tracker = { type:0, scroll:0, click:0 };
                                        document.addEventListener('keydown', e => { if(e.isTrusted && (e.key.length===1||e.key==='Backspace'||e.key==='Enter')) window._vc_tracker.type = Date.now(); }, true);
                                        document.addEventListener('wheel', e => { if(e.isTrusted) window._vc_tracker.scroll = Date.now(); }, true);
                                        document.addEventListener('touchmove', e => { if(e.isTrusted) window._vc_tracker.scroll = Date.now(); }, true);
                                        document.addEventListener('mousedown', e => { if(e.isTrusted) window._vc_tracker.click = Date.now(); }, true);
                                    })()'''
                                    await ws.send(json.dumps({"id": 4, "method": "Runtime.evaluate", "params": {"expression": tracker_js}}))"""

text = text.replace("""                                    await ws.send(json.dumps({"id": 1, "method": "DOM.enable"}))
                                    await ws.send(json.dumps({"id": 2, "method": "Accessibility.enable"}))""", tracker_inject)

# 6. Reading tracker in the while True loop
# Find 'auto_scroll_js' usage
tracker_query = """                                # Tracker query
                                tracker_query = '''(function(){
                                    if(!window._vc_tracker) return {type:0, scroll:0, click:0};
                                    var n = Date.now();
                                    return {
                                        type: n - window._vc_tracker.type,
                                        scroll: n - window._vc_tracker.scroll,
                                        click: n - window._vc_tracker.click
                                    };
                                })()'''
                                await ws.send(json.dumps({"id": 98, "method": "Runtime.evaluate", "params": {"expression": tracker_query, "returnByValue": True}}))"""
text = text.replace("""                                # Auto scroll
                                if not self.scroll_paused:""", tracker_query + "\n\n" + """                                # Auto scroll
                                if not self.scroll_paused:""")

# 7. Processing tracker query response and setting cooldown
# Find `dots = is_agent_loading`
tracker_process = """                                        if data.get("id") == 98:
                                            res_val = data.get("result", {}).get("result", {}).get("value")
                                            if res_val:
                                                t_left = max(0, self.typing_delay - res_val.get('type', 9999999)/1000.0)
                                                s_left = max(0, self.scroll_delay - res_val.get('scroll', 9999999)/1000.0)
                                                c_left = max(0, self.tab_delay - res_val.get('click', 9999999)/1000.0)
                                                user_wait = max(t_left, s_left, c_left)
                                                if user_wait > 0:
                                                    user_cooldown = user_wait * 1000
                                                    if user_cooldown > max_cd:
                                                        max_cd = user_cooldown"""

text = text.replace("""                                        if data.get("id") == 101:""", tracker_process + "\n\n" + """                                        if data.get("id") == 101:""")

with open('vegaclick.py', 'w', encoding='utf-8') as f:
    f.write(text)
print("vegaclick.py patched successfully!")
