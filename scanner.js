(function(){
  // ─── HEARTBEAT ───
  if(window.__vc && (Date.now() - window.__vchb < 10000)) {
    var typDelay = window.__vcTypingDelay || 5000;
    var typLeft = window.__vctyping ? Math.max(0, typDelay - (Date.now() - window.__vctyping)) : 0;
    var scrLeft = window.__vcscrolling ? Math.max(0, 15000 - (Date.now() - window.__vcscrolling)) : 0;
    var cd = Math.max(typLeft, scrLeft);
    return JSON.stringify({s:'active', c:window.__vcc||0, m:window.__vcm||'', inv:window.__vcTargets?window.__vcTargets.length:0, cd:cd});
  }

  // ─── CLEANUP ───
  if(window.__vcObs) { try{window.__vcObs.disconnect();}catch(e){} }
  if(window.__vcInt) clearInterval(window.__vcInt);
  if(window.__vcScanInt) clearInterval(window.__vcScanInt);
  if(window.__vcKD) document.removeEventListener('keydown', window.__vcKD, true);
  if(window.__vcWH) document.removeEventListener('wheel', window.__vcWH, true);
  if(window.__vcTM) document.removeEventListener('touchmove', window.__vcTM, true);

  // ─── STATE ───
  window.__vc = true;
  window.__vchb = Date.now();
  window.__vcc = window.__vcc || 0;
  window.__vcm = window.__vcm || '';
  window.__vctyping = 0;
  window.__vcoverlay = true;
  window.__vcTargets = [];  // Scanner results — the clicker reads from here
  window.__vcClicked = {};  // Dedup map: hash -> timestamp
  window.__vcClickPause = false;  // Post-click cooldown flag


  // ─── TYPING DETECTION (5s cooldown) ───
  window.__vcKD = function(e){
    if(e.key && (e.key.length===1||e.key==='Backspace'||e.key==='Enter'||e.key==='Tab'))
      window.__vctyping = Date.now();
  };
  document.addEventListener('keydown', window.__vcKD, true);

  // ─── SCROLL DETECTION (15s cooldown for auto-scroll only) ───
  window.__vcscrolling = 0;
  window.__vcWH = function(){ window.__vcscrolling = Date.now(); };
  window.__vcTM = function(){ window.__vcscrolling = Date.now(); };
  document.addEventListener('wheel', window.__vcWH, true);
  document.addEventListener('touchmove', window.__vcTM, true);

  // ─── AUTO-SCROLL: Keep chat pinned to bottom ───
  function autoScroll() {
    // Don't auto-scroll if user scrolled in last 15s
    var sclDelay = window.__vcScrollDelay || 15000;
    if(Date.now() - window.__vcscrolling < sclDelay) return;
    var containers = document.querySelectorAll('[class*="overflow"]');
    for(var i=0; i<containers.length; i++){
      var el = containers[i];
      var cs = window.getComputedStyle(el);
      if((cs.overflowY==='auto'||cs.overflowY==='scroll') && el.scrollHeight > el.clientHeight + 50){
        var distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
        if(distFromBottom > 200){
          el.scrollTop = el.scrollHeight;
        }
      }
    }
  }

  // ─── BLOCKLIST ───
  var BL = [
    'delete','remove','uninstall','format','reset','sign out','log out',
    'close','cancel','discard','reject','deny','dismiss','erase','drop',
    'run and debug','go back','go forward','more actions','always run',
    'running','runner','run extension','run_cli','rescue run','rescue',
    'allowlist','restart','reload','rules','mcp','feedback','star'
  ];

  // ─── DANGER COMMANDS ───
  var BCMD = ['rm ','rm -','del ','format ','fdisk','mkfs','dd if=','DROP TABLE','DROP DATABASE'];

  // ═══════════════════════════════════════════════════════════
  // PART 1: DEEP SCANNER
  // Walks ENTIRE DOM tree including shadow DOMs and iframes
  // Finds ALL elements, checks text against keywords
  // Stores matches in window.__vcTargets
  // ═══════════════════════════════════════════════════════════
  function deepScan() {
    var targets = [];
    var seen = new Set();

    function walk(root, depth) {
      if(depth > 12) return;
      try {
        // Get ALL elements in this root
        var all = root.querySelectorAll('*');
        for(var i = 0; i < all.length; i++) {
          var e = all[i];
          if(seen.has(e)) continue;
          seen.add(e);

          // Get text from multiple sources
          var raw = '';
          // For leaf-ish elements, use textContent; for containers, use first line of innerText
          var inner = (e.innerText || '').trim();
          var textC = (e.textContent || '').trim();
          var aria = (e.getAttribute('aria-label') || '').trim();
          var title = (e.getAttribute('title') || '').trim();
          var val = (e.getAttribute('value') || '').trim();

          // Prefer short text sources (more specific)
          if(inner && inner.length < 60) raw = inner;
          else if(textC && textC.length < 60) raw = textC;
          else if(aria) raw = aria;
          else if(title) raw = title;
          else if(val) raw = val;

          if(!raw && !e.getAttribute('data-tooltip-id')) {
            // Enter shadow roots even if no text
            if(e.shadowRoot) walk(e.shadowRoot, depth+1);
            continue;
          }

          var t = raw.split(/\r?\n/)[0].trim().toLowerCase();
          var hasTooltip = !!e.getAttribute('data-tooltip-id');
          if(!hasTooltip && (t.length > 60 || t.length < 2)) {
            if(e.shadowRoot) walk(e.shadowRoot, depth+1);
            continue;
          }

          // Blocklist check
          var blocked = false;
          for(var bi=0; bi<BL.length; bi++){
            if(t.indexOf(BL[bi])>=0){ blocked=true; break; }
          }

          // Skip menubar and non-chat UI elements
          var cls = (e.className||'').toString().toLowerCase();
          var tag = e.tagName ? e.tagName.toLowerCase() : '';

          // Skip list: editor, code, tabs, terminal, status bar, diff viewer, sidebar
          if(cls.indexOf('menubar-menu')>=0 || cls.indexOf('menu-item')>=0 ||
             cls.indexOf('mtk')>=0 ||           // Monaco token (code highlighting)
             cls.indexOf('monaco-icon-label')>=0 || // File path labels
             cls.indexOf('tab ')>=0 || cls.indexOf('tab-')>=0 || // File tabs
             cls.indexOf('diffeditor')>=0 || cls.indexOf('diff-')>=0 || // Diff view
             cls.indexOf('editor-container')>=0 ||  // Editor area
             cls.indexOf('xterm')>=0 ||          // Terminal
             cls.indexOf('statusbar')>=0 ||      // Status bar
             cls.indexOf('minimap')>=0 ||         // Minimap
             cls.indexOf('breadcrumb')>=0 ||      // Breadcrumbs
             cls.indexOf('explorer')>=0 ||        // File explorer
             cls.indexOf('label-name')>=0 ||      // Tab labels
             cls.indexOf('filename-link')>=0 ||   // Filename links in chat (not buttons)
             cls.indexOf('action-card')>=0 ||     // Sidebar action cards
             cls.indexOf('action-row')>=0 ||      // Sidebar action rows
             cls.indexOf('sidebar')>=0 ||         // Sidebar elements
             cls.indexOf('settings')>=0 ||        // Settings panels
             cls.indexOf('global-tooltip')>=0 ||  // Tooltips
             (tag === 'span' && cls.indexOf('mtk')>=0) || // Code spans
             (tag === 'div' && cls.indexOf('view-line')>=0)) { // Editor lines
            if(e.shadowRoot) walk(e.shadowRoot, depth+1);
            continue;
          }

          // Also skip if element is inside the editor (role=tab = file tab, not action button)
          if(tag !== 'button' && e.getAttribute('role') === 'tab') {
            if(e.shadowRoot) walk(e.shadowRoot, depth+1);
            continue;
          }

          // PARENT CHECK: skip if inside sidebar, action-card, settings panel
          var inSidebar = false;
          var pp = e.parentElement;
          for(var pi=0; pi<6 && pp; pi++){
            var pc = (pp.className||'').toString().toLowerCase();
            var pt = pp.tagName ? pp.tagName.toLowerCase() : '';
            if(pc.indexOf('sidebar')>=0 || pc.indexOf('action-card')>=0 ||
               pc.indexOf('action-row')>=0 || pc.indexOf('settings')>=0 ||
               pc.indexOf('panel-container')>=0 || pc.indexOf('pane-body')>=0 ||
               pt === 'sidebar-footer' || pt === 'sidebar-header'){
              inSidebar = true; break;
            }
            pp = pp.parentElement;
          }
          if(inSidebar) continue;

          // CHAT PANEL CHECK: only click inside the agent chat panel
          var inChat = false;
          var cp = e.parentElement;
          for(var ci=0; ci<25 && cp; ci++){
            var ccls = (cp.className||'').toString().toLowerCase();
            var cid = (cp.id||'').toLowerCase();
            if(ccls.indexOf('antigravity-agent-side-panel') >= 0 || cid === 'conversation') {
              inChat = true; break;
            }
            cp = cp.parentElement;
          }
          if(!inChat) {
            if(e.shadowRoot) walk(e.shadowRoot, depth+1);
            continue;
          }

          if(blocked) {
            if(e.shadowRoot) walk(e.shadowRoot, depth+1);
            continue;
          }

          // ─── INTERACTIVITY CHECK: only match actual buttons ───
          var isClickable = (tag === 'button' || tag === 'a' || e.getAttribute('role') === 'button');
          if(!isClickable) {
            try {
              var cs2 = window.getComputedStyle(e);
              if(cs2.cursor === 'pointer') isClickable = true;
            } catch(ex){}
          }
          // Also check up to 3 parents — catches text nodes (STRONG/SPAN) inside buttons
          if(!isClickable) {
            var pp2 = e.parentElement;
            for(var pi2=0; pi2<3 && pp2; pi2++) {
              var pt2 = pp2.tagName ? pp2.tagName.toLowerCase() : '';
              if(pt2 === 'button' || pt2 === 'a' || pp2.getAttribute('role') === 'button') {
                isClickable = true; e = pp2; break;
              }
              try {
                if(window.getComputedStyle(pp2).cursor === 'pointer') {
                  isClickable = true; e = pp2; break;
                }
              } catch(ex){}
              pp2 = pp2.parentElement;
            }
          }
          if(!isClickable) {
            if(e.shadowRoot) walk(e.shadowRoot, depth+1);
            continue;
          }

          // ─── KEYWORD MATCHING ───
          var kw = null, priority = 0;

          if(t === 'accept all' || t.indexOf('accept all') === 0)
            { kw='accept all'; priority=100; }
          else if(t === 'allow in this conversation' || t === 'allow this conversation')
            { kw='allow'; priority=85; }
          else if(t === 'trust' || t.indexOf('trust ') === 0)
            { kw='trust'; priority=85; }
          else if(t === 'approve' || t.indexOf('approve ') === 0)
            { kw='approve'; priority=80; }
          else if(t === 'continue')
            { kw='continue'; priority=75; }
          else if(t.indexOf('run') === 0)
            { kw='run'; priority=70; }
          else if(t === 'retry')
            { kw='retry'; priority=65; }
          else if(t === 'ok')
            { kw='ok'; priority=60; }
          else if(t === 'yes')
            { kw='yes'; priority=55; }
          else if(t === 'apply')
            { kw='apply'; priority=50; }
          else if(t === 'relocate')
            { kw='relocate'; priority=45; }

          // Tooltip fallback: icon-only buttons (e.g. Changes Overview)
          if(!kw) {
            var tip = (e.getAttribute('data-tooltip-id')||'').toLowerCase();
            if(tip === 'tooltip-changesoverview') {
              // The panel is OPEN if the title ("0 Files With Changes") exists.
              // Therefore we only click if the title DOES NOT exist (panel is closed).
              // We also add a 3.5s cooldown to prevent double-clicking during the animation/mounting phase.
              var titleEl = document.querySelector('[data-tooltip-id="toolbar-title-tooltip"]');
              var titleExists = false;
              if (titleEl) {
                var txt = (titleEl.innerText || '').toLowerCase();
                if (txt.indexOf('changes') >= 0) titleExists = true;
              }
              
              var timeSinceClick = window.__vcOverviewAt ? (Date.now() - window.__vcOverviewAt) : 999999;
              if(!titleExists && timeSinceClick > 3500) {
                kw='changes overview'; priority=40;
              }
            }
          }

          if(kw) {
            // Check if this keyword is toggled on in settings
            if(window.__vcEnabled && window.__vcEnabled[kw] === false) {
              if(e.shadowRoot) walk(e.shadowRoot, depth+1);
              continue;
            }
            targets.push({el:e, kw:kw, priority:priority, text:t, depth:depth});
          }

          // Always recurse into shadow roots
          if(e.shadowRoot) walk(e.shadowRoot, depth+1);
        }

        // Recurse into iframes
        var iframes = root.querySelectorAll('iframe, webview');
        for(var j=0; j<iframes.length; j++){
          try {
            var doc = iframes[j].contentDocument || (iframes[j].contentWindow && iframes[j].contentWindow.document);
            if(doc) walk(doc, depth+1);
          } catch(e){}
        }
      } catch(e){}
    }

    walk(document, 0);
    window.__vcTargets = targets;
  }

  // ═══════════════════════════════════════════════════════════
  // PART 2: FAST CLICKER
  // Reads from window.__vcTargets (populated by scanner)
  // Quickly filters for visibility, dedup, danger
  // Clicks all valid targets in priority order
  // ═══════════════════════════════════════════════════════════
  function clickTargets() {
    window.__vchb = Date.now();

    // Post-click cooldown — wait for rescan cycle
    if(window.__vcClickPause) return;

    // Typing cooldown (5s)
    var typD2 = window.__vcTypingDelay || 5000;
    if(Date.now() - window.__vctyping < typD2) return;

    // Auto-scroll chat (respects 15s scroll cooldown)
    autoScroll();

    var targets = window.__vcTargets || [];
    if(targets.length === 0) return;

    var candidates = [];

    for(var i=0; i<targets.length; i++){
      var t = targets[i];
      var e = t.el;
      if(!e || !e.isConnected) continue; // Element removed from DOM
      if(e.dataset && e.dataset.vc16) continue; // Already clicked

      // Visibility check
      var r;
      try { r = e.getBoundingClientRect(); } catch(ex){ continue; }
      if(r.width === 0 || r.height === 0) continue;
      if(r.top < -10 || r.bottom > window.innerHeight + 50) continue;
      try {
        var cs = window.getComputedStyle(e);
        if(cs.display === 'none' || cs.visibility === 'hidden') continue;
      } catch(ex){}

      // Danger check for 'run'
      if(t.kw === 'run') {
        var danger = false, p = e;
        for(var j=0; j<4 && p; j++){
          try {
            var cd = p.querySelector('code,pre');
            if(cd){
              var cdt = (cd.textContent||'');
              for(var di=0; di<BCMD.length; di++){
                if(cdt.indexOf(BCMD[di])>=0){ danger=true; break; }
              }
            }
          } catch(ex){}
          if(danger) break;
          p = p.parentElement;
        }
        if(danger) continue;
      }

      // Dedup check
      var hash = t.kw + '|' + Math.round(r.left/20) + '|' + Math.round(r.top/20);
      var lastClick = window.__vcClicked[hash];
      if(lastClick && Date.now() - lastClick < 5000) continue;

      candidates.push({el:e, kw:t.kw, priority:t.priority, rect:r, hash:hash, text:t.text});
    }

    // Sort by priority
    candidates.sort(function(a,b){ return b.priority - a.priority; });

    // Inject ripple CSS
    if(candidates.length > 0 && window.__vcoverlay && !window.__vccss){
      window.__vccss = true;
      var st = document.createElement('style');
      st.textContent = '@keyframes vcripple{0%{transform:scale(0.5);opacity:1}100%{transform:scale(2.5);opacity:0}}';
      document.head.appendChild(st);
    }

    var colors = {
      'run':'59,130,246','accept all':'34,197,94','accept':'34,197,94',
      'allow':'34,197,94','trust':'34,197,94','continue':'34,197,94',
      'retry':'234,179,8','approve':'99,102,241','changes overview':'168,85,247'
    };

    // Click all
    for(var ci=0; ci<candidates.length; ci++){
      var c = candidates[ci];
      var el = c.el;
      var rect = c.rect;

      el.dataset.vc16 = '1';
      window.__vcClicked[c.hash] = Date.now();

      // Click dispatch
      try {
        var cx = rect.left + rect.width/2;
        var cy = rect.top + rect.height/2;
        el.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,cancelable:true,clientX:cx,clientY:cy}));
        el.dispatchEvent(new PointerEvent('pointerup',{bubbles:true,cancelable:true,clientX:cx,clientY:cy}));
        el.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,clientX:cx,clientY:cy}));
        // DANGEROUS FOR TOGGLES: Calling el.click() 50ms after the MouseEvent fires the click twice for React buttons.
        // We skip it for "changes overview" entirely.
        if(c.kw !== 'changes overview') {
          if(typeof el.click==='function') setTimeout(function(){try{el.click();}catch(e){}}, 50);
        }
      } catch(ex){
        try{el.click();}catch(e2){}
      }

      if(typeof el.blur==='function') el.blur();

      // Visual ripple
      if(window.__vcoverlay){
        var rgb = colors[c.kw]||'139,92,246';
        var dx = Math.round(rect.left+rect.width/2);
        var dy = Math.round(rect.top+rect.height/2);
        var dot = document.createElement('div');
        dot.style.cssText = 'position:fixed;pointer-events:none;z-index:999999;border-radius:50%;'+
          'left:'+(dx-16)+'px;top:'+(dy-16)+'px;width:32px;height:32px;'+
          'border:3px solid rgba('+rgb+',0.9);background:rgba('+rgb+',0.3);'+
          'animation:vcripple 0.5s ease-out forwards;';
        document.body.appendChild(dot);
        setTimeout(function(){try{dot.remove();}catch(e){}}, 600);
      }

      window.__vcc++;
      window.__vcm = 'Clicked ' + c.kw + ' (' + c.text.slice(0,15) + ')';

      // 3.5s debounce to prevent double-clicks while animations/rendering finish
      if(c.kw === 'changes overview') window.__vcOverviewAt = Date.now();
      // Clear click flag after delay
      (function(el){
        setTimeout(function(){ try { delete el.dataset.vc16; } catch(e){} }, 600);
      })(el);

      // Clear flag after 5s as fallback
      (function(el){setTimeout(function(){try{delete el.dataset.vc16;}catch(e){}}, 5000);})(el);
    }

    // Cleanup old dedup entries
    var now = Date.now();
    for(var key in window.__vcClicked){
      if(now - window.__vcClicked[key] > 10000) delete window.__vcClicked[key];
    }

    // Post-click cooldown: scan delay → rescan → remaining gap → click
    if(candidates.length > 0) {
      window.__vcClickPause = true;
      var scanD = window.__vcScanDelay || 100;
      var clickD = window.__vcClickDelay || 150;
      var gap = Math.max(0, clickD - scanD);
      setTimeout(function(){
        deepScan();
        setTimeout(function(){
          window.__vcClickPause = false;
          clickTargets();
        }, gap);
      }, scanD);
    }
  }

  // ═══════════════════════════════════════════════════════════
  // PART 3: ORCHESTRATOR
  // Deep scan runs every 2s (thorough but heavier)
  // Click check runs every 100ms on mutation + every 500ms
  // ═══════════════════════════════════════════════════════════

  // Initial deep scan
  deepScan();

  // Deep scan on interval (re-walks entire DOM)
  window.__vcScanInt = setInterval(deepScan, 2000);

  // Fast clicker on mutation observer
  var thr = null;
  window.__vcObs = new MutationObserver(function(){
    // Re-scan on DOM changes (quick scan + click)
    deepScan();
    if(thr) return;
    thr = setTimeout(function(){ thr=null; clickTargets(); }, 80);
  });
  window.__vcObs.observe(document.body, {childList:true, subtree:true});

  // Fast clicker on interval
  window.__vcInt = setInterval(clickTargets, 500);
  setTimeout(clickTargets, 200);

  return JSON.stringify({s:'injected', c:0, m:'v16 deep scanner injected', cd:0});
})()
