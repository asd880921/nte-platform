# NTE 自動化平台

**[⬇️ 點擊下載 (為 Github Release 最新版)](https://github.com/asd880921/nte-platform/releases/latest/download/NTE.Platform.zip)**

異環（NTE / Neverness to Everness）遊戲腳本的**整合平台**。以一個 Apple 風格的桌面介面統一管理多支自動化腳本；每支腳本都是獨立資料夾，透過「讀取遊戲畫面 → 影像辨識找圖 → 定向操作」完成重複性任務。

打包後為**單一 zip**，使用者電腦**不需要安裝 Python** 即可執行。

---

## 特色

- **整合面板**：所有腳本列成卡片，一鍵啟動 / 停止，底部主控台即時顯示 log。
- **易擴充**：新增腳本 = 在 `scripts/` 開一個資料夾（`main.py` + `template/` + `meta.json`），平台自動掃到。
- **定向讀畫面**：只截取遊戲「視窗」畫面（`PrintWindow`），不受其他視窗干擾。
- **前景硬體輸入**：滑鼠用 `win32api`、鍵盤用 `keyboard` 函式庫（送掃描碼，遊戲吃得到）。
- **免 Python 交付**：PyInstaller 打包成資料夾並壓成 zip，秒開、無黑色主控台視窗。

---

## 專案結構

```
nte-platform/
├─ requirements.txt        精簡套件清單（用它產生 .venv）
├─ build.ps1               一鍵建置：venv → 裝套件 → 打包 → 複製 scripts → 壓 zip
├─ nte_platform.spec       PyInstaller 打包設定（排除清單、cv2 剔除、onedir）
├─ launcher/
│  ├─ app.py               平台後端（掃描腳本、啟動/停止子行程、串流 log）
│  └─ web/                 前端介面（index.html / styles.css / app.js）
└─ scripts/
   └─ manager_picks/       「1-1 店長精選」
      ├─ main.py           腳本入口
      ├─ meta.json         顯示名稱 / 說明 / 圖示 / 控制鍵
      └─ template/         辨識用圖片（可依機器替換）
```

---

## 開發環境

- Windows 10 / 11、Python 3.11（64-bit）
- 需要 Microsoft Edge **WebView2 Runtime**（Win10/11 通常已內建；介面靠它顯示）

### 建立環境

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

### 開發模式執行（不打包，直接跑）

```powershell
.venv\Scripts\python launcher\app.py
```

---

## 打包（產出交付物）

在專案根目錄執行：

```powershell
powershell -ExecutionPolicy Bypass -File build.ps1
```

流程：建立 `.venv`（若無）→ 依 `requirements.txt` 安裝 → PyInstaller 打包（onedir）→ 複製 `scripts/` → 壓成 zip。

產出：

- `dist\NTE Platform\`　解壓後的資料夾（約 142 MB，最外層僅 `exe` + `_internal` + `scripts`）
- `dist\NTE Platform.zip`　**交付物**（約 54 MB）

---

## 交付與使用

1. 把 `NTE Platform.zip` 給使用者。
2. 使用者解壓 → 進資料夾雙擊 `NTE Platform.exe`（約 1 秒開啟，無黑窗）。
3. 在面板選腳本按「啟動」→ 切到遊戲前景 → 用腳本的熱鍵操作。
4. **辨識異常時**：進 `scripts\<腳本>\template\` 替換對應 png（重新截自己畫面的圖），存檔後重按啟動。

> 執行期間請保持遊戲在前景，且不要手動搶動滑鼠鍵盤（腳本走前景硬體輸入）。

---

## 內建腳本：1-1 店長精選（`manager_picks`）

自動重複刷取店長精選商品。

**熱鍵**：`F1` 開始循環、`F2` 停止（回待機，不關腳本）。

**流程**：
1. 等 `start.png` → 點擊 → 等 1 秒
2. 內層循環：等 `tomato.png`（不點）→ 等 `prod0.png` 點擊 → 等 `prod1.png` 點擊 → 計數器 +1 → 等 1 秒；計數器達 2 則按 `ESC`，否則回到步驟 2
3. `ESC` 後等 1 秒 → 等 `finish.png` 點擊 → 等 1 秒 → 印「已完成第 x 輪」→ 回到步驟 1

**視窗偵測**：`main.py` 上方 `WINDOW_TITLE` / `PROCESS_NAME` 是用來找遊戲視窗的常數；若遊戲更新後視窗標題或程序名改變，改這兩個值即可。

**信心門檻**：`MATCH_THRESHOLD`（預設 0.80）與 `THRESHOLDS`（個別圖覆寫）控制找圖判定；`POLL_INTERVAL` 控制每次找圖間隔（預設 0.25 秒）。

---

## 新增腳本

在 `scripts/` 下建立新資料夾，放入：

- `main.py`：入口，需有 `main()` 函式；template 路徑用 `os.path.dirname(__file__)/template` 相對取得。
- `template/`：該腳本專屬辨識圖。
- `meta.json`：
  ```json
  {
    "id": "your_script_id",
    "name": "顯示名稱",
    "description": "說明文字",
    "emoji": "🎮",
    "entry": "main.py",
    "controls": [{ "key": "F1", "label": "開始" }, { "key": "F2", "label": "停止" }]
  }
  ```

平台會自動掃到並列成卡片（介面右上角 ↻ 可重新掃描）。若新腳本用到目前尚未打包的套件，記得加進 `requirements.txt` 與 `nte_platform.spec` 的收集清單，再重新 `build.ps1`。

---

## 技術備註

- **打包模式**：onedir（啟動不需解壓、秒開），整包壓成 zip 交付。
- **無黑窗**：`console=False`；子腳本的 log 改由暫存記錄檔串流，避免視窗程式下 stdout 失效。
- **體積優化**：排除 PyQt5 等未使用套件、改用 `opencv-python-headless`、剔除 cv2 的 ffmpeg 影片 dll 與人臉偵測 XML。

---

## 安全性 / 免責說明

本工具採用**侵入性最低**的自動化方式，運作原理單純：

- ✅ 只做兩件事：**截取遊戲視窗畫面**做影像辨識、**模擬鍵盤/滑鼠**操作（等同真人操作）。
- ❌ **不**讀寫遊戲記憶體、**不**修改任何遊戲檔案、**不**攔截或竄改網路封包、**不**注入遊戲程序。

因此相較於修改器 / 記憶體外掛，這是風險相對低的一類做法。

不過它仍屬**第三方自動化工具**，是否符合規範由遊戲官方條款認定，本專案無法保證絕對不會被偵測或處分。**請自行斟酌使用**。
