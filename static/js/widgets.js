/* 自定义控件：虚拟摇杆 + 虚拟按钮，可拖拽布局，保存到 localStorage */
(function () {
  const LS_LAYOUT = 'wd_layout';
  const LS_SIZE = 'wd_ctlsize';

  // 默认标签取当前语言（仅首次生成默认布局时生效；已保存布局的标签属于用户数据，不随语言变）
  const DEFAULT_LAND = [
    { id: 's1', type: 'stick', kind: 'mouse', x: 0.16, y: 0.68, size: 120, speed: 24,
      keys: { up: 'up', down: 'down', left: 'left', right: 'right' } },
    { id: 'b1', type: 'button', label: t('w_left'), x: 0.88, y: 0.55, act: { t: 'mouse', btn: 'left' } },
    { id: 'b2', type: 'button', label: t('w_right'), x: 0.88, y: 0.72, act: { t: 'mouse', btn: 'right' } },
    { id: 'b3', type: 'button', label: t('w_dbl'), x: 0.88, y: 0.89, act: { t: 'mouse', btn: 'left', dbl: true } },
  ];
  // 竖屏：视口窄，x 百分比压缩进右边缘窄带；缩放条已移到右上角，按钮列直接
  // 落右下方（与横屏同列）；键盘停靠时 #controls 底部收缩，%坐标自动整体上移
  const DEFAULT_PORT = [
    { id: 's1', type: 'stick', kind: 'mouse', x: 0.16, y: 0.68, size: 120, speed: 24,
      keys: { up: 'up', down: 'down', left: 'left', right: 'right' } },
    { id: 'b1', type: 'button', label: t('w_left'), x: 0.88, y: 0.55, act: { t: 'mouse', btn: 'left' } },
    { id: 'b2', type: 'button', label: t('w_right'), x: 0.88, y: 0.72, act: { t: 'mouse', btn: 'right' } },
    { id: 'b3', type: 'button', label: t('w_dbl'), x: 0.88, y: 0.89, act: { t: 'mouse', btn: 'left', dbl: true } },
  ];
  const DEFAULTS = { landscape: DEFAULT_LAND, portrait: DEFAULT_PORT };
  // 预设件默认位（v2 迁移用）：左键/右键/双击默认右下柱列，两方向一致
  const PRESET_POS = {
    landscape: { b1: [0.88, 0.55], b2: [0.88, 0.72], b3: [0.88, 0.89] },
    portrait: { b1: [0.88, 0.55], b2: [0.88, 0.72], b3: [0.88, 0.89] },
  };
  // 一次性迁移 v2：把预设按钮 b1..b3 钉到新的“右下方”默认位（横竖各改各的），
  // 用户自加控件与摇杆位置不动。旧数据里按钮为避开底部缩放条被排在中部，
  // 缩放条移去右上角后不再需要那套避让
  const LS_VER = 'wd_layoutv2';        // 本地数据迁移标记
  const LS_VER_SRV = 'wd_layoutv2srv'; // 服务端下发数据迁移标记（首连单独走一次）
  function reseedPresets(n) {
    for (const ori of ['landscape', 'portrait']) {
      const pos = PRESET_POS[ori];
      for (const w of (n[ori] || [])) {
        const p = pos[w.id];
        if (p) { w.x = p[0]; w.y = p[1]; }
      }
    }
  }
  function migrateV2(n) {
    try { if (localStorage.getItem(LS_VER)) return n; localStorage.setItem(LS_VER, '1'); }
    catch (e) { return n; }
    reseedPresets(n);
    return n;
  }

  // 旧版本预设按钮（Esc/Space/Ctrl+C/Alt+Tab）一次性清除
  const LEGACY_IDS = new Set(['b4', 'b5', 'b6', 'b7']);

  // 布局按方向分仓：横竖屏视口长宽比差一倍以上，同一套百分比坐标换方向后
  // px 间距跟着坍缩/拉伸，必然互相叠压——各方向各存一套才是正解。
  // 兼容旧数据：纯数组 = 单布局时代，两方向各用一份拷贝起步。
  function normLayouts(x, fallback) {
    const ok = (a) => Array.isArray(a);
    const cut = (a) => a.filter(w => !LEGACY_IDS.has(w.id));
    let n = null;
    if (ok(x)) n = { landscape: cut(x), portrait: JSON.parse(JSON.stringify(cut(x))) };
    else if (x && ok(x.landscape) && ok(x.portrait))
      n = { landscape: cut(x.landscape), portrait: cut(x.portrait) };
    if (!n) return fallback ? JSON.parse(JSON.stringify(DEFAULTS)) : null;
    return n;
  }

  function loadLayouts() {
    try {
      const n = normLayouts(JSON.parse(localStorage.getItem(LS_LAYOUT)), false);
      if (n) return migrateV2(n);
    } catch {}
    return migrateV2(JSON.parse(JSON.stringify(DEFAULTS)));
  }

  const Widgets = {
    layer: null,
    layouts: loadLayouts(),        // { landscape: [...], portrait: [...] }
    _orient: 'landscape',
    editing: false,
    sizeScale: parseFloat(localStorage.getItem(LS_SIZE) || '1'),
    onChange: null,          // 布局变化回调（用于保存）
    _nodes: new Map(),

    orient() { return window.innerWidth >= window.innerHeight ? 'landscape' : 'portrait'; },

    get layout() { return this.layouts[this._orient]; },
    set layout(l) { this.layouts[this._orient] = l; },

    init(layer) {
      this.layer = layer;
      this._orient = this.orient();
      this.render();
      const check = () => {
        if (this.orient() !== this._orient) { this._orient = this.orient(); this.render(); }
      };
      window.addEventListener('resize', check);
      window.addEventListener('orientationchange', () => setTimeout(check, 350));
    },

    // 每次布局变化：本地缓存 + 自动推送到服务端（layout.json），多设备共享
    save() {
      localStorage.setItem(LS_LAYOUT, JSON.stringify(this.layouts));
      if (this._pushTimer) clearTimeout(this._pushTimer);
      this._pushTimer = setTimeout(() => {
        if (window.Net && Net.connected) Net.send({ t: 'layout', layout: this.layouts });
      }, 400);
      if (this.onChange) this.onChange();
    },

    // 应用服务端下发的布局（连接时）。与本地一致则跳过，避免无谓重绘/回推。
    // 迁移必须在比较前做：服务端可能还存着 v1 时代“按钮挤在中部”的旧坐标，
    // 若不在这里同样 reseed，旧数据会立刻覆盖掉本地刚迁移好的右下方默认位
    applyServerLayout(incoming) {
      let n = normLayouts(incoming, false);
      if (!n) return;
      let reseeded = false;
      try {
        if (!localStorage.getItem(LS_VER_SRV)) {   // 升级后首连：服务端还是 v1 旧坐标，
          localStorage.setItem(LS_VER_SRV, '1');   // 同样把预设按钮挪到右下方默认位，
          reseedPresets(n);                        // 并回写一次，让所有设备收敛
          reseeded = true;
        }
      } catch (e) { /* 隐私模式禁 storage：照旧采用服务端数据 */ }
      if (!reseeded && JSON.stringify(n) === JSON.stringify(this.layouts)) return;
      this.layouts = n;
      localStorage.setItem(LS_LAYOUT, JSON.stringify(n));
      this.render();
      if (reseeded) this.save();     // 仅迁移这一次回推；平时保持“只写缓存，不回推”
    },

    setEditing(on) {
      this.editing = on;
      document.body.classList.toggle('editing', on);
      this.render();
    },

    setSizeScale(s) {
      this.sizeScale = s;
      localStorage.setItem(LS_SIZE, String(s));
      this.render();
    },

    uid() { return 'w' + Date.now().toString(36) + Math.random().toString(36).slice(2, 5); },

    addWidget(w) {
      w.id = this.uid();
      this.layout.push(w);
      this.save(); this.render();
      return w;
    },

    remove(id) {
      this.layout = this.layout.filter(w => w.id !== id);
      this.save(); this.render();
    },

    render() {
      this.layer.innerHTML = '';
      this._nodes.clear();
      for (const w of this.layout) {
        const el = w.type === 'stick' ? this._makeStick(w) : this._makeButton(w);
        el.dataset.wid = w.id;   // CSS 按预设 id 定点微调（如竖屏开键盘时挪摇杆/藏按钮）
        this.layer.appendChild(el);
        this._nodes.set(w.id, el);
        if (this.editing) this._makeDraggable(el, w);
      }
      if (!this.editing) requestAnimationFrame(() => this._avoidZoombar());
    },

    // 缩放条避让：缩放条（放大/缩小/还原）钉死在右上角，右边缘控件（x≈0.88 的
    // 按钮列、或用户拖到右上角的任意控件）可能与它叠压。渲染后实测相交，把控件
    // 沿最短方向（左/下）推出缩放条，并把新位置存进当前方向的布局，一次修正永久生效。
    _avoidZoombar() {
      const zb = document.getElementById('zoombar');
      if (!zb || zb.offsetParent === null || this.editing) return;
      const z = zb.getBoundingClientRect();
      if (!z.width) return;
      const W = this.layer.clientWidth || window.innerWidth;
      const H = this.layer.clientHeight || window.innerHeight;
      let moved = false;
      for (const [id, el] of this._nodes) {
        const r = el.getBoundingClientRect();
        if (r.right <= z.left || r.left >= z.right || r.bottom <= z.top || r.top >= z.bottom) continue;
        const w = this.layout.find(x => x.id === id);
        if (!w) continue;
        const overL = r.right - z.left, overB = z.bottom - r.top;   // 缩放条已移右上角：只往左、下让
        if (overL <= overB) w.x = Math.max(0.04, w.x - (overL + 8) / W);
        else w.y = Math.min(0.94, w.y + (overB + 8) / H);
        el.style.left = (w.x * 100) + '%';
        el.style.top = (w.y * 100) + '%';
        moved = true;
      }
      if (moved) this.save();     // 推送到服务端，其它设备同样收敛到不重叠位置
    },

    _applyScale(el, px) { el.style.width = px * this.sizeScale + 'px'; },

    /* ---------- 按钮 ---------- */
    _makeButton(w) {
      const el = document.createElement('div');
      el.className = 'widget wbtn';
      el.textContent = w.label || '·';
      el.style.left = (w.x * 100) + '%';
      el.style.top = (w.y * 100) + '%';
      if (!this.editing) this._bindButtonAction(el, w);
      return el;
    },

    _bindButtonAction(el, w) {
      const act = w.act || { t: 'key', key: 'a' };
      const isHold = act.t === 'hold';
      let active = false;
      const press = (e) => {
        e.preventDefault(); e.stopPropagation();
        if (active) return; active = true;
        el.classList.add('pressed');
        this._exec(act, true);
        if (navigator.vibrate) navigator.vibrate(8);
      };
      const release = (e) => {
        if (e) { e.preventDefault(); e.stopPropagation(); }
        if (!active) return; active = false;
        el.classList.remove('pressed');
        if (isHold) this._exec(act, false);
      };
      el.addEventListener('pointerdown', press);
      el.addEventListener('pointerup', release);
      el.addEventListener('pointercancel', release);
      el.addEventListener('pointerleave', () => { if (isHold) release(); });
      el.addEventListener('contextmenu', e => e.preventDefault());
    },

    _exec(act, down) {
      if (!act) return;
      switch (act.t) {
        case 'key': Net.send({ t: 'key', key: act.key, d: 'tap' }); break;
        case 'hold': Net.send({ t: 'key', key: act.key, d: down ? 'down' : 'up' }); break;
        case 'combo': if (down !== false) Net.send({ t: 'combo', keys: act.keys || [] }); break;
        case 'mouse':
          if (down !== false) Net.send({ t: 'click', btn: act.btn || 'left', dbl: !!act.dbl });
          break;
        case 'macro': if (down !== false) Net.send({ t: 'macro', steps: act.steps || [] }); break;
        case 'text': if (down !== false) Net.send({ t: 'text', s: act.s || '' }); break;
      }
    },

    /* ---------- 摇杆 ---------- */
    _makeStick(w) {
      const el = document.createElement('div');
      el.className = 'widget wstick ' + (w.kind === 'keys' ? 'mode-keys' : 'mode-mouse');
      el.style.left = (w.x * 100) + '%';
      el.style.top = (w.y * 100) + '%';
      const px = (w.size || 120) * this.sizeScale;
      el.style.width = px + 'px'; el.style.height = px + 'px';
      const modeText = () => (w.kind === 'keys' ? t('stick_keys') : t('stick_mouse'));
      const syncMode = () => {
        el.classList.toggle('mode-keys', w.kind === 'keys');
        el.classList.toggle('mode-mouse', w.kind !== 'keys');
        el.querySelector('.stick-label').textContent = modeText();
      };
      el.innerHTML = '<div class="stick-base"></div><div class="stick-knob"><span class="stick-label"></span></div>';
      syncMode();
      // 角标：点按即切换 鼠标/方向键 模式（防误触开启时角标仍可点，方便切到方向键）
      const badge = document.createElement('div');
      badge.className = 'stick-mode';
      badge.textContent = t('stick_switch');
      // 兜底：个别浏览器/触控组合下触摸只派发 click 不派发 pointerdown。
      // 一次真实点按两路都会到 ⇒ 用时间戳去抖，400ms 内的第二次触发忽略
      let lastFlip = 0;
      const flipMode = (e) => {
        e.preventDefault(); e.stopPropagation();
        if (performance.now() - lastFlip < 400) return;
        lastFlip = performance.now();
        w.kind = (w.kind === 'keys') ? 'mouse' : 'keys';
        syncMode();
        this.save();
        if (navigator.vibrate) navigator.vibrate(15);
        const blocked = window.Net && window.Net.blocked;
        if (blocked && window.toast)
          window.toast(w.kind === 'keys' ? t('toast_stick_keys') : t('toast_stick_mouse'));
      };
      badge.addEventListener('pointerdown', flipMode);
      badge.addEventListener('click', flipMode);
      el.appendChild(badge);
      if (!this.editing) this._bindStickAction(el, w);
      return el;
    },

    _bindStickAction(el, w) {
      const knob = el.querySelector('.stick-knob');
      const R = () => el.clientWidth / 2;
      let pid = null, vx = 0, vy = 0, timer = null;
      const keysDown = new Set();

      const DEAD = 0.22;               // 死区
      const applyVec = () => {
        knob.style.transform = 'translate(calc(-50% + ' + (vx * R() * 0.72) + 'px), calc(-50% + ' + (vy * R() * 0.72) + 'px))';
      };

      // 长按自动重复：注入的 keydown 不享受 Windows 实体键盘的按住连发
      // （系统 auto-repeat 只由真实扫描码驱动），长拉摇杆必须客户端补发。
      // 节奏仿系统键盘：按住 450ms 后开始，每 90ms 一次「抬起+按下」——
      // 用完整按压沿而非裸重复 down，游戏/菜单/文本光标各类目标都能识别。
      const RPT_DELAY = 450, RPT_INT = 90;
      const rptAt = {};                        // key → 下次补发时刻
      const repeatTick = () => {
        if (!keysDown.size) return;
        const now = performance.now();
        for (const k of keysDown) {
          if (rptAt[k] > now) continue;
          rptAt[k] = now + RPT_INT;
          Net.send({ t: 'key', key: k, d: 'up' }, true);
          Net.send({ t: 'key', key: k, d: 'down' }, true);
        }
      };

      const updateKeys = () => {
        const keys = w.keys || {};
        const want = new Set();
        if (vx < -0.5 && keys.left) want.add(keys.left);
        if (vx > 0.5 && keys.right) want.add(keys.right);
        if (vy < -0.5 && keys.up) want.add(keys.up);
        if (vy > 0.5 && keys.down) want.add(keys.down);
        const now = performance.now();
        // force=true：方向键是玩家有意的游戏输入，防误触(挡误触)不应拦截
        for (const k of keysDown)
          if (!want.has(k)) { Net.send({ t: 'key', key: k, d: 'up' }, true); keysDown.delete(k); delete rptAt[k]; }
        for (const k of want)
          if (!keysDown.has(k)) {
            Net.send({ t: 'key', key: k, d: 'down' }, true); keysDown.add(k);
            rptAt[k] = now + RPT_DELAY;
          }
      };

      const loop = () => {
        if (w.kind === 'keys') { updateKeys(); repeatTick(); return; }
        // 鼠标模式：相对移动，speed = 每秒最大像素；除以画面缩放倍数保持与画面上所见一致
        const sp = (w.speed || 14) * 60 / 15 * 0.25 * (window.WD_REL || 1);
        if (Math.hypot(vx, vy) > DEAD) {
          Net.send({ t: 'mrel', dx: Math.round(vx * sp), dy: Math.round(vy * sp) });
        }
      };

      const start = (e) => {
        e.preventDefault();
        pid = e.pointerId; el.setPointerCapture(pid);
        timer = setInterval(loop, 66);
        move(e);
      };
      const move = (e) => {
        if (e.pointerId !== pid) return;
        const r = el.getBoundingClientRect();
        let dx = (e.clientX - (r.left + r.width / 2)) / R();
        let dy = (e.clientY - (r.top + r.height / 2)) / R();
        const mag = Math.hypot(dx, dy);
        if (mag > 1) { dx /= mag; dy /= mag; }
        if (mag < DEAD) { dx = 0; dy = 0; }
        vx = dx; vy = dy;
        applyVec();
        if (w.kind === 'keys') updateKeys();
      };
      const end = (e) => {
        if (pid === null) return;
        pid = null; vx = vy = 0; applyVec();
        clearInterval(timer); timer = null;
        for (const k of keysDown) Net.send({ t: 'key', key: k, d: 'up' }, true);
        keysDown.clear();
        for (const k of Object.keys(rptAt)) delete rptAt[k];   // 松开即停连发
      };
      // 角标(切换模式)是摇杆的子节点：它的 pointerdown 会冒泡到这里启动摇杆，
      // 把一次“点角标”误判成“拉摇杆”并发出方向键。按事件目标剔除，角标只管切换。
      const fromBadge = (e) => e.target && e.target.closest && e.target.closest('.stick-mode');
      el.addEventListener('pointerdown', (e) => { if (!fromBadge(e)) start(e); });
      el.addEventListener('pointermove', (e) => { if (!fromBadge(e)) move(e); });
      el.addEventListener('pointerup', (e) => { if (!fromBadge(e)) end(e); });
      el.addEventListener('pointercancel', (e) => { if (!fromBadge(e)) end(e); });
      el.addEventListener('contextmenu', e => e.preventDefault());
    },

    /* ---------- 编辑：拖拽移动 + 点击属性 ---------- */
    _makeDraggable(el, w) {
      let sx, sy, ox, oy, moved, pid = null;
      el.addEventListener('pointerdown', (e) => {
        e.preventDefault(); e.stopPropagation();
        pid = e.pointerId; el.setPointerCapture(pid);
        const r = el.getBoundingClientRect();
        sx = e.clientX; sy = e.clientY;
        ox = w.x; oy = w.y; moved = false;
      });
      el.addEventListener('pointermove', (e) => {
        if (e.pointerId !== pid) return;
        // 用控件层实际尺寸换算：键盘停靠时层底部收缩，%坐标必须按层高校准
        const dx = (e.clientX - sx) / (this.layer.clientWidth || window.innerWidth);
        const dy = (e.clientY - sy) / (this.layer.clientHeight || window.innerHeight);
        if (Math.abs(dx) + Math.abs(dy) > 0.01) moved = true;
        w.x = Math.min(1, Math.max(0, ox + dx));
        w.y = Math.min(1, Math.max(0, oy + dy));
        el.style.left = (w.x * 100) + '%';
        el.style.top = (w.y * 100) + '%';
      });
      el.addEventListener('pointerup', (e) => {
        if (e.pointerId !== pid) return;
        pid = null;
        if (moved) this.save();
        else if (window.EditUI) window.EditUI.open(w);   // 未移动 => 打开属性
      });
    },
  };

  window.Widgets = Widgets;

  // 切换语言：重绘控件（摇杆模式文字/角标取当前语言；用户自定义按钮标签保持不变）
  document.addEventListener('wd:lang', () => { if (Widgets.layer) Widgets.render(); });
})();
