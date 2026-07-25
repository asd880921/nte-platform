// NTE 自動化平台 - 前端邏輯
(() => {
  "use strict";

  const grid = document.getElementById("grid");
  const empty = document.getElementById("empty");
  const scriptCount = document.getElementById("scriptCount");
  const cardTpl = document.getElementById("cardTpl");
  const refreshBtn = document.getElementById("refreshBtn");

  const dock = document.getElementById("dock");
  const dockHandle = document.getElementById("dockHandle");
  const dockName = document.getElementById("dockName");
  const dockDot = document.getElementById("dockDot");
  const dockStop = document.getElementById("dockStop");
  const logView = document.getElementById("logView");

  const cards = new Map(); // id -> { el, meta, running }
  let activeId = null;

  // ---- 前台 / 後台徽章 ----
  // 資料來源是 meta.json 的 requires_foreground (布林)，不是描述文字裡的標記。
  // 沒寫這個欄位的腳本一律視為前台 (平台預設就是前景硬體輸入)。
  const MODE = {
    fg: {
      label: "前台",
      hint: "遊戲視窗需保持在最上層；執行期間請勿操作鍵盤滑鼠",
      // 螢幕圖示：這支腳本會佔用你的畫面與鍵鼠
      icon: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"
                  stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
               <rect x="2.5" y="4" width="19" height="13" rx="2.5" />
               <path d="M9 20.5h6" />
             </svg>`,
    },
    bg: {
      label: "後台",
      hint: "遊戲視窗不可最小化，但可以被其他視窗蓋住；"
          + "執行期間鍵盤滑鼠可自由使用，不影響腳本",
      // 疊層圖示：這支腳本在後面跑，前面照你的意思用
      icon: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"
                  stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
               <path d="M7.5 6.5h11a2 2 0 0 1 2 2v11" opacity="0.55" />
               <rect x="3" y="3" width="13" height="13" rx="2.5" />
             </svg>`,
    },
  };

  function fillModeBadge(el, meta) {
    const mode = meta.requires_foreground === false ? MODE.bg : MODE.fg;
    el.classList.add(meta.requires_foreground === false ? "bg" : "fg");
    el.innerHTML = `${mode.icon}<span>${mode.label}</span>`;
    el.title = mode.hint;
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
      fillModeBadge(node.querySelector(".mode-badge"), meta);
      node.querySelector(".card-desc").textContent = meta.description || "";

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
      cards.set(meta.id, { el: node, meta, running: !!meta.running });
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
      const res = await window.pywebview.api.start_script(id);
      if (res && res.ok) {
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

  // ---- About 彈窗 ----
  const aboutBtn = document.getElementById("aboutBtn");
  const aboutOverlay = document.getElementById("aboutOverlay");
  const aboutClose = document.getElementById("aboutClose");
  const aboutGithub = document.getElementById("aboutGithub");
  const GITHUB_URL = "https://github.com/asd880921/nte-platform";

  function openAbout() { aboutOverlay.classList.add("open"); }
  function closeAbout() { aboutOverlay.classList.remove("open"); }

  aboutBtn.addEventListener("click", openAbout);
  aboutClose.addEventListener("click", closeAbout);
  aboutOverlay.addEventListener("click", (e) => {
    if (e.target === aboutOverlay) closeAbout(); // 點卡片外的遮罩關閉
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && aboutOverlay.classList.contains("open")) closeAbout();
  });
  aboutGithub.addEventListener("click", (e) => {
    e.preventDefault();
    // 用系統預設瀏覽器開啟，而非在 webview 內導航
    if (window.pywebview && window.pywebview.api) {
      window.pywebview.api.open_url(GITHUB_URL);
    }
  });

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
    poll();
  });
})();
