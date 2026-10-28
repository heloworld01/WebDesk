/* 键盘面板：特殊键 + 可自定义热键行 + 系统软键盘文本输入 */
(function () {
  const LS_HOT = 'wd_hotkeys';
  const DEFAULT_HOT = [
    { label: 'Ctrl+C', keys: ['ctrl', 'c'] },
    { label: 'Ctrl+V', keys: ['ctrl', 'v'] },
    { label: 'Ctrl+Z', keys: ['ctrl', 'z'] },
    { label: 'Ctrl+A', keys: ['ctrl', 'a'] },
    { label: 'Alt+F4', keys: ['alt', 'f4'] },
    { label: 'Win+D', keys: ['win', 'd'] },
  ];

  const SPECIAL = [
    ['Esc', 'esc'], ['Tab', 'tab'], ['Caps', 'capslock'], ['`', '`'], ['~', '~'],
    ['Shift', 'shift', true], ['Ctrl', 'ctrl', true], ['Alt', 'alt', true], ['Win', 'win'],
    ['←', 'left'], ['↑', 'up'], ['↓', 'down'], ['→', 'right'],
    ['PgUp', 'pgup'], ['PgDn', 'pgdn'], ['Home', 'home'], ['End', 'end'],
    ['Ins', 'insert'], ['Del', 'delete'], ['F1', 'f1'], ['F2', 'f2'], ['F5', 'f5'],
    ['Enter', 'enter'], ['Bksp', 'backspace'], ['Space', 'space'],
  ];

  const Keyboard = {
    root: null, textInput: null, visible: false,

    init() {
      this.root = document.getElementById('keyboard');
      this.textInput = document.getElementById('text-input');
      const sp = document.getElementById('special-keys');
      const hot = document.getElementById('hotkey-row');

      for (const item of SPECIAL) {
        const [label, key, hold] = item;
        const b = document.createElement('button');
        b.textContent = label;
        if (hold) {
          b.addEventListener('pointerdown', (e) => { e.preventDefault(); b.classList.add('hold'); Net.send({ t: 'key', key, d: 'down' }); });
          const up = (e) => { e.preventDefault(); b.classList.remove('hold'); Net.send({ t: 'key', key, d: 'up' }); };
          b.addEventListener('pointerup', up); b.addEventListener('pointercancel', up);
        } else {
          b.addEventListener('pointerdown', (e) => { e.preventDefault(); Net.send({ t: 'key', key, d: 'tap' }); });
        }
        sp.appendChild(b);
      }

      this.renderHot();

      // 系统软键盘：监听输入差异，逐字符发送
      let prev = '';
      this.textInput.addEventListener('focus', () => { prev = this.textInput.value = ''; });
      this.textInput.addEventListener('input', () => {
        const v = this.textInput.value;
        if (v.length > prev.length) {
          for (const ch of v.slice(prev.length)) Net.send({ t: 'text', s: ch });
        } else if (v.length < prev.length) {
          for (let i = 0; i < prev.length - v.length; i++) Net.send({ t: 'key', key: 'backspace', d: 'tap' });
        }
        prev = v;
        if (v.length > 40) { this.textInput.value = ''; prev = ''; }
      });
      this.textInput.addEventListener('keydown', (e) => {
        const map = { Enter: 'enter', Tab: 'tab', Escape: 'esc' };
        if (map[e.key]) { e.preventDefault(); Net.send({ t: 'key', key: map[e.key], d: 'tap' }); }
      });
    },

    loadHot() {
      try {
        const h = JSON.parse(localStorage.getItem(LS_HOT));
        if (Array.isArray(h)) return h;
      } catch {}
      return JSON.parse(JSON.stringify(DEFAULT_HOT));
    },

    renderHot() {
      const hot = document.getElementById('hotkey-row');
      hot.innerHTML = '';
      for (const h of this.loadHot()) {
        const b = document.createElement('button');
        b.textContent = h.label;
        b.addEventListener('pointerdown', (e) => { e.preventDefault(); Net.send({ t: 'combo', keys: h.keys }); });
        hot.appendChild(b);
      }
    },

    toggle(on) {
      this.visible = on === undefined ? !this.visible : on;
      this.root.classList.toggle('hidden', !this.visible);
      if (this.visible) setTimeout(() => this.textInput.focus(), 50);
      return this.visible;
    },
  };

  window.Keyboard = Keyboard;
})();
