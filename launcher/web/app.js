// NTE 自動化平台 - 前端邏輯
(() => {
  "use strict";

  const grid = document.getElementById("grid");
  const empty = document.getElementById("empty");
  const scriptCount = document.getElementById("scriptCount");
  const cardTpl = document.getElementById("cardTpl");
  const refreshBtn = document.getElementById("refreshBtn");

  const settingsBtn = document.getElementById("settingsBtn");
  const settingsOverlay = document.getElementById("settingsOverlay");
  const settingsClose = document.getElementById("settingsClose");
  const settingsCancel = document.getElementById("settingsCancel");
  const settingsForm = document.getElementById("settingsForm");
  const settingsSave = document.getElementById("settingsSave");
  const settingsStatus = document.getElementById("settingsStatus");
  const functionKeyGrid = document.getElementById("functionKeyGrid");
  const keyPickerLabel = document.getElementById("keyPickerLabel");
  const startKeyPreview = document.getElementById("startKeyPreview");
  const stopKeyPreview = document.getElementById("stopKeyPreview");
  const assignmentRows = [...document.querySelectorAll(".assignment-row")];

  const dock = document.getElementById("dock");
  const dockHandle = document.getElementById("dockHandle");
  const dockName = document.getElementById("dockName");
  const dockDot = document.getElementById("dockDot");
  const dockStop = document.getElementById("dockStop");
  const logView = document.getElementById("logView");

  const cards = new Map(); // id -> { el, meta, running }
  let activeId = null;
  let hotkeyOriginal = { start_key: "F1", stop_key: "F2" };
  let hotkeyDraft = { ...hotkeyOriginal };
  let hotkeyAction = "start";
  let hotkeyLocked = false;
  let hotkeySaving = false;

  // ---- 執行模式 (前台 / 後台) ----
  // 來源是 meta.json 的 modes 陣列 (後端已正規化，第一個為預設)。
  // 同一支腳本兩種模式流程完全一樣，只差按鍵怎麼送進遊戲。
  const MODE = {
    foreground: {
      label: "前台",
      short: "遊戲需在最上層，期間別碰鍵盤滑鼠。",
      hint: "按鍵送給最上層視窗。請保持遊戲在最上層，執行期間不要操作鍵盤滑鼠。",
      // 螢幕圖示：腳本佔用你的畫面與鍵鼠
      icon: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"
                  stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
               <rect x="2.5" y="4" width="19" height="13" rx="2.5" />
               <path d="M9 20.5h6" />
             </svg>`,
    },
    background: {
      label: "後台",
      short: "可邊做別的事；遊戲可被蓋住但別最小化。",
      hint: "鍵盤訊息直接送給遊戲視窗，執行期間可自由使用電腦。"
          + "遊戲視窗可以被其他視窗蓋住，但不能最小化；比前台多一層介入。",
      // 疊層圖示：腳本在後面跑，前面照你的意思用
      icon: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"
                  stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
               <path d="M7.5 6.5h11a2 2 0 0 1 2 2v11" opacity="0.55" />
               <rect x="3" y="3" width="13" height="13" rx="2.5" />
             </svg>`,
    },
  };

  const modeOf = (name) => MODE[name] || MODE.foreground;

  // 使用者選過的模式記下來 (以腳本 id 為 key)；讀不到 localStorage 就用預設值
  function savedMode(id) {
    try { return localStorage.getItem(`nte.mode.${id}`); } catch (_) { return null; }
  }
  function saveMode(id, mode) {
    try { localStorage.setItem(`nte.mode.${id}`, mode); } catch (_) {}
  }

  /**
   * 每張卡片都有「執行模式」這一區，結構固定為 標籤 → 值 → 一行說明；
   * 只有中間那段不同：能切換的給分段控制項，只支援一種模式的給靜態膠囊
   * (膠囊看起來就是標籤，不會讓人想去點)。
   */
  function buildModeSection(node, modes, id) {
    const picker = node.querySelector(".mode-picker");
    const seg = picker.querySelector(".segmented");
    const badge = picker.querySelector(".mode-badge");
    const note = picker.querySelector(".mode-note");
    const switchable = modes.length > 1;

    if (switchable) {
      badge.classList.add("hidden");
      seg.style.setProperty("--seg-count", modes.length);
      modes.forEach((name) => {
        const m = modeOf(name);
        const btn = document.createElement("button");
        btn.className = "seg";
        btn.type = "button";
        btn.dataset.mode = name;
        btn.setAttribute("role", "radio");
        btn.title = m.hint;
        btn.innerHTML = `${m.icon}<span>${m.label}</span>`;
        btn.addEventListener("click", (e) => {
          e.stopPropagation();          // 不要順便觸發卡片選取
          const c = cards.get(id);
          if (c && c.running) return;   // 執行中不能換模式
          setMode(id, name);
          saveMode(id, name);
        });
        seg.appendChild(btn);
      });
    } else {
      seg.classList.add("hidden");
      const m = modeOf(modes[0]);
      badge.classList.add(modes[0] === "background" ? "bg" : "fg");
      badge.innerHTML = `${m.icon}<span>${m.label}</span>`;
      badge.title = m.hint;
    }

    // 起始值：使用者上次的選擇 → 否則腳本宣告的預設 (modes[0])
    const saved = savedMode(id);
    const initial = switchable && modes.includes(saved) ? saved : modes[0];
    return { seg, badge, note, switchable, initial };
  }

  // 把某張卡片切到指定模式 (更新說明文字，能切換的再更新 thumb 位置與 aria 狀態)
  function setMode(id, name) {
    const c = cards.get(id);
    if (!c) return;
    c.mode = name;
    const s = c.section;
    if (!s) return;
    s.note.textContent = modeOf(name).short;
    s.note.title = modeOf(name).hint;
    if (!s.switchable) return;
    const idx = Math.max(0, c.meta.modes.indexOf(name));
    s.seg.style.setProperty("--seg-index", idx);
    s.seg.classList.toggle("on-background", name === "background");
    s.seg.querySelectorAll(".seg").forEach((b) => {
      b.setAttribute("aria-checked", String(b.dataset.mode === name));
    });
  }

  // ---- pywebview 就緒 ----
  function whenReady() {
    return new Promise((resolve) => {
      if (window.pywebview && window.pywebview.api) return resolve();
      window.addEventListener("pywebviewready", () => resolve(), { once: true });
    });
  }

  // ---- 渲染卡片 ----
  function renderScripts(list) {
    grid.innerHTML = "";
    cards.clear();
    scriptCount.textContent = list.length;
    empty.classList.toggle("hidden", list.length > 0);

    list.forEach((meta, i) => {
      const node = cardTpl.content.firstElementChild.cloneNode(true);
      node.style.animationDelay = `${i * 60}ms`;
      node.querySelector(".emoji").textContent = meta.emoji || "🎮";
      node.querySelector(".card-name").textContent = meta.name || meta.id;
      node.querySelector(".card-desc").textContent = meta.description || "";

      const modes = meta.modes && meta.modes.length ? meta.modes : ["foreground"];
      const section = buildModeSection(node, modes, meta.id);

      const controls = node.querySelector(".card-controls");
      (meta.controls || []).forEach((c) => {
        const span = document.createElement("span");
        span.className = "kbd";
        span.innerHTML = `<b>${c.key}</b> ${c.label}`;
        controls.appendChild(span);
      });

      const runBtn = node.querySelector(".run-btn");
      runBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        toggleRun(meta.id);
      });

      // 點卡片本體 = 只選取（不自動展開主控台，以免影響腳本列表畫面）
      node.addEventListener("click", () => selectScript(meta.id, false));

      grid.appendChild(node);
      cards.set(meta.id, { el: node, meta: { ...meta, modes }, section,
                           mode: section.initial, running: !!meta.running });
      // 執行中的腳本要顯示它「實際在跑」的模式，而不是使用者上次選的
      setMode(meta.id, meta.running && meta.mode ? meta.mode : section.initial);
      applyRunningUI(meta.id, !!meta.running);
    });
  }

  function applyRunningUI(id, running) {
    const c = cards.get(id);
    if (!c) return;
    c.running = running;
    c.el.classList.toggle("running", running);
    c.el.querySelector(".status-badge").textContent = running ? "執行中" : "待機";
    c.el.querySelector(".run-btn").textContent = running ? "停止" : "啟動";
    if (c.section && c.section.switchable) {
      // 執行中鎖住模式：這一輪已經用該模式啟動了，中途換沒有意義
      c.section.seg.classList.toggle("locked", running);
      c.section.seg.title = running ? "執行中無法切換模式，請先停止腳本" : "";
    }
  }

  // ---- 選取 / dock ----
  function selectScript(id, expand) {
    activeId = id;
    cards.forEach((c, cid) => c.el.classList.toggle("selected", cid === id));
    const c = cards.get(id);
    dockName.textContent = c ? c.meta.name : "主控台";
    if (expand) dock.classList.remove("collapsed");
    updateDock();
  }

  function updateDock() {
    const c = activeId ? cards.get(activeId) : null;
    const running = c ? c.running : false;
    dockDot.classList.toggle("live", running);
    dockStop.classList.toggle("hidden", !running);
  }

  dockHandle.addEventListener("click", (e) => {
    if (e.target === dockStop) return;
    dock.classList.toggle("collapsed");
  });

  dockStop.addEventListener("click", (e) => {
    e.stopPropagation();
    if (activeId) window.pywebview.api.stop_script(activeId);
  });

  // ---- 啟動 / 停止 ----
  async function toggleRun(id) {
    const c = cards.get(id);
    if (!c) return;
    if (c.running) {
      await window.pywebview.api.stop_script(id);
    } else {
      const res = await window.pywebview.api.start_script(id, c.mode);
      if (res && res.ok) {
        if (res.mode) setMode(id, res.mode);   // 後端驗證後的實際模式
        applyRunningUI(id, true);
        selectScript(id, true);
      }
    }
  }

  // ---- 輪詢狀態 + log ----
  function isNearBottom(el) {
    return el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  }

  async function poll() {
    try {
      const state = await window.pywebview.api.get_state();
      for (const [id, s] of Object.entries(state)) {
        if (cards.has(id) && cards.get(id).running !== s.running) {
          const wasRunning = cards.get(id).running;
          applyRunningUI(id, s.running);
          if (id === activeId) updateDock();
          // 腳本停止（執行中→停止）時，自動摺疊主控台
          if (id === activeId && wasRunning && !s.running) {
            dock.classList.add("collapsed");
          }
        }
      }
      if (activeId && state[activeId]) {
        const text = (state[activeId].logs || []).join("\n");
        if (text !== logView.textContent) {
          const stick = isNearBottom(logView);
          logView.textContent = text;
          if (stick) logView.scrollTop = logView.scrollHeight;
        }
      }
    } catch (_) {}
    setTimeout(poll, 400);
  }

  // ---- 快捷鍵設定 ----
  function settingsMessage(text = "", kind = "") {
    settingsStatus.textContent = text;
    settingsStatus.classList.toggle("success", kind === "success");
    settingsStatus.classList.toggle("error", kind === "error");
  }

  function hotkeyIsDirty() {
    return hotkeyDraft.start_key !== hotkeyOriginal.start_key
      || hotkeyDraft.stop_key !== hotkeyOriginal.stop_key;
  }

  function updateHotkeyUI() {
    startKeyPreview.textContent = hotkeyDraft.start_key;
    stopKeyPreview.textContent = hotkeyDraft.stop_key;
    keyPickerLabel.textContent = "設定快捷鍵";

    assignmentRows.forEach((row) => {
      const active = row.dataset.action === hotkeyAction;
      row.classList.toggle("active", active);
      row.setAttribute("aria-pressed", String(active));
      row.disabled = hotkeyLocked;
    });

    const activeKey = hotkeyDraft[`${hotkeyAction}_key`];
    const otherAction = hotkeyAction === "start" ? "stop" : "start";
    const otherKey = hotkeyDraft[`${otherAction}_key`];
    functionKeyGrid.querySelectorAll(".function-key").forEach((button) => {
      const selected = button.dataset.key === activeKey;
      const assignedOther = button.dataset.key === otherKey;
      button.setAttribute("aria-checked", String(selected));
      button.classList.toggle("assigned-other", assignedOther);
      button.disabled = hotkeyLocked;
      button.title = assignedOther ? "已指派給另一個動作；點選後交換" : "";
    });

    settingsSave.disabled = hotkeyLocked || hotkeySaving || !hotkeyIsDirty();
  }

  function renderFunctionKeys(keys) {
    functionKeyGrid.innerHTML = "";
    keys.forEach((key) => {
      const button = document.createElement("button");
      button.className = "function-key";
      button.type = "button";
      button.dataset.key = key;
      button.textContent = key;
      button.setAttribute("role", "radio");
      button.addEventListener("click", () => {
        if (hotkeyLocked) return;
        const ownField = `${hotkeyAction}_key`;
        const otherAction = hotkeyAction === "start" ? "stop" : "start";
        const otherField = `${otherAction}_key`;
        const previous = hotkeyDraft[ownField];
        const swaps = hotkeyDraft[otherField] === key;
        hotkeyDraft[ownField] = key;
        if (swaps) hotkeyDraft[otherField] = previous;
        settingsMessage(swaps
          ? "已交換開始與停止快捷鍵。"
          : "先選擇要設定的功能，再按下想使用的按鍵。");
        updateHotkeyUI();
      });
      functionKeyGrid.appendChild(button);
    });
  }

  assignmentRows.forEach((row) => {
    row.addEventListener("click", () => {
      if (hotkeyLocked) return;
      hotkeyAction = row.dataset.action;
      settingsMessage("先選擇要設定的功能，再按下想使用的按鍵。");
      updateHotkeyUI();
    });
  });

  async function openSettings() {
    aboutOverlay.classList.remove("open");
    settingsOverlay.classList.add("open");
    settingsMessage("正在讀取設定…");
    try {
      const info = await window.pywebview.api.get_hotkey_settings();
      hotkeyOriginal = {
        start_key: info.start_key || "F1",
        stop_key: info.stop_key || "F2",
      };
      hotkeyDraft = { ...hotkeyOriginal };
      hotkeyAction = "start";
      hotkeyLocked = !!info.locked;
      renderFunctionKeys(info.valid_keys || []);
      if (hotkeyLocked) {
        settingsMessage("請先停止執行中的腳本，再修改快捷鍵。", "error");
      } else {
        settingsMessage("先選擇要設定的功能，再按下想使用的按鍵。");
      }
      updateHotkeyUI();
      requestAnimationFrame(() => assignmentRows[0].focus());
    } catch (_) {
      settingsMessage("無法讀取快捷鍵設定。", "error");
    }
  }

  function closeSettings(restoreFocus = true) {
    settingsOverlay.classList.remove("open");
    if (restoreFocus) settingsBtn.focus();
  }

  settingsBtn.addEventListener("click", openSettings);
  settingsClose.addEventListener("click", () => closeSettings());
  settingsCancel.addEventListener("click", () => closeSettings());
  settingsOverlay.addEventListener("click", (e) => {
    if (e.target === settingsOverlay) closeSettings();
  });

  settingsForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (settingsSave.disabled) return;
    hotkeySaving = true;
    settingsMessage("正在儲存…");
    updateHotkeyUI();
    try {
      const result = await window.pywebview.api.save_hotkey_settings(
        hotkeyDraft.start_key,
        hotkeyDraft.stop_key,
      );
      if (!result || !result.ok) {
        settingsMessage(result && result.message
          ? result.message : "無法儲存快捷鍵設定。", "error");
        return;
      }
      hotkeyOriginal = {
        start_key: result.start_key,
        stop_key: result.stop_key,
      };
      hotkeyDraft = { ...hotkeyOriginal };
      const selected = activeId;
      const list = await window.pywebview.api.list_scripts();
      renderScripts(list);
      if (selected && cards.has(selected)) selectScript(selected, false);
      settingsMessage("已儲存，之後啟動的腳本會使用新快捷鍵。", "success");
    } catch (_) {
      settingsMessage("無法儲存快捷鍵設定。", "error");
    } finally {
      hotkeySaving = false;
      updateHotkeyUI();
    }
  });

  // ---- About 彈窗 ----
  const aboutBtn = document.getElementById("aboutBtn");
  const aboutOverlay = document.getElementById("aboutOverlay");
  const aboutClose = document.getElementById("aboutClose");
  const aboutGithub = document.getElementById("aboutGithub");
  const GITHUB_URL = "https://github.com/asd880921/nte-platform";

  function openAbout() {
    settingsOverlay.classList.remove("open");
    aboutOverlay.classList.add("open");
  }
  function closeAbout() { aboutOverlay.classList.remove("open"); }

  aboutBtn.addEventListener("click", openAbout);
  aboutClose.addEventListener("click", closeAbout);
  aboutOverlay.addEventListener("click", (e) => {
    if (e.target === aboutOverlay) closeAbout(); // 點卡片外的遮罩關閉
  });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (settingsOverlay.classList.contains("open")) closeSettings();
    else if (aboutOverlay.classList.contains("open")) closeAbout();
  });
  aboutGithub.addEventListener("click", (e) => {
    e.preventDefault();
    // 用系統預設瀏覽器開啟，而非在 webview 內導航
    if (window.pywebview && window.pywebview.api) {
      window.pywebview.api.open_url(GITHUB_URL);
    }
  });

  // ---- 版本與更新提示 ----
  const updateBtn = document.getElementById("updateBtn");
  const updateText = document.getElementById("updateText");
  const aboutVersion = document.getElementById("aboutVersion");
  const aboutUpdate = document.getElementById("aboutUpdate");
  let releaseUrl = null;

  async function loadVersion() {
    try {
      const v = await window.pywebview.api.get_version();
      aboutVersion.textContent = v ? `v${v}` : "—";
    } catch (_) {}
  }

  function showUpdate(info) {
    releaseUrl = info.url;
    // 講明是「發現的新版本」而非目前版本；about 內另有目前版本可對照
    updateText.textContent = `發現新版本 v${info.latest}`;
    updateBtn.title = `開啟 v${info.latest} 的下載頁`;
    updateBtn.classList.remove("hidden");
    aboutUpdate.textContent = `發現新版本 v${info.latest}，點此開啟下載頁 ↗`;
    aboutUpdate.classList.remove("hidden");
  }

  /** 後端是背景查的，這裡輪詢幾次就放棄（沒有新版是常態，不必吵） */
  async function pollUpdate(tries = 12) {
    try {
      const info = await window.pywebview.api.get_update();
      if (info && info.checked) {
        if (info.available && info.latest) showUpdate(info);
        return;
      }
    } catch (_) {
      return;
    }
    if (tries > 0) setTimeout(() => pollUpdate(tries - 1), 1000);
  }

  function openRelease(e) {
    e.preventDefault();
    if (releaseUrl && window.pywebview && window.pywebview.api) {
      window.pywebview.api.open_url(releaseUrl);
    }
  }

  updateBtn.addEventListener("click", openRelease);
  aboutUpdate.addEventListener("click", openRelease);

  // ---- 重新掃描 ----
  refreshBtn.addEventListener("click", async () => {
    refreshBtn.classList.remove("spin");
    void refreshBtn.offsetWidth; // reflow 以重播動畫
    refreshBtn.classList.add("spin");
    const list = await window.pywebview.api.refresh();
    renderScripts(list);
    if (activeId && cards.has(activeId)) selectScript(activeId, false);
  });

  // ---- 啟動 ----
  whenReady().then(async () => {
    const list = await window.pywebview.api.list_scripts();
    renderScripts(list);
    loadVersion();
    pollUpdate();
    poll();
  });
})();
