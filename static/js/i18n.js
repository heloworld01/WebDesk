/* 全局 i18n：字典 + 语言解析 + 静态文案替换 + 切换事件。
   必须在其它脚本之前加载（widgets.js 等在建模块时常量里就调用 t()）。
   暴露：window.I18N / t(key, params) / wdLang() / wdSetLang(lang) / wdApplyI18n()
   语言解析优先级：localStorage('wd_lang') > URL ?lang=zh|en > navigator.language(zh*→zh, 否则 en)
   文案刷新约定：wdSetLang → 替换 [data-i18n*] 静态节点 → document.dispatchEvent(new Event('wd:lang'))
   （动态渲染的模块各自监听 'wd:lang' 重绘） */
(function () {
  const I18N = {
    zh: {
      /* ---------- 通用状态 ---------- */
      st_connecting: '连接中…',
      st_connected: '已连接',
      st_busy: '已有设备正在使用远程桌面，每 5 秒自动重试…',
      st_busy_kb: '已有设备在用，每 5 秒重试…',
      st_closed_retry: '连接断开，自动重连中…',
      st_closed_fail: '连接断开，已重试 {n} 次仍失败 — 可下拉/按键刷新本页面重连',
      kb_closed_retry: '已断开，自动重连中…',
      kb_closed_fail: '已断开，已重试 {n} 次 — 请刷新本页面重连',
      st_auth_ok: '验证成功',
      srv_bad_pin: '验证码错误',
      srv_auth_expired: '会话未验证或已过期，请先通过验证码后重试',
      srv_busy: '已有其他设备正在连接',
      srv_bad_offer: 'offer 格式错误',
      srv_no_loopback: '未找到系统音频回环设备',
      st_need_code: '请在弹出框输入验证码',
      ask_code: '请输入验证码：\n（本机控制面板上可查看；扫码打开则自动免输）',

      /* ---------- 语言切换按钮 ---------- */
      lang_other: 'EN',            // 按钮永远显示"另一种语言"的名字
      lang_tip: '切换语言 / Switch language',

      /* ---------- 主界面按钮 ---------- */
      btn_keyboard_mode: '键盘模式',
      tip_keyboard_mode: '切换到键盘触摸板（同页切换，不占画面带宽；桌面浏览器还能直接敲实体键盘）',
      guard_off: '防误触:关',
      guard_on: '防误触:开',
      tip_guard: '开启后屏蔽一切键鼠注入，只看不碰',
      audio_off: '音频:关',
      audio_on: '音频:开',
      audio_busy: '音频…',
      tip_audio: '电脑系统声音：点一下开启（黄色=连接中，蓝色=已开启），再点关闭',
      btn_edit: '编辑',
      tip_edit: '编辑控件',
      btn_full: '全屏',
      tip_full: '全屏',
      zoom_in: '放大',
      zoom_out: '缩小',
      zoom_reset: '还原',
      tip_zoom_in: '放大',
      tip_zoom_out: '缩小',
      tip_zoom_reset: '还原',

      /* ---------- 主界面提示 ---------- */
      toast_jpeg: '已切换 JPEG 模式',
      toast_rtc_ok: 'WebRTC/H.264 已连接',
      toast_rtc_lost: 'WebRTC 中断，已回退 JPEG',
      toast_rtc_noframe: 'WebRTC 无画面，已回退 JPEG',
      toast_rtc_fail: 'WebRTC 失败({err})，使用 JPEG',
      toast_audio_nopin: '验证码尚未验证通过，完成验证后再点开「音频」',
      toast_audio_cancel: '音频连接已取消，请再点一次「音频」',
      toast_audio_on: '系统音频已开启',
      toast_audio_off: '系统音频已关闭',
      toast_audio_stage: '连接在协商中被中断（{stage}），再点一次「音频」试试',
      toast_audio_fail: '音频开启失败: {err}',
      toast_guard_on: '防误触已开：屏幕点按/鼠标/按钮已屏蔽，摇杆方向键仍可用',
      toast_guard_off: '鼠标控制已开启',
      toast_fullscreen: '已进入全屏（地址栏隐藏）',
      toast_fullscreen_no: '此浏览器不支持网页全屏。iPhone/iPad：Safari 分享 → “添加到主屏幕”，从主屏幕图标打开即全屏无地址栏',
      toast_zoom_reset: '画面已还原',
      toast_edit_hint: '拖动控件摆放位置，点控件改属性',
      title_kb: 'WebDesk · 键盘',

      /* ---------- 编辑工具栏 / 属性弹窗 ---------- */
      add_button: '＋按钮',
      add_stick: '＋摇杆',
      done_edit: '完成',
      prop_title: '控件属性',
      prop_del: '删除',
      prop_cancel: '取消',
      prop_ok: '确定',
      prop_type: '类型',
      prop_kind_mouse: '鼠标模式（移动光标）',
      prop_kind_keys: '按键模式（如 WASD）',
      prop_stick_size: '摇杆大小(px)',
      prop_speed: '鼠标速度（鼠标模式）',
      prop_keys: '按键绑定（按键模式）',
      prop_label: '标签',
      prop_cur: '当前：{desc}',
      prop_legacy_hint: '（点选新动作可替换）',
      prop_bad: '属性有误: {err}',
      act_hold: '按住 {key}（松开时自动抬起）',
      act_combo: '组合键 {keys}（历史动作，保持不变）',
      act_mouse_dbl: '鼠标双击（{btn}）',
      act_mouse_click: '鼠标点击（{btn}）',
      act_text: '文本输入（历史动作，保持不变）',
      act_macro: '宏（历史动作，保持不变）',
      act_key: '单击按键 {key}',
      mode_tap: '按下即松',
      mode_hold: '按住(松手抬起)',
      mk_left: '🖱 左键',
      mk_right: '右键',
      mk_dbl: '双击',
      kb_space_disp: '空格',
      wb_key: '按键',

      /* ---------- 控件 ---------- */
      w_left: '左键',
      w_right: '右键',
      w_dbl: '双击',
      stick_keys: '方向键',
      stick_mouse: '鼠标',
      stick_switch: '切换',
      toast_stick_keys: '方向键模式不受防误触限制，可用',
      toast_stick_mouse: '防误触已开：鼠标模式摇杆已禁用',

      /* ---------- 键盘视图 ---------- */
      kb_touch_mode: '触屏模式',
      kb_tp_hint: '单指拖动移光标 · 轻点=左键 · 长按=右键 · 双指轻点=双击 · 双指拖动=滚轮',
      mr_left: '左键',
      mr_right: '右键',
      mr_dbl: '双击',
      mr_wu: '滚轮↑',
      mr_wd: '滚轮↓',
    },
    en: {
      /* ---------- 通用状态 ---------- */
      st_connecting: 'Connecting…',
      st_connected: 'Connected',
      st_busy: 'Another device is using the remote desktop — retrying every 5s…',
      st_busy_kb: 'Device busy — retrying every 5s…',
      st_closed_retry: 'Disconnected, reconnecting…',
      st_closed_fail: 'Disconnected after {n} retries — pull down or refresh this page to reconnect',
      kb_closed_retry: 'Disconnected, reconnecting…',
      kb_closed_fail: 'Disconnected after {n} retries — refresh this page to reconnect',
      st_auth_ok: 'Verified',
      srv_bad_pin: 'Wrong access code',
      srv_auth_expired: 'Session not verified or expired — pass the access code and retry',
      srv_busy: 'Another device is already connecting',
      srv_bad_offer: 'Malformed offer',
      srv_no_loopback: 'No system-audio loopback device found',
      st_need_code: 'Enter the code in the popup',
      ask_code: 'Enter the access code:\n(shown on the control panel on the PC; opening via QR code skips this)',

      /* ---------- 语言切换按钮 ---------- */
      lang_other: '中',
      lang_tip: '切换语言 / Switch language',

      /* ---------- 主界面按钮 ---------- */
      btn_keyboard_mode: 'Keyboard',
      tip_keyboard_mode: 'Switch to keyboard + touchpad (same page, no video bandwidth; desktop browsers can also type on the physical keyboard)',
      guard_off: 'Guard:Off',
      guard_on: 'Guard:On',
      tip_guard: 'When on, all keyboard/mouse input is blocked — view only',
      audio_off: 'Audio:Off',
      audio_on: 'Audio:On',
      audio_busy: 'Audio…',
      tip_audio: 'PC system sound: tap to start (yellow=connecting, blue=on), tap again to stop',
      btn_edit: 'Edit',
      tip_edit: 'Edit widgets',
      btn_full: 'Fullscreen',
      tip_full: 'Fullscreen',
      zoom_in: 'Zoom in',
      zoom_out: 'Zoom out',
      zoom_reset: 'Reset',
      tip_zoom_in: 'Zoom in',
      tip_zoom_out: 'Zoom out',
      tip_zoom_reset: 'Reset view',

      /* ---------- 主界面提示 ---------- */
      toast_jpeg: 'Switched to JPEG mode',
      toast_rtc_ok: 'WebRTC/H.264 connected',
      toast_rtc_lost: 'WebRTC lost — fell back to JPEG',
      toast_rtc_noframe: 'No WebRTC video — fell back to JPEG',
      toast_rtc_fail: 'WebRTC failed ({err}), using JPEG',
      toast_audio_nopin: 'Access code not verified yet — verify first, then tap “Audio”',
      toast_audio_cancel: 'Audio connection cancelled — tap “Audio” again',
      toast_audio_on: 'System audio on',
      toast_audio_off: 'System audio off',
      toast_audio_stage: 'Connection interrupted during negotiation ({stage}) — tap “Audio” again',
      toast_audio_fail: 'Audio failed: {err}',
      toast_guard_on: 'Guard on: taps / mouse / buttons are blocked; stick arrow keys still work',
      toast_guard_off: 'Mouse control on',
      toast_fullscreen: 'Fullscreen (address bar hidden)',
      toast_fullscreen_no: 'This browser cannot fullscreen the page. iPhone/iPad: Safari → Share → “Add to Home Screen”, then open from the home-screen icon for a full-screen page without the address bar',
      toast_zoom_reset: 'View reset',
      toast_edit_hint: 'Drag widgets to position them; tap a widget to edit its properties',
      title_kb: 'WebDesk · Keyboard',

      /* ---------- 编辑工具栏 / 属性弹窗 ---------- */
      add_button: '+ Button',
      add_stick: '+ Stick',
      done_edit: 'Done',
      prop_title: 'Widget properties',
      prop_del: 'Delete',
      prop_cancel: 'Cancel',
      prop_ok: 'OK',
      prop_type: 'Type',
      prop_kind_mouse: 'Mouse mode (moves cursor)',
      prop_kind_keys: 'Key mode (e.g. WASD)',
      prop_stick_size: 'Stick size (px)',
      prop_speed: 'Mouse speed (mouse mode)',
      prop_keys: 'Key bindings (key mode)',
      prop_label: 'Label',
      prop_cur: 'Current: {desc}',
      prop_legacy_hint: ' (pick a new action to replace it)',
      prop_bad: 'Invalid properties: {err}',
      act_hold: 'Hold {key} (released automatically)',
      act_combo: 'Combo {keys} (legacy action, kept as is)',
      act_mouse_dbl: 'Mouse double-click ({btn})',
      act_mouse_click: 'Mouse click ({btn})',
      act_text: 'Text input (legacy action, kept as is)',
      act_macro: 'Macro (legacy action, kept as is)',
      act_key: 'Tap key {key}',
      mode_tap: 'Tap',
      mode_hold: 'Hold (release on lift)',
      mk_left: '🖱 Left',
      mk_right: 'Right',
      mk_dbl: 'Double',
      kb_space_disp: 'Space',
      wb_key: 'Key',

      /* ---------- 控件 ---------- */
      w_left: 'Left',
      w_right: 'Right',
      w_dbl: 'Double',
      stick_keys: 'Arrows',
      stick_mouse: 'Mouse',
      stick_switch: 'Swap',
      toast_stick_keys: 'Arrow-key mode is not blocked by Guard',
      toast_stick_mouse: 'Guard on: mouse-mode stick disabled',

      /* ---------- 键盘视图 ---------- */
      kb_touch_mode: 'Touch mode',
      kb_tp_hint: 'One finger: move cursor · Tap: left click · Long press: right click · Two-finger tap: double-click · Two-finger drag: scroll',
      mr_left: 'Left',
      mr_right: 'Right',
      mr_dbl: 'Double',
      mr_wu: 'Wheel ↑',
      mr_wd: 'Wheel ↓',
    },
  };

  function valid(l) { return l === 'zh' || l === 'en'; }

  function detect() {
    try {
      const s = localStorage.getItem('wd_lang');
      if (valid(s)) return s;
    } catch (e) { /* 隐私模式等：忽略 */ }
    try {
      const q = new URLSearchParams(location.search).get('lang');
      if (valid(q)) return q;
    } catch (e) { /* 老浏览器忽略 */ }
    return String(navigator.language || 'zh').toLowerCase().indexOf('zh') === 0 ? 'zh' : 'en';
  }

  let lang = detect();

  function t(key, params) {
    let s = I18N[lang] && I18N[lang][key];
    if (s === undefined) s = I18N.zh[key];
    if (s === undefined) s = key;                    // 缺失键回退：zh → 键名本身
    if (params) for (const k in params)
      s = s.split('{' + k + '}').join(String(params[k]));
    return s;
  }

  // 替换静态文案：data-i18n→textContent，data-i18n-title/placeholder/aria-label→对应属性
  function wdApplyI18n(root) {
    const scope = root || document;
    scope.querySelectorAll('[data-i18n]').forEach((el) => { el.textContent = t(el.getAttribute('data-i18n')); });
    scope.querySelectorAll('[data-i18n-title]').forEach((el) => { el.title = t(el.getAttribute('data-i18n-title')); });
    scope.querySelectorAll('[data-i18n-placeholder]').forEach((el) => { el.placeholder = t(el.getAttribute('data-i18n-placeholder')); });
    scope.querySelectorAll('[data-i18n-aria]').forEach((el) => { el.setAttribute('aria-label', t(el.getAttribute('data-i18n-aria'))); });
    document.documentElement.lang = lang === 'zh' ? 'zh-CN' : 'en';
  }

  function wdSetLang(l) {
    if (!valid(l) || l === lang) return;
    lang = l;
    try { localStorage.setItem('wd_lang', l); } catch (e) { /* 忽略 */ }
    wdApplyI18n();
    document.dispatchEvent(new Event('wd:lang'));   // 动态渲染的模块自行重绘
  }

  window.I18N = I18N;
  window.t = t;
  window.wdLang = () => lang;
  window.wdSetLang = wdSetLang;
  window.wdApplyI18n = wdApplyI18n;

  if (document.readyState === 'loading')
    document.addEventListener('DOMContentLoaded', wdApplyI18n);
  else wdApplyI18n();

  // 语言按钮：全局代理（登录前/键盘视图/主界面共用同一行为）
  document.addEventListener('click', (e) => {
    const b = e.target.closest && e.target.closest('.wd-lang-btn');
    if (b) wdSetLang(lang === 'zh' ? 'en' : 'zh');
  });
})();
