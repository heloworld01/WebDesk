/* 主控制器：画面渲染、触控手势、模式切换、设置、控件编辑 UI */
(function () {
  const $ = (s) => document.querySelector(s);
  const canvas = $('#screen'), ctx = canvas.getContext('2d');
  const video = $('#screen-video');
  const statusEl = $('#status');
  let enginePref = localStorage.getItem('wd_engine') || 'auto';  // auto|webrtc|jpeg
  let pc = null;
  const screenEl = () => (video.srcObject && video.style.display !== 'none') ? video : canvas;

  const S = {
    longPress: localStorage.getItem('wd_lp') !== '0',
    inputOn: localStorage.getItem('wd_input') !== '0',   // 鼠标/输入控制总开关
    zoom: 1, panX: 0, panY: 0,                           // 画面级缩放（不影响控件）
    screenW: 0, screenH: 0,
  };
  Net.blocked = !S.inputOn;

  /* ---------- 画面缩放 / 平移（只作用于 canvas/video，控件层不缩放） ---------- */
  function applyScreen() {
    const t = `translate(calc(-50% + ${S.panX}px), calc(-50% + ${S.panY}px)) scale(${S.zoom})`;
    for (const el of [canvas, video]) el.style.transform = t;
  }
  function clampPan() {
    const w = parseFloat(canvas.style.width) || window.innerWidth;
    const h = parseFloat(canvas.style.height) || window.innerHeight;
    const mx = Math.max(0, (w * S.zoom - window.innerWidth) / 2);
    const my = Math.max(0, (h * S.zoom - window.innerHeight) / 2);
    S.panX = Math.min(mx, Math.max(-mx, S.panX));
    S.panY = Math.min(my, Math.max(-my, S.panY));
  }
  function setZoom(z) {
    S.zoom = Math.min(4, Math.max(1, Math.round(z * 100) / 100));
    if (S.zoom === 1) { S.panX = 0; S.panY = 0; }
    window.WD_REL = 1 / S.zoom;   // 供摇杆等控件换算相对位移
    clampPan(); applyScreen();
  }

  /* ================= 画面渲染 ================= */
  let pendingFrame = null, drawing = false;
  Net.on('frame', (buf) => {
    pendingFrame = buf;
    if (!drawing) drawNext();
  });
  function drawNext() {
    if (!pendingFrame) { drawing = false; return; }
    drawing = true;
    const buf = pendingFrame; pendingFrame = null;
    createImageBitmap(new Blob([buf], { type: 'image/jpeg' }))
      .then((bmp) => {
        if (canvas.width !== bmp.width) canvas.width = bmp.width;
        if (canvas.height !== bmp.height) canvas.height = bmp.height;
        fitCanvas();
        ctx.drawImage(bmp, 0, 0);
        bmp.close();
        drawing = false;
        drawNext();
      })
      .catch(() => { drawing = false; });
  }
  function fitCanvas() {
    // canvas 与 video 用同一桌面宽高比，居中放置
    const ar = S.screenW / S.screenH || 16 / 9;
    let w = window.innerWidth, h = w / ar;
    if (h > window.innerHeight) { h = window.innerHeight; w = h * ar; }
    for (const el of [canvas, video]) {
      el.style.width = w + 'px'; el.style.height = h + 'px';
    }
    clampPan(); applyScreen();
  }
  window.addEventListener('resize', fitCanvas);
  if (window.visualViewport) window.visualViewport.addEventListener('resize', fitCanvas);

  // 阻止 iOS/Android 浏览器双击与双指缩放页面（桌面画面内的双击不受影响）
  window.addEventListener('dblclick', (e) => e.preventDefault());
  for (const g of ['gesturestart', 'gesturechange', 'gestureend'])
    document.addEventListener(g, (e) => e.preventDefault());
  // Windows 触控板的「双指双击缩放 / 捏合缩放」在 Chrome/Edge 里以 Ctrl+wheel 下发，
  // 单独拦这条路（只拦带 Ctrl 的滚轮，普通滚轮不受影响；须 passive:false 才能 preventDefault）
  window.addEventListener('wheel', (e) => { if (e.ctrlKey) e.preventDefault(); }, { passive: false });

  /* ================= 触控手势（直触方案：指哪打哪，canvas/video 共用） ==========
     单指拖动=光标跟手指走  单指轻点=该处左键  长按=该处右键
     双指拖动=滚轮    双指轻点=双击（两指中心）  放大/关闸时双指=平移画面
     第二指在宽限期内落下 = 判定为手势意图，单指的点击/长按一律不触发 */
  const pointers = new Map();     // pid -> {x,y,x0,y0,t0,moved,lpTimer,grace}
  let gesture = null;             // 多指手势状态

  function attachScreen(el) {
  // 屏幕坐标 → 桌面绝对坐标（getBoundingClientRect 已含 zoom/pan 变换，逆映射即得）
  function mapToDesktop(cx, cy) {
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height || !S.screenW) return null;
    return {
      x: Math.min(S.screenW - 1, Math.max(0, (cx - r.left) / r.width * S.screenW)),
      y: Math.min(S.screenH - 1, Math.max(0, (cy - r.top) / r.height * S.screenH)),
    };
  }
  // 直触点击：move+click 打包成一条消息（服务端 executor 内原子执行，无乱序）
  function directClick(btn, dbl, cx, cy) {
    const d = mapToDesktop(cx, cy);
    if (!d) { Net.send({ t: 'click', btn: btn, dbl: !!dbl }); return; }
    est = { x: d.x, y: d.y };
    drawDot();
    Net.send({ t: 'click', btn: btn, dbl: !!dbl, x: Math.round(d.x), y: Math.round(d.y) });
  }

  el.addEventListener('pointerdown', (e) => {
    e.preventDefault();
    el.setPointerCapture(e.pointerId);
    const p = { x: e.clientX, y: e.clientY, x0: e.clientX, y0: e.clientY, t0: Date.now(), moved: 0 };
    pointers.set(e.pointerId, p);

    if (pointers.size === 2) {
      // 双指手势：放大状态=平移画面；否则=滚动滚轮（双指轻点=双击）
      cancelLongPress();
      const mid = midPoint();
      if (S.zoom > 1) {
        gesture = { type: 'pan', mx0: mid.x, my0: mid.y, px0: S.panX, py0: S.panY };
      } else {
        gesture = { type: 'scroll', y0: mid.y, x0: mid.x, acc: 0, tap: true, t0: Date.now() };
      }
    } else if (pointers.size === 1 && !gesture) {
      if (!S.inputOn) {
        // 输入闸关闭：单指拖动=平移画面（放大后浏览画面区域）
        gesture = { type: 'pan', mx0: e.clientX, my0: e.clientY, px0: S.panX, py0: S.panY };
        return;
      }
      if (S.longPress) {
        // 长按（未移动）= 右键：直触=在手指处，触摸板=在光标处。
        // 先经过一段"多指宽限期"：期间落下第二指 = 判定为双指手势意图（平移/滚轮），
        // 长按右键彻底取消——杜绝"放大后双指拖画面，起手第一指误触发右键"。
        const armLP = () => {
          p.lpTimer = setTimeout(() => {
            if (p.moved < 10) {
              directClick('right', false, p.x0, p.y0);
              p.consumed = true;
              if (navigator.vibrate) navigator.vibrate(20);
            }
          }, 520);
        };
        p.grace = setTimeout(() => { p.grace = null; armLP(); }, 260);
      }
    }
  });

  function midPoint() {
    const pts = [...pointers.values()];
    let x = 0, y = 0;
    for (const q of pts) { x += q.x; y += q.y; }
    const n = Math.max(1, pts.length);
    return { x: x / n, y: y / n };
  }

  function cancelLongPress() {
    // 第二指落下：清掉所有在途的宽限/长按计时器，并把这批手指标记为手势意图
    //（手势意图的手指即使后来单独抬起，也不再走"轻点=左键"，鼠标只认单指点击）
    for (const p of pointers.values()) {
      if (p.grace) { clearTimeout(p.grace); p.grace = null; }
      if (p.lpTimer) { clearTimeout(p.lpTimer); p.lpTimer = null; }
      p.gestureIntent = true;
    }
  }

  el.addEventListener('pointermove', (e) => {
    const p = pointers.get(e.pointerId);
    if (!p) return;
    e.preventDefault();
    const dx = e.clientX - p.x, dy = e.clientY - p.y;
    p.moved += Math.abs(dx) + Math.abs(dy);
    p.x = e.clientX; p.y = e.clientY;

    if (gesture && gesture.type === 'pan' && pointers.size >= 1) {
      const mid = midPoint();
      S.panX = gesture.px0 + (mid.x - gesture.mx0);
      S.panY = gesture.py0 + (mid.y - gesture.my0);
      clampPan(); applyScreen();
      return;
    }
    if (gesture && gesture.type === 'scroll' && pointers.size >= 2) {
      const mid = midPoint();
      gesture.acc += (gesture.y0 - mid.y);
      if (Math.abs(gesture.acc) > 18) {
        Net.send({ t: 'wheel', dy: Math.sign(gesture.acc) * Math.min(5, Math.abs(gesture.acc) / 12) });
        gesture.acc = 0;
      }
      if (p.moved > 10) gesture.tap = false;
      return;
    }
    if (gesture) return;
    if (p.moved > 10 && p.lpTimer) { clearTimeout(p.lpTimer); p.lpTimer = null; }

    // 拖动=光标直接跟手指走（绝对坐标映射，与缩放平移无关，放大后依然指哪打哪）
    const d = mapToDesktop(e.clientX, e.clientY);
    if (d) {
      est = { x: d.x, y: d.y };
      Net.send({ t: 'mmove', x: Math.round(d.x), y: Math.round(d.y) });
      drawDot();
    }
  });

  function endPointer(e) {
    const p = pointers.get(e.pointerId);
    if (!p) return;
    pointers.delete(e.pointerId);
    if (p.lpTimer) clearTimeout(p.lpTimer);
    if (p.grace) { clearTimeout(p.grace); p.grace = null; }   // 宽限期内抬起：防止抬起后才误武装长按

    if (gesture) {
      if (pointers.size === 0) {
        const quick = Date.now() - gesture.t0 < 350;
        if (gesture.type === 'scroll' && gesture.tap && quick) {
          directClick('left', true, gesture.x0, gesture.y0);   // 双指轻点 = 双击（两指中心处）
        }
        gesture = null;
      }
      return;
    }

    // 轻点 = 左键，点哪打哪（长按已触发右键、或曾作为双指手势起手指标记则跳过）
    const quick = Date.now() - p.t0 < 350;
    if (!p.consumed && !p.gestureIntent && p.moved < 10 && quick) {
      directClick('left', false, p.x0, p.y0);
    }
  }
  el.addEventListener('pointerup', endPointer);
  el.addEventListener('pointercancel', endPointer);
  el.addEventListener('contextmenu', (e) => e.preventDefault());
  }
  attachScreen(canvas);
  attachScreen(video);

  /* ================= 状态提示 ================= */
  let statusTimer = null;
  window.toast = toast;   // 供 widgets.js 角标切换提示复用
  function toast(msg, ms) {
    statusEl.textContent = msg;
    statusEl.classList.remove('fade');
    clearTimeout(statusTimer);
    statusTimer = setTimeout(() => statusEl.classList.add('fade'), ms || 1800);
  }

  /* ================= 网络事件 ================= */
  function askCode() {
    const c = prompt('请输入验证码：\n（本机控制面板上可查看；扫码打开则自动免输）');
    if (c != null && c !== '') { localStorage.setItem('wd_pin', c.trim()); Net.send({ t: 'auth', pin: c.trim() }); }
  }
  function maybeStartRtc() {
    if (view === 'kb') return;    // 键盘视图不拉视频（含鉴权成功后的自动升级）
    if (S.webrtcAvailable && enginePref !== 'jpeg' && !pc) startWebRTC();
  }
  Net.on('hello', (m) => {
    S.screenW = m.w; S.screenH = m.h;
    if (!canvas.width) { canvas.width = m.w; canvas.height = m.h; fitCanvas(); }
    if (m.need_auth && !localStorage.getItem('wd_pin')) askCode();
    S.webrtcAvailable = !!m.webrtc;
    // 面板设置的「登录后默认视图」：本次页面首次 hello 时应用一次；
    // 此后用户手动切换/断线重连都不再覆盖用户当前的选择
    if (!viewApplied && location.pathname !== '/kb') {
      viewApplied = true;
      if (m.default_view === 'kb') setView('kb');
    }
    // 不在此处开 RTC：sid 要等鉴权通过的 ok 才下发，ok 处理器里再开
  });
  // 服务端下发的控件布局（自动保存/多设备共享）
  Net.on('layout', (m) => Widgets.applyServerLayout(m.layout));
  // 单连接闸门：已有设备在用 → 提示并慢速等待空位
  Net.on('busy', () => toast('已有设备正在使用远程桌面，每 5 秒自动重试…', 4200));

  /* ================= WebRTC (H.264) ================= */
  function useVideo(on) {
    video.style.display = on ? 'block' : 'none';
    canvas.style.display = on ? 'none' : 'block';
  }
  function stopWebRTC(silent) {
    if (pc) { try { pc.close(); } catch {} pc = null; }
    video.srcObject = null;
    useVideo(false);
    Net.send({ t: 'stream', mode: 'jpeg' });
    if (!silent) toast('已切换 JPEG 模式');
  }
  let rtcTries = 0;
  async function startWebRTC() {
    if (pc || !Net.sid) return;   // sid 随鉴权 ok 才下发；ok 处理器会再次触发
    try {
      pc = new RTCPeerConnection();
      pc.addTransceiver('video', { direction: 'recvonly' });
      let gotFrame = false;
      pc.ontrack = (e) => {
        video.srcObject = e.streams[0];
        video.play().catch(() => {});
      };
      video.onplaying = () => {
        if (gotFrame) return; gotFrame = true;
        useVideo(true); rtcTries = 0;
        Net.send({ t: 'stream', mode: 'webrtc' });
        toast('WebRTC/H.264 已连接');
      };
      pc.onconnectionstatechange = () => {
        if (!pc) return;
        if (pc.connectionState === 'failed' || pc.connectionState === 'disconnected') {
          stopWebRTC(true);
          if (enginePref !== 'jpeg' && rtcTries < 2) { rtcTries++; setTimeout(startWebRTC, 1200); }
          else toast('WebRTC 中断，已回退 JPEG');
        }
      };
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      const res = await fetch('/rtc/offer?sid=' + encodeURIComponent(Net.sid || ''), {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(pc.localDescription),
      });
      const ans = await res.json().catch(() => ({}));
      if (!res.ok || ans.error) throw new Error(ans.error || ('HTTP ' + res.status));
      await pc.setRemoteDescription(ans);
      // 看门狗：6 秒无画面自动回退
      setTimeout(() => {
        if (pc && !gotFrame) { stopWebRTC(true); toast('WebRTC 无画面，已回退 JPEG'); }
      }, 6000);
    } catch (e) {
      stopWebRTC(true);
      toast('WebRTC 失败(' + e.message + ')，使用 JPEG');
    }
  }

  /* ================= 系统音频（独立 WebRTC 连接，默认关闭） ================= */
  // 音频开关 = 建立/拆除一条只收音频的 RTCPeerConnection，与视频通道互不影响
  let audioPc = null;
  const audioEl = $('#remote-audio');
  async function startAudio() {
    if (audioPc) return;
    if (!Net.sid) { toast('验证码尚未验证通过，完成验证后再点开「音频」'); return; }
    const btn = $('#btn-audio');
    // 立即反馈：协商（含 ICE 收集）最长约 3 秒，期间按钮呈黄色"连接中"，成功后转蓝
    btn.classList.add('wait');
    btn.textContent = '音频…';
    let stage = 'create';
    try {
      const apc = new RTCPeerConnection();
      audioPc = apc;
      const cancelled = () => audioPc !== apc || apc.connectionState === 'closed';
      apc.addTransceiver('audio', { direction: 'recvonly' });
      apc.ontrack = (e) => {
        audioEl.srcObject = e.streams[0];
        audioEl.play().catch(() => {});
      };
      apc.onconnectionstatechange = () => {
        if (apc.connectionState === 'failed' || apc.connectionState === 'closed') stopAudio();
      };
      stage = 'offer';
      const offer = await apc.createOffer();
      await apc.setLocalDescription(offer);
      stage = 'signal';   // 服务端要等 ICE 收集（最长 ~3s），期间可能被关闭/二次点击取消
      const res = await fetch('/rtc/offer?sid=' + encodeURIComponent(Net.sid || ''), {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(apc.localDescription),
      });
      const ans = await res.json().catch(() => ({}));
      if (!res.ok || ans.error) throw new Error(ans.error || ('HTTP ' + res.status));
      if (cancelled()) { toast('音频连接已取消，请再点一次「音频」'); return; }
      stage = 'answer';
      await apc.setRemoteDescription(ans);
      btn.classList.remove('wait');
      btn.classList.add('on');
      btn.textContent = '音频:开';
      toast('系统音频已开启');
    } catch (e) {
      stopAudio();
      const dom = e && e.name === 'InvalidStateError'
        ? '连接在协商中被中断（' + stage + '），再点一次「音频」试试' : e.message;
      toast('音频开启失败: ' + dom);
    }
  }
  function stopAudio() {
    if (audioPc) { try { audioPc.close(); } catch {} audioPc = null; }
    if (audioEl) { audioEl.srcObject = null; try { audioEl.pause(); } catch {} }
    const btn = $('#btn-audio');
    btn.classList.remove('on'); btn.classList.remove('wait');
    btn.textContent = '音频:关';
  }
  $('#btn-audio').addEventListener('click', () => {
    if (audioPc) { stopAudio(); toast('系统音频已关闭'); }
    else startAudio();
  });

  Net.on('open', () => {
    toast('已连接');
    if (view === 'kb') Net.send({ t: 'stream', mode: 'none' });   // 重连后保持键盘模式（不拉画面）
  });
  // 虚拟光标（mss 截屏不含系统光标，由服务端回传位置）
  const cursorDot = document.createElement('div');
  cursorDot.id = 'cursor-dot';
  document.body.appendChild(cursorDot);
  // 远程光标本地估计（桌面像素）：拖动时按整数增量推进，服务端 cur 流定期校准。
  // 拖动改发绝对坐标 mmove，避开 Windows"增强指针精度"对相对移动的加速漂移。
  let est = null;
  function drawDot() {
    if (!est || !S.screenW) return;
    const r = screenEl().getBoundingClientRect();
    cursorDot.style.left = (r.left + est.x / S.screenW * r.width) + 'px';
    cursorDot.style.top = (r.top + est.y / S.screenH * r.height) + 'px';
    cursorDot.style.display = 'block';
  }
  Net.on('cur', (m) => {
    if (!est || Math.abs(m.x - est.x) > 24 || Math.abs(m.y - est.y) > 24) {
      est = { x: m.x, y: m.y };                      // 偏差大（摇杆/远程操作）直接吸附
    } else {
      est.x += (m.x - est.x) * 0.4; est.y += (m.y - est.y) * 0.4;  // 小偏差平滑纠偏
    }
    drawDot();
  });
  setInterval(drawDot, 100);   // 平移/缩放画面时光标点跟随重画
  Net.on('close', (d) => {
    if (Net.paused) return;
    // 前两次安静重试；之后明示进度并告知可手动刷新（手机切后台/断网后偶尔需要）
    const n = (d && d.retry) || 1;
    toast(n <= 2 ? '连接断开，自动重连中…'
                 : `连接断开，已重试 ${n} 次仍失败 — 可下拉/按键刷新本页面重连`, 999999);
  });
  // 鉴权通过才真正开视频通道（sid 随 ok 到达）
  Net.on('ok', () => {
    toast('验证成功');
    if (view === 'kb') Net.send({ t: 'stream', mode: 'none' }, true);   // 握手期发的 mode 未鉴权会被丢，这里补发
    else maybeStartRtc();
  });
  Net.on('err', (m) => {
    toast(m.msg);
    if (String(m.msg || '').match(/验证码|PIN/)) { localStorage.removeItem('wd_pin'); askCode(); }
  });

  /* ================= 工具栏 ================= */
  // 输入总开关：关闭后画面照常刷新，但一切键鼠注入被丢弃（防误触）
  const btnInput = $('#btn-input');
  function refreshInput() {
    btnInput.classList.toggle('off', !S.inputOn);
    btnInput.textContent = S.inputOn ? '防误触:关' : '防误触:开';
    // 防误触开启时控件灰显禁用（编辑模式除外），点击直接穿透到画面平移
    document.body.classList.toggle('input-blocked', !S.inputOn);
  }
  btnInput.addEventListener('click', () => {
    S.inputOn = !S.inputOn;
    Net.blocked = !S.inputOn;
    localStorage.setItem('wd_input', S.inputOn ? '1' : '0');
    refreshInput();
    toast(S.inputOn ? '鼠标控制已开启' : '防误触已开：屏幕点按/鼠标/按钮已屏蔽，摇杆方向键仍可用');
  });
  refreshInput();

  /* ================= 视图切换：画面 ⇄ 键盘（同一页面、同一条连接） ================= */
  const kbView = $('#kbview');
  let view = 'remote';
  let viewApplied = false;        // 面板默认视图只在本次页面第一次 hello 时应用
  function setView(v) {
    if (v === view) return;
    view = v;
    const kbOn = v === 'kb';
    kbView.classList.toggle('hidden', !kbOn);
    document.body.classList.toggle('kb-mode', kbOn);
    if (kbOn) {
      stopAudio();
      if (pc) { try { pc.close(); } catch (e) {} pc = null; }   // 拆除视频通道，停止编码/带宽
      useVideo(false);
      Net.send({ t: 'stream', mode: 'none' });
      Kb.reset(); Kb.setActive(true);
      document.title = 'WebDesk · 键盘';
    } else {
      Kb.setActive(false);
      Net.send({ t: 'stream', mode: 'jpeg' });                   // 先走 JPEG，RTC 就绪后自动升级
      maybeStartRtc();
      document.title = 'WebDesk';
    }
    refreshInput();
  }
  $('#btn-kbpage').addEventListener('click', () => setView('kb'));
  $('#go-screen').addEventListener('click', (e) => { e.preventDefault(); setView('remote'); });

  $('#btn-full').addEventListener('click', async () => {
    if (document.fullscreenElement) { document.exitFullscreen(); return; }
    try {
      await document.documentElement.requestFullscreen({ navigationUI: 'hide' });
      try { await screen.orientation.lock('landscape'); } catch {}
      toast('已进入全屏（地址栏隐藏）');
    } catch {
      toast('此浏览器不支持网页全屏。iPhone/iPad：Safari 分享 → “添加到主屏幕”，从主屏幕图标打开即全屏无地址栏');
    }
  });
  document.addEventListener('fullscreenchange', () => setTimeout(fitCanvas, 120));

  /* ---------- 画面缩放按钮 ---------- */
  $('#zoom-in').addEventListener('click', () => setZoom(S.zoom * 1.3));
  $('#zoom-out').addEventListener('click', () => setZoom(S.zoom / 1.3));
  $('#zoom-reset').addEventListener('click', () => { setZoom(1); toast('画面已还原'); });

  /* ---------- 工具栏/缩放条闲置变淡（防误触），轻触任意处恢复 ---------- */
  const dimEls = [$('#toolbar'), $('#zoombar'), $('#btn-kbpage')];
  let dimTimer = null;
  function undim() {
    dimEls.forEach(el => el.classList.remove('dim'));
    clearTimeout(dimTimer);
    if (!document.body.classList.contains('editing'))
      dimTimer = setTimeout(() => dimEls.forEach(el => el.classList.add('dim')), 2600);
  }
  document.addEventListener('pointerdown', undim, true);
  undim();

  /* ================= 控件编辑 UI ================= */
  Widgets.init($('#controls'));

  $('#btn-edit').addEventListener('click', () => {
    const on = !Widgets.editing;
    Widgets.setEditing(on);
    $('#editbar').classList.toggle('hidden', !on);
    $('#btn-edit').classList.toggle('on', on);
    $('#controls').classList.toggle('hide-widgets', false);
    if (on) toast('拖动控件摆放位置，点控件改属性');
  });
  $('#done-edit').addEventListener('click', () => $('#btn-edit').click());

  $('#add-btn').addEventListener('click', () => {
    const w = Widgets.addWidget({ type: 'button', label: '按键', x: 0.5, y: 0.5, act: { t: 'key', key: 'a' } });
    EditUI.open(w);
  });
  $('#add-stick').addEventListener('click', () => {
    Widgets.addWidget({
      type: 'stick', kind: 'mouse', x: 0.5, y: 0.5, size: 120, speed: 14,
      keys: { up: 'up', down: 'down', left: 'left', right: 'right' },
    });
  });
  // 布局无需手动导入导出：每次编辑由 Widgets.save() 自动推送到服务端 layout.json

  /* ---------- 属性弹窗：点选式动作选择器（鼠标键位 + 虚拟键盘） ---------- */
  const KB_LAYOUT = [
    [['esc', 'Esc'], ['tab', 'Tab'], ['space', '空格'], ['enter', 'Enter'], ['backspace', '⌫'], ['del', 'Del']],
    [['pgup', 'PgUp'], ['pgdn', 'PgDn'], ['home', 'Home'], ['end', 'End'], ['win', 'Win'], ['printscreen', 'PrtSc']],
    ...['qwertyuiop', 'asdfghjkl', 'zxcvbnm'].map(r => r.split('').map(k => [k, k.toUpperCase()])),
    [['ctrl', 'Ctrl'], ['alt', 'Alt'], ['shift', 'Shift'], ['up', '↑'], ['down', '↓'], ['left', '←'], ['right', '→']],
    '1234567890'.split('').map(k => [k, k]),
    [['f1', 'F1'], ['f2', 'F2'], ['f3', 'F3'], ['f4', 'F4'], ['f5', 'F5'], ['f6', 'F6']],
    [['f7', 'F7'], ['f8', 'F8'], ['f9', 'F9'], ['f10', 'F10'], ['f11', 'F11'], ['f12', 'F12']],
  ];
  function describeAct(a) {
    switch (a.t) {
      case 'hold': return '按住 ' + a.key + '（松开时自动抬起）';
      case 'combo': return '组合键 ' + a.keys.join('+') + '（历史动作，保持不变）';
      case 'mouse': return '鼠标' + (a.dbl ? '双击' : '点击') + '（' + a.btn + '）';
      case 'text': return '文本输入（历史动作，保持不变）';
      case 'macro': return '宏（历史动作，保持不变）';
      default: return '单击按键 ' + a.key;
    }
  }

  /* ---------- 属性弹窗 ---------- */
  const EditUI = {
    cur: null,
    open(w) {
      this.cur = w;
      $('#prop-dialog').classList.remove('hidden');
      const f = $('#prop-fields');
      f.innerHTML = '';
      const field = (label, el) => {
        const l = document.createElement('label');
        l.append(label); l.appendChild(el); f.appendChild(l); return el;
      };
      const inp = (id, val, ph) => {
        const el = document.createElement('input');
        el.id = id; el.value = val ?? ''; if (ph) el.placeholder = ph; return el;
      };

      if (w.type === 'stick') {
        const kind = document.createElement('select'); kind.id = 'p-kind';
        kind.innerHTML = '<option value="mouse">鼠标模式（移动光标）</option><option value="keys">按键模式（如 WASD）</option>';
        kind.value = w.kind || 'mouse';
        field('类型', kind);
        field('摇杆大小(px)', inp('p-size', w.size || 120));
        const sp = inp('p-speed', w.speed || 14);
        field('鼠标速度（鼠标模式）', sp);
        const keysBox = document.createElement('div');
        keysBox.style.cssText = 'display:flex;gap:6px;flex-wrap:wrap';
        for (const dir of ['up', 'down', 'left', 'right']) {
          const el = inp('p-k-' + dir, (w.keys || {})[dir] || '', dir);
          el.style.width = '70px'; keysBox.appendChild(el);
        }
        field('按键绑定（按键模式）', keysBox);
        this._keysBox = keysBox;
      } else {
        // 简化：点选式动作选择器，不输入任何文字。不点选 = 保持原动作不变
        field('标签', inp('p-label', w.label));
        this.picked = null;
        this._curAct = w.act || { t: 'key', key: 'a' };
        this._mode = (this._curAct.t === 'hold') ? 'hold' : 'key';

        const prev = document.createElement('div');
        prev.className = 'pk-prev';
        f.appendChild(prev);
        const upd = () => {
          const a = this.picked || this._curAct;
          const legacy = !this.picked && ['combo', 'text', 'macro'].includes(a.t);
          prev.textContent = '当前：' + describeAct(a) + (legacy ? '（点选新动作可替换）' : '');
          const j = JSON.stringify(this.picked || this._curAct);
          for (const b of f.querySelectorAll('[data-key]'))
            b.classList.toggle('sel', j === b.dataset.key || j === b.dataset.hold);
        };

        const btn = (label, actA, actB) => {
          const b = document.createElement('button');
          b.type = 'button'; b.className = 'pk-btn'; b.textContent = label;
          b.dataset.key = JSON.stringify(actA);
          if (actB) b.dataset.hold = JSON.stringify(actB);
          b.addEventListener('click', () => {
            this.picked = (b.dataset.hold && this._mode === 'hold') ? JSON.parse(b.dataset.hold) : actA;
            if (navigator.vibrate) navigator.vibrate(6);
            upd();
          });
          return b;
        };
        const row = (parent) => { const r = document.createElement('div'); r.className = 'pk-row'; parent.appendChild(r); return r; };

        // 鼠标键位
        let r = row(f);
        for (const [n, a] of [['🖱 左键', { t: 'mouse', btn: 'left' }], ['右键', { t: 'mouse', btn: 'right' }], ['双击', { t: 'mouse', btn: 'left', dbl: true }]])
          r.appendChild(btn(n, a));

        // 单击 / 按住 模式
        const modeRow = row(f); modeRow.classList.add('pk-mode');
        for (const [m, name] of [['key', '按下即松'], ['hold', '按住(松手抬起)']]) {
          const b = document.createElement('button');
          b.type = 'button'; b.className = 'pk-btn'; b.textContent = name; b.dataset.mode = m;
          b.addEventListener('click', () => {
            this._mode = m;
            for (const x of modeRow.querySelectorAll('[data-mode]')) x.classList.toggle('sel', x.dataset.mode === m);
            // 已选中键时按新模式重设
            if (this.picked && ['key', 'hold'].includes(this.picked.t))
              this.picked = { t: m, key: this.picked.key };
            upd();
          });
          modeRow.appendChild(b);
        }
        const syncMode = () => { for (const x of modeRow.querySelectorAll('[data-mode]')) x.classList.toggle('sel', x.dataset.mode === this._mode); };

        // 虚拟键盘
        for (const kbRow of KB_LAYOUT) {
          r = row(f);
          for (const [val, disp] of kbRow)
            r.appendChild(btn(disp, { t: 'key', key: val }, { t: 'hold', key: val }));
        }
        syncMode(); upd();
      }
    },

    collect() {
      const w = this.cur, g = (id) => document.getElementById(id);
      if (w.type === 'stick') {
        w.kind = g('p-kind').value;
        w.size = +g('p-size').value || 120;
        w.speed = +g('p-speed').value || 14;
        w.keys = {
          up: g('p-k-up').value.trim() || 'w', down: g('p-k-down').value.trim() || 's',
          left: g('p-k-left').value.trim() || 'a', right: g('p-k-right').value.trim() || 'd',
        };
      } else {
        w.label = g('p-label').value || '·';
        // 未点选任何键位 = 保持原动作（含历史 combo/text/macro）
        if (this.picked) w.act = this.picked;
      }
    },
  };
  window.EditUI = EditUI;

  $('#prop-ok').addEventListener('click', () => {
    try { EditUI.collect(); } catch (e) { toast('属性有误: ' + e.message); return; }
    Widgets.save(); Widgets.render();
    $('#prop-dialog').classList.add('hidden');
  });
  $('#prop-cancel').addEventListener('click', () => $('#prop-dialog').classList.add('hidden'));
  $('#prop-del').addEventListener('click', () => {
    Widgets.remove(EditUI.cur.id);
    $('#prop-dialog').classList.add('hidden');
  });

  /* ================= 启动 ================= */
  Net.connect();
  fitCanvas();
  if (location.pathname === '/kb') setView('kb');   // /kb 深链：直接进键盘视图
})();
