/* WebDesk 键盘视图（与主页面同一 URL 单页共存，#kbview 覆盖显示时激活）。
   触摸板手势与主页面一致；虚拟键盘（shift/ctrl·alt 组合键锁存；Caps 真实切远端灯，
   大写态由服务端 {t:'caps'} 回传同步）；
   桌面浏览器可用实体键盘直接转发（中文等输入法在电脑本机或实体键盘侧解决）。
   连接/鉴权/串流模式统一由 app.js 管理，本模块只管输入与光标反馈。 */
(function () {
  const $ = (s) => document.querySelector(s);

  const S = { w: 0, h: 0 };
  let est = null;               // 光标位置估计（服务端 cur 校准）
  const tp = $('#tp'), mm = $('#mm'), mctx = mm.getContext('2d');
  let active = false;           // 键盘视图是否正在展示（app.js 经 Kb.setActive 控制）

  function setStatus(ok, text) {
    $('#lamp').className = ok === true ? 'ok' : (ok === false ? 'bad' : '');
    $('#st').textContent = text;
  }

  /* ================= 连接状态展示 ================= */
  Net.on('open', () => setStatus(true, '已连接'));
  Net.on('ok', () => setStatus(true, '已连接'));    // 输码通过：从“请输码”提示恢复（open 早于输码，不会再触发）
  Net.on('busy', () => setStatus(false, '已有设备在用，每 5 秒重试…'));
  Net.on('close', (d) => {
    const n = (d && d.retry) || 1;
    setStatus(false, n <= 2 ? '已断开，自动重连中…'
                            : `已断开，已重试 ${n} 次 — 请刷新本页面重连`);
  });
  Net.on('hello', (m) => {
    S.w = m.w; S.h = m.h; est = null; drawMM();
    if (m.need_auth && !localStorage.getItem('wd_pin'))
      setStatus(false, '请在弹出框输入验证码');   // 此刻 app.js 的输码弹框即将弹出
  });
  Net.on('err', (m) => {
    if (String(m.msg || '').match(/验证码|PIN/)) setStatus(false, '请在弹出框输入验证码');
  });

  /* ================= 光标反馈（小地图 + 坐标） ================= */
  Net.on('cur', (m) => {
    if (!est || Math.abs(m.x - est.x) > 24 || Math.abs(m.y - est.y) > 24) {
      est = { x: m.x, y: m.y };            // 偏差大直接吸附
    } else {
      est.x += (m.x - est.x) * 0.4;        // 小偏差平滑纠偏
      est.y += (m.y - est.y) * 0.4;
    }
    $('#pos').textContent = Math.round(est.x) + ', ' + Math.round(est.y);
    drawMM();
  });

  function drawMM() {
    if (!mctx || !S.w) return;
    const sc = Math.min(mm.width / S.w, mm.height / S.h);
    mctx.clearRect(0, 0, mm.width, mm.height);
    if (est) {
      mctx.fillStyle = '#ff6b6b';
      mctx.beginPath();
      mctx.arc(Math.min(mm.width - 3, est.x * sc), Math.min(mm.height - 3, est.y * sc), 3, 0, 7);
      mctx.fill();
    }
  }

  /* ================= 触摸板 ================= */
  const K = 1.4;                  // 触摸板灵敏度（无画面缩放，固定倍率）
  const pointers = new Map();
  let gesture = null;
  let mvAccX = 0, mvAccY = 0;     // 亚像素残差（慢速拖动不丢步）

  function midPoint() {
    const pts = [...pointers.values()];
    let x = 0, y = 0;
    for (const q of pts) { x += q.x; y += q.y; }
    const n = Math.max(1, pts.length);
    return { x: x / n, y: y / n };
  }
  function cancelLongPress() {
    for (const p of pointers.values()) if (p.lpTimer) { clearTimeout(p.lpTimer); p.lpTimer = null; }
  }
  function flash() { tp.classList.add('flash'); setTimeout(() => tp.classList.remove('flash'), 180); }

  tp.addEventListener('pointerdown', (e) => {
    e.preventDefault();
    tp.setPointerCapture(e.pointerId);
    const p = { x: e.clientX, y: e.clientY, t0: Date.now(), moved: 0 };
    pointers.set(e.pointerId, p);
    if (pointers.size === 2) {
      cancelLongPress();
      gesture = { type: 'scroll', y0: midPoint().y, acc: 0, tap: true, t0: Date.now() };
    } else if (pointers.size === 1 && !gesture) {
      p.lpTimer = setTimeout(() => {          // 长按 = 右键
        if (p.moved < 10) {
          Net.send({ t: 'click', btn: 'right' });
          p.consumed = true; flash();
          if (navigator.vibrate) navigator.vibrate(20);
        }
      }, 520);
    }
  });

  tp.addEventListener('pointermove', (e) => {
    const p = pointers.get(e.pointerId);
    if (!p) return;
    e.preventDefault();
    const dx = e.clientX - p.x, dy = e.clientY - p.y;
    p.moved += Math.abs(dx) + Math.abs(dy);
    p.x = e.clientX; p.y = e.clientY;

    if (gesture && gesture.type === 'scroll' && pointers.size >= 2) {
      const mid = midPoint();
      gesture.acc += gesture.y0 - mid.y;
      if (Math.abs(gesture.acc) > 18) {       // 双指拖动 = 滚轮
        Net.send({ t: 'wheel', dy: Math.sign(gesture.acc) * Math.min(5, Math.abs(gesture.acc) / 12) });
        gesture.acc = 0;
      }
      if (p.moved > 10) gesture.tap = false;
      return;
    }
    if (gesture) return;
    if (p.moved > 10 && p.lpTimer) { clearTimeout(p.lpTimer); p.lpTimer = null; }

    // 单指拖动 = 移光标：绝对定位 mmove + 残差累积，不受鼠标加速影响
    if (est && S.w) {
      mvAccX += dx * K; mvAccY += dy * K;
      const sx = Math.round(mvAccX), sy = Math.round(mvAccY);
      if (sx || sy) {
        mvAccX -= sx; mvAccY -= sy;
        est.x = Math.min(S.w - 1, Math.max(0, est.x + sx));
        est.y = Math.min(S.h - 1, Math.max(0, est.y + sy));
        Net.send({ t: 'mmove', x: Math.round(est.x), y: Math.round(est.y) });
      }
    } else {
      Net.send({ t: 'mrel', dx: Math.round(dx * K), dy: Math.round(dy * K) });
    }
  });

  function endPointer(e) {
    const p = pointers.get(e.pointerId);
    if (!p) return;
    pointers.delete(e.pointerId);
    if (p.lpTimer) clearTimeout(p.lpTimer);
    if (gesture) {
      if (pointers.size === 0) {
        const quick = Date.now() - gesture.t0 < 350;
        if (gesture.type === 'scroll' && gesture.tap && quick) {
          Net.send({ t: 'click', btn: 'left', dbl: true });   // 双指轻点 = 双击
          flash();
        }
        gesture = null;
      }
      return;
    }
    const quick = Date.now() - p.t0 < 350;
    if (!p.consumed && p.moved < 10 && quick) {               // 轻点 = 左键
      Net.send({ t: 'click', btn: 'left' });
      flash();
    }
  }
  tp.addEventListener('pointerup', endPointer);
  tp.addEventListener('pointercancel', endPointer);
  tp.addEventListener('contextmenu', (e) => e.preventDefault());

  /* ================= 鼠标快捷按钮 ================= */
  const MOUSE_ACT = {
    esc: () => Net.send({ t: 'key', key: 'esc', d: 'tap' }),
    left: () => Net.send({ t: 'click', btn: 'left' }),
    right: () => Net.send({ t: 'click', btn: 'right' }),
    dbl: () => Net.send({ t: 'click', btn: 'left', dbl: true }),
    wu: () => Net.send({ t: 'wheel', dy: -3 }),
    wd: () => Net.send({ t: 'wheel', dy: 3 }),
  };
  for (const b of document.querySelectorAll('#mrow button')) {
    b.addEventListener('pointerdown', (e) => {
      e.preventDefault();
      (MOUSE_ACT[b.dataset.a] || (() => {}))();
      if (navigator.vibrate) navigator.vibrate(8);
    });
  }

  /* ================= 虚拟键盘 ================= */
  // 条目：[键名, 显示文字, flex 宽度, shift 符号]；字母默认小写，大写态显示大写
  // Esc 已移到鼠标快捷行（#mrow）最左，这里不再重复
  const ROWS = [
    [['`', '`', 1.3, '~'], ['1', '1', 1, '!'], ['2', '2', 1, '@'],
     ['3', '3', 1, '#'], ['4', '4', 1, '$'], ['5', '5', 1, '%'], ['6', '6', 1, '^'],
     ['7', '7', 1, '&'], ['8', '8', 1, '*'], ['9', '9', 1, '('], ['0', '0', 1, ')'],
     ['-', '-', 1, '_'], ['=', '=', 1, '+'], ['backspace', '⌫', 1.7]],
    [['tab', 'Tab', 1.55], ['q'], ['w'], ['e'], ['r'], ['t'], ['y'], ['u'], ['i'],
     ['o'], ['p'], ['[', '[', 1, '{'], [']', ']', 1, '}'], ['\\', '\\', 1, '|']],
    [['capslock', 'Caps', 1.85, true], ['a'], ['s'], ['d'], ['f'], ['g'], ['h'], ['j'],
     ['k'], ['l'], [';', ';', 1, ':'], ["'", "'", 1, '"'], ['enter', 'Enter', 1.85]],
    [['shift', 'Shift', 2.15, true], ['z'], ['x'], ['c'], ['v'], ['b'], ['n'], ['m'],
     [',', ',', 1, '<'], ['.', '.', 1, '>'], ['/', '/', 1, '?']],
    [['ctrl', 'Ctrl', 2.2], ['alt', 'Alt', 2.2],
     ['space', 'Space', 0, true]],
  ];

  let shiftLatch = false, capsOn = false;
  // 大写态以远端真实灯态为准：服务端在 Caps 变化时推 {t:'caps'}，本地即时校准
  Net.on('caps', (m) => {
    const on = !!m.on;
    if (on === capsOn) return;
    capsOn = on;
    paintMods(); paintCase();
  });
  const mods = new Set();                       // 锁存的 ctrl/alt/win
  const MOD_KEYS = new Set(['shift', 'capslock', 'ctrl', 'alt', 'win']);

  const kbRoot = $('#kb');
  const modBtns = {};
  const letterBtns = [];                        // 字母键随大写态切换显示
  function buildKB() {
    kbRoot.innerHTML = '';
    letterBtns.length = 0;
    for (const row of ROWS) {
      const r = document.createElement('div');
      r.className = 'krow';
      for (const entry of row) {
        const [key, label0, flex] = entry;
        const b = document.createElement('button');
        b.dataset.key = key;
        b.textContent = label0 !== undefined ? label0 : key;   // 字母按默认小写显示
        if (/^[a-z]$/.test(key)) letterBtns.push(b);
        if (key === 'space') b.className = 'space';
        else if (flex) b.style.flex = String(flex);
        if (MOD_KEYS.has(key)) { b.classList.add('mod', 'wide'); modBtns[key] = b; }
        else if (label0 && label0.length > 1) b.classList.add('wide');
        b.addEventListener('pointerdown', (e) => { e.preventDefault(); press(entry); });
        r.appendChild(b);
      }
      kbRoot.appendChild(r);
    }
    paintMods();
    paintCase();
  }

  function paintMods() {
    if (modBtns['shift']) modBtns['shift'].classList.toggle('active', shiftLatch);
    if (modBtns['capslock']) modBtns['capslock'].classList.toggle('active', capsOn);
    for (const m of ['ctrl', 'alt', 'win'])
      if (modBtns[m]) modBtns[m].classList.toggle('active', mods.has(m));
  }
  function paintCase() {                        // 大写（Caps 或 Shift 锁存）→ 字母显示大写
    const up = capsOn || shiftLatch;
    for (const b of letterBtns) b.textContent = up ? b.dataset.key.toUpperCase() : b.dataset.key;
  }

  function press(entry) {
    const [key, , , sym] = entry;
    if (key === 'shift') { shiftLatch = !shiftLatch; paintMods(); paintCase(); return; }
    if (key === 'capslock') {
      // 真实 CapsLock 语义：点亮/熄灭的是远端大写灯。先把键发给远端，本地乐观翻转，
      // 稍后服务端 {t:'caps'} 回传真实灯态做最终校准（远端被程序改灯也能纠正显示）
      Net.send({ t: 'key', key: 'capslock', d: 'tap' });
      capsOn = !capsOn; paintMods(); paintCase();
      if (navigator.vibrate) navigator.vibrate(6);
      return;
    }
    if (key === 'ctrl' || key === 'alt' || key === 'win') {
      mods.has(key) ? mods.delete(key) : mods.add(key);
      paintMods(); return;
    }
    if (key === 'space') { sendCombo(['space'], false); return; }
    // 大写态现在真实存在于远端：字母键不再叠加 shift（否则 shift 会把大写翻回小写）；
    // 数字/符号键 shift 才能出上档符号，与远端 caps 无关，照常按 caps/shift 锁存加 shift
    const isLetter = /^[a-z]$/.test(key);
    const wantShift = shiftLatch || (!isLetter && sym && capsOn);
    sendCombo([key], wantShift);
  }

  function sendCombo(keyList, withShift) {
    const keys = [];
    if (mods.has('ctrl')) keys.push('ctrl');
    if (mods.has('alt')) keys.push('alt');
    if (mods.has('win')) keys.push('win');
    if (withShift) keys.push('shift');
    keys.push(...keyList);
    if (keys.length === 1) Net.send({ t: 'key', key: keys[0], d: 'tap' });
    else Net.send({ t: 'combo', keys });
    shiftLatch = false; mods.clear();
    paintMods();
    paintCase();
    if (navigator.vibrate) navigator.vibrate(6);
  }

  buildKB();

  /* ================= 实体键盘转发（桌面浏览器） ================= */
  const NAMED = {
    Escape: 'esc', Tab: 'tab', Enter: 'enter', Backspace: 'backspace', Delete: 'delete',
    Insert: 'insert', Home: 'home', End: 'end', PageUp: 'pgup', PageDown: 'pgdn',
    ArrowUp: 'up', ArrowDown: 'down', ArrowLeft: 'left', ArrowRight: 'right',
    ' ': 'space', Control: 'ctrl', Alt: 'alt', Shift: 'shift', Meta: 'win',
    CapsLock: 'capslock',
  };
  const CODE_BASE = {   // Shift 状态下用物理位置还原未移位键名（组合键发给宿主，由宿主移位）
    Digit0: '0', Digit1: '1', Digit2: '2', Digit3: '3', Digit4: '4', Digit5: '5',
    Digit6: '6', Digit7: '7', Digit8: '8', Digit9: '9',
    Minus: '-', Equal: '=', BracketLeft: '[', BracketRight: ']', Backslash: '\\',
    Semicolon: ';', Quote: "'", Backquote: '`', Comma: ',', Period: '.', Slash: '/',
  };
  function physKey(e) {
    if (NAMED[e.key]) return NAMED[e.key];
    if (/^F\d{1,2}$/.test(e.key)) return e.key.toLowerCase();
    if (e.key.length === 1) {
      const c = e.key.toLowerCase();
      if (/[a-z0-9]/.test(c) || !e.shiftKey) return c;
      return CODE_BASE[e.code] || c;
    }
    return null;
  }
  function typingHere(el) {
    return el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable);
  }
  const pressed = new Set();
  function releaseAllPhysical() {              // 切走视图时全部松键，防粘滞
    for (const k of pressed) Net.send({ t: 'key', key: k, d: 'up' });
    pressed.clear();
  }
  window.addEventListener('keydown', (e) => {
    if (!active) return;                       // 只在键盘视图激活时转发实体键，避免与画面模式重复
    if (typingHere(e.target)) return;
    if (['F5', 'F12'].includes(e.key) || (e.ctrlKey && /r|w/i.test(e.key))) return; // 放行浏览器关键键
    const k = physKey(e);
    if (!k) return;
    e.preventDefault();
    if (e.repeat || pressed.has(k)) return;
    pressed.add(k);
    Net.send({ t: 'key', key: k, d: 'down' });
  });
  window.addEventListener('keyup', (e) => {
    const k = physKey(e);
    if (!k || !pressed.has(k)) return;
    pressed.delete(k);
    Net.send({ t: 'key', key: k, d: 'up' });
  });
  window.addEventListener('blur', () => { if (active) releaseAllPhysical(); });

  /* ================= 供 app.js 视图切换调用 ================= */
  window.Kb = {
    setActive(v) {
      v = !!v;
      if (active && !v) releaseAllPhysical();
      active = v;
    },
    reset() {                                  // 进入键盘视图：清残留手势/锁存，防跨模式串状态
      pointers.clear(); gesture = null;
      mvAccX = 0; mvAccY = 0;
      shiftLatch = false; mods.clear(); paintMods();
    },
  };
})();
