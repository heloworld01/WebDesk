/* WebSocket 网络层：JSON 控制消息 + 二进制 JPEG 帧 */
(function () {
  // 控制面板的二维码可携带 ?pin=xxxx：自动存入本机并抹掉地址栏痕迹
  try {
    const u = new URL(location.href);
    const p = u.searchParams.get('pin');
    if (p) {
      localStorage.setItem('wd_pin', p);
      u.searchParams.delete('pin');
      history.replaceState(null, '', u.pathname + (u.search || '') + u.hash);
    }
  } catch (e) { /* 老浏览器忽略 */ }

  const Net = {
    ws: null,
    handlers: {},
    connected: false,
    needAuth: false,
    sid: null,        // hello 下发的会话令牌；/rtc/offer 必须携带
    busy: false,      // 服务端提示已有其他设备连接（重连退避 5s）

    // 同一事件允许多个订阅者（主控制器 + 键盘视图都在本页内）
    on(type, fn) { (this.handlers[type] || (this.handlers[type] = [])).push(fn); return this; },
    _emit(type, data) { const a = this.handlers[type]; if (a) for (const f of a) { try { f(data); } catch (e) { console.error(e); } } },

    retryN: 0,          // 连续失败次数（供界面提示“重试多次请刷新页面”）
    lastMsg: 0,         // 最近一次收到任何 WS 消息的时刻（保活看门狗用）
    connect() {
      if (this.ws && this.ws.readyState <= 1) return;   // 已有连接/在途尝试：忽略重复发起
      const proto = location.protocol === 'https:' ? 'wss' : 'ws';
      let ws;
      try { ws = new WebSocket(proto + '://' + location.host + '/ws'); }
      catch (e) { setTimeout(() => this.connect(), 3000); return; }
      this.ws = ws;
      ws.binaryType = 'arraybuffer';
      // 看门狗：手机休眠/断网时 WebSocket 可能长时间停在 CONNECTING（既不 open 也不
      // close，重连流程整体“冻结”）。8 秒还没就连上就主动掐掉，强制走重连循环。
      const guard = setTimeout(() => {
        if (ws.readyState === 0) { try { ws.close(); } catch (e) {} }
      }, 8000);

      ws.onopen = () => {
        clearTimeout(guard);
        this.connected = true;
        this.busy = false;
        this.retryN = 0;
        this.lastMsg = Date.now();
        const pin = localStorage.getItem('wd_pin') || '';
        if (this.needAuth || pin) ws.send(JSON.stringify({ t: 'auth', pin }));
        this._emit('open');
      };
      ws.onmessage = (e) => {
        this.lastMsg = Date.now();
        if (typeof e.data === 'string') {
          let m; try { m = JSON.parse(e.data); } catch { return; }
          // sid 只在鉴权通过（ok）后下发；hello/重连时一律清空，防止拿旧 sid 拉流
          if (m.t === 'hello') { this.needAuth = m.need_auth; this.sid = null; }
          if (m.t === 'ok' && m.sid) this.sid = m.sid;
          if (m.t === 'busy') { this.busy = true; this._emit('busy'); return; }
          this._emit(m.t, m);
        } else {
          this._emit('frame', e.data);
        }
      };
      ws.onclose = (e) => {
        clearTimeout(guard);
        this.connected = false;
        this.retryN++;
        this._emit('close', { retry: this.retryN });
        if (this.paused) return;   // 主动让出名额（如切到键盘页）：不自动重连
        // 自动重连：被占用时退避 5 秒，避免疯狂重试
        setTimeout(() => this.connect(), this.busy ? 5000 : 1200);
      };
      ws.onerror = () => { try { ws.close(); } catch {} };
    },

    // 主动让出/收回唯一连接名额：主页面点「键盘页」时 pause，用户回到本页面再 resume。
    // 手机浏览器会把后台标签页冻结，指望后台页自己释放不可靠，必须由即将打开新标签页的一方主动让位。
    paused: false,
    pause() {
      this.paused = true;
      if (this.ws) { try { this.ws.close(); } catch (e) {} }
    },
    resume() {
      if (!this.paused) return;
      this.paused = false;
      if (!this.connected) this.connect();
    },

    blocked: false,   // 输入总闸：true 时丢弃一切键鼠类消息（画面/配置照常）
    INPUT_TYPES: new Set(['mmove', 'mrel', 'mdown', 'mup', 'click', 'wheel', 'key', 'combo', 'text', 'macro']),

    // force=true 绕过输入闸（摇杆方向键模式：防误触屏蔽误触，但游戏方向键是有意的）
    send(obj, force) {
      if (this.blocked && !force && this.INPUT_TYPES.has(obj.t)) return;
      if (this.ws && this.ws.readyState === 1) this.ws.send(JSON.stringify(obj));
    },
  };
  window.Net = Net;

  // 静默看门狗：手机 Wi-Fi 休眠/漫游会制造「半开连接」——TCP 已死但浏览器不发
  // onclose，页面看似已连接却永远黑屏，只能手动刷新。鉴权通过后服务端每 3 秒
  // 下发 ping、光标移动时下发 cur，8 秒收不到任何消息即判定僵尸连接：主动掐断，
  // 走 onclose 的自动重连流程（等价于替用户刷新了一次页面）。
  setInterval(() => {
    const ws = Net.ws;
    if (!ws || Net.paused || !Net.sid) return;   // 未鉴权时服务端本就静默，不检测
    if (ws.readyState !== 1) return;
    if (Date.now() - Net.lastMsg > 8000) {
      Net.lastMsg = Date.now();                  // 防止 close 送达前重复触发
      try { ws.close(); } catch (e) {}
    }
  }, 2000);

  // 手机切回前台时立刻试一次：后台标签的定时器常被浏览器冻结/节流，
  // 回前台不等退避计时器，直接补一次连接（重复发起已由 connect() 内部去重）。
  document.addEventListener('visibilitychange', () => {
    if (document.hidden || Net.paused || Net.connected) return;
    if (Net.ws && Net.ws.readyState <= 1) { try { Net.ws.close(); } catch (e) {} }
    Net.connect();
  });
})();
