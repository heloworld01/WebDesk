/* 自定义控件：虚拟摇杆 + 虚拟按钮，可拖拽布局，保存到 localStorage */
(function () {
  const LS_LAYOUT = 'wd_layout';
  const LS_SIZE = 'wd_ctlsize';

  const DEFAULT_LAND = [
    { id: 's1', type: 'stick', kind: 'mouse', x: 0.16, y: 0.68, size: 120, speed: 24,
      keys: { up: 'up', down: 'down', left: 'left', right: 'right' } },
    { id: 'b1', type: 'button', label: '左键', x: 0.88, y: 0.55, act: { t: 'mouse', btn: 'left' } },
    { id: 'b2', type: 'button', label: '右键', x: 0.88, y: 0.72, act: { t: 'mouse', btn: 'right' } },
    { id: 'b3', type: 'button', label: '双击', x: 0.88, y: 0.89, act: { t: 'mouse', btn: 'left', dbl: true } },
  ];
  // 竖屏：视口窄，x 百分比压缩进右边缘窄带，默认按钮列抬高避开右下角缩放条（放大/缩小/还原）
  const DEFAULT_PORT = [
    { id: 's1', type: 'stick', kind: 'mouse', x: 0.16, y: 0.68, size: 120, speed: 24,
      keys: { up: 'up', down: 'down', left: 'left', right: 'right' } },
    { id: 'b1', type: 'button', label: '左键', x: 0.88, y: 0.36, act: { t: 'mouse', btn: 'left' } },
    { id: 'b2', type: 'button', label: '右键', x: 0.88, y: 0.50, act: { t: 'mouse', btn: 'right' } },
    { id: 'b3', type: 'button', label: '双击', x: 0.88, y: 0.64, act: { t: 'mouse', btn: 'left', dbl: true } },
  ];
  const DEFAULTS = { landscape: DEFAULT_LAND, portrait: DEFAULT_PORT };

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
      if (n) return n;
    } catch {}
    return JSON.parse(JSON.stringify(DEFAULTS));
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

    // 应用服务端下发的布局（连接时）。与本地一致则跳过，避免无谓重绘/回推
    applyServerLayout(incoming) {
      const n = normLayouts(incoming, false);
      if (!n) return;
      if (JSON.stringify(n) === JSON.stringify(this.layouts)) return;
      this.layouts = n;
      localStorage.setItem(LS_LAYOUT, JSON.stringify(n));   // 只写缓存，不回推
      this.render();
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
        this.layer.appendChild(el);
        this._nodes.set(w.id, el);
        if (this.editing) this._makeDraggable(el, w);
      }
      if (!this.editing) requestAnimationFrame(() => this._avoidZoombar());
    },

    // 右下角避让：缩放条（放大/缩小/还原）钉死在右下角，竖屏窄视口下右边缘
    // 控件（x≈0.88 的按钮列）会被压进同一条窄带、与缩放条叠压。渲染后实测相交，
    // 把控件沿最短方向推出缩放条，并把新位置存进当前方向的布局，一次修正永久生效。
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
        const overL = r.right - z.left, overT = r.bottom - z.top;   // 右下角无处往右/下推：只往左、上让
        if (overL <= overT) w.x = Math.max(0.04, w.x - (overL + 8) / W);
        else w.y = Math.max(0.06, w.y - (overT + 8) / H);
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
      const modeText = () => (w.kind === 'keys' ? '方向键' : '鼠标');
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
      badge.textContent = '切换';
      badge.addEventListener('pointerdown', (e) => {
        e.preventDefault(); e.stopPropagation();
        w.kind = (w.kind === 'keys') ? 'mouse' : 'keys';
        syncMode();
        this.save();
        if (navigator.vibrate) navigator.vibrate(15);
        const blocked = window.Net && window.Net.blocked;
        if (blocked && window.toast)
          window.toast(w.kind === 'keys' ? '方向键模式不受防误触限制，可用' : '防误触已开：鼠标模式摇杆已禁用');
      });
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

      const updateKeys = () => {
        const keys = w.keys || {};
        const want = new Set();
        if (vx < -0.5 && keys.left) want.add(keys.left);
        if (vx > 0.5 && keys.right) want.add(keys.right);
        if (vy < -0.5 && keys.up) want.add(keys.up);
        if (vy > 0.5 && keys.down) want.add(keys.down);
        // force=true：方向键是玩家有意的游戏输入，防误触(挡误触)不应拦截
        for (const k of keysDown) if (!want.has(k)) { Net.send({ t: 'key', key: k, d: 'up' }, true); keysDown.delete(k); }
        for (const k of want) if (!keysDown.has(k)) { Net.send({ t: 'key', key: k, d: 'down' }, true); keysDown.add(k); }
      };

      const loop = () => {
        if (w.kind === 'keys') { updateKeys(); return; }
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
      };
      el.addEventListener('pointerdown', start);
      el.addEventListener('pointermove', move);
      el.addEventListener('pointerup', end);
      el.addEventListener('pointercancel', end);
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
        const dx = (e.clientX - sx) / window.innerWidth;
        const dy = (e.clientY - sy) / window.innerHeight;
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
})();
