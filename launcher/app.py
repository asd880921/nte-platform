"""
NTE 自動化平台 - 啟動器 (pywebview + HTML/CSS)

架構：
  - scripts/<id>/          每個腳本一個資料夾
       ├─ main.py          統一入口 (以子行程執行，內部自帶 F1/F2 控制)
       ├─ template/        該腳本專屬圖片
       └─ meta.json        顯示名稱、說明、圖示、控制鍵
  - launcher/web/          前端 (Apple 風格介面)

後端只做三件事：列出腳本、啟動/停止子行程、把子行程輸出串流給前端。
"""

import os
import sys
import json
import time
import tempfile
import threading
import subprocess
import webbrowser
from collections import deque

import webview

FROZEN = getattr(sys, "frozen", False)


def _base_dir():
    """
    外置 scripts/ 的基準目錄 (main.py + template + meta.json 都在這，使用者可編輯/替換)：
      - 打包成 exe 時 = exe 所在資料夾 (scripts 外置於 exe 旁)
      - 開發時 = 專案根目錄
    """
    if FROZEN:
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _web_dir():
    """
    前端 web/ 目錄：
      - 打包時 = 被塞進 exe 的臨時目錄 (sys._MEIPASS)/web
      - 開發時 = launcher/web
    """
    if FROZEN:
        return os.path.join(sys._MEIPASS, "web")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


def ensure_external_scripts():
    """打包 (onefile) 情境：exe 內附一份預設 scripts/，
    第一次執行時解壓到 exe 旁邊，之後都讀外置那份 (使用者可替換 template)。"""
    if not FROZEN:
        return
    src = os.path.join(sys._MEIPASS, "scripts")   # exe 內附的預設版
    dst = os.path.join(os.path.dirname(sys.executable), "scripts")  # exe 旁邊
    if not os.path.isdir(src):
        return
    if os.path.isdir(dst) and os.listdir(dst):
        return  # 已存在就不覆蓋，保留使用者替換過的圖片
    import shutil
    try:
        shutil.copytree(src, dst, dirs_exist_ok=True)
    except Exception:
        pass


SCRIPTS_DIR = os.path.join(_base_dir(), "scripts")
WEB_DIR = _web_dir()
INDEX = os.path.join(WEB_DIR, "index.html")

CREATE_NO_WINDOW = 0x08000000
LOG_MAX = 400

GITHUB_REPO = "asd880921/nte-platform"
RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases/latest"
LATEST_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


def _read_version():
    """
    版本號的唯一來源是根目錄的 VERSION 檔 (打包時被塞進 exe)。
    前端不寫死版本，一律跟後端要，避免 exe 與 About 顯示不一致。
    """
    for base in (getattr(sys, "_MEIPASS", None), _base_dir()):
        if not base:
            continue
        path = os.path.join(base, "VERSION")
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    return f.read().strip()
            except Exception:
                pass
    return "unknown"


APP_VERSION = _read_version()


def _parse_version(text):
    """把 'v1.2.3' 這種字串轉成 (1, 2, 3) 方便比大小；非數字的片段當 0。"""
    parts = []
    for chunk in (text or "").strip().lstrip("vV").split(".")[:4]:
        digits = ""
        for ch in chunk:
            if not ch.isdigit():
                break
            digits += ch
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


class UpdateChecker:
    """
    背景查一次 GitHub 最新 release，有新版就讓前端顯示提示。
    只讀不裝：使用者自己下載 zip 覆蓋 (執行中的 exe 沒辦法安全地覆蓋自己)。
    查不到就安靜跳過 —— 離線、GitHub 掛掉都不該影響平台開啟。
    """

    def __init__(self):
        self.result = {"checked": False, "available": False,
                       "current": APP_VERSION, "latest": None, "url": RELEASES_PAGE}

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            import urllib.request
            req = urllib.request.Request(
                LATEST_API,
                headers={"User-Agent": f"NTE-Platform/{APP_VERSION}",
                         "Accept": "application/vnd.github+json"},
            )
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            latest = (data.get("tag_name") or "").strip()
            if latest:
                self.result["latest"] = latest.lstrip("vV")
                self.result["url"] = data.get("html_url") or RELEASES_PAGE
                self.result["available"] = (
                    _parse_version(latest) > _parse_version(APP_VERSION)
                )
        except Exception:
            pass       # 網路問題 / API 限流 / 沒有 release，都當作沒有新版
        finally:
            self.result["checked"] = True


def run_script_entry(entry_path):
    """--run 模式：動態載入外置腳本的 main.py 並執行其 main()。"""
    import importlib.util

    entry_path = os.path.abspath(entry_path)
    if not os.path.isfile(entry_path):
        print(f"[錯誤] 找不到腳本入口檔：{entry_path}")
        return
    folder = os.path.dirname(entry_path)
    os.chdir(folder)
    if folder not in sys.path:
        sys.path.insert(0, folder)
    spec = importlib.util.spec_from_file_location("__nte_script__", entry_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if hasattr(mod, "main"):
        mod.main()


MODES = ("foreground", "background")
MODE_LABEL = {"foreground": "前台", "background": "後台"}


def normalize_meta(meta, folder_name):
    """
    補齊 meta.json 的預設值。
    modes 是腳本支援的輸入模式清單，第一個是預設值；
    只宣告 requires_foreground 的舊腳本也要能讀 (往回相容)。
    """
    meta.setdefault("id", folder_name)
    modes = meta.get("modes")
    if not isinstance(modes, list) or not modes:
        modes = ["foreground"] if meta.get("requires_foreground", True) else ["background"]
    meta["modes"] = [m for m in modes if m in MODES] or ["foreground"]
    return meta


class ScriptRunner:
    """管理單一腳本子行程的啟動 / 停止 / log 緩衝。"""

    def __init__(self, meta, folder):
        self.meta = meta
        self.folder = folder
        self.proc = None
        self.logfile = None
        self.mode = meta["modes"][0]   # 這次(或上一次)執行用的輸入模式
        self.logs = deque(maxlen=LOG_MAX)
        self._lock = threading.Lock()

    @property
    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self, mode=None):
        if self.running:
            return False, "腳本已在執行中"
        entry = self.meta.get("entry", "main.py")
        entry_path = os.path.join(self.folder, entry)
        if not os.path.isfile(entry_path):
            return False, f"找不到入口檔：{entry}"

        # 前端傳來的模式一律驗證過才用，不支援就退回該腳本的預設模式
        self.mode = mode if mode in self.meta["modes"] else self.meta["modes"][0]

        self.logs.clear()
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
                   NTE_INPUT_MODE=self.mode)

        if FROZEN:
            # 視窗程式 (console=False) 下子行程 stdout 不可靠，
            # 改讓子腳本把輸出寫進暫存記錄檔，平台再 tail 該檔取得 log。
            self.logfile = os.path.join(
                tempfile.gettempdir(),
                f"nte_{self.meta['id']}_{os.getpid()}_{int(time.time())}.log",
            )
            open(self.logfile, "w", encoding="utf-8").close()
            self.proc = subprocess.Popen(
                [sys.executable, "--run", entry_path, "--log", self.logfile],
                cwd=self.folder,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                creationflags=CREATE_NO_WINDOW,
            )
            threading.Thread(target=self._pump_file, daemon=True).start()
        else:
            # 開發模式：直接以 python 執行 main.py，用 stdout 管線擷取
            self.logfile = None
            self.proc = subprocess.Popen(
                [sys.executable, "-u", entry_path],
                cwd=self.folder,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
            threading.Thread(target=self._pump_pipe, daemon=True).start()

        if self.mode == "background":
            self._append("[啟動] 腳本已啟動（後台模式）。遊戲不用切到前景，直接按 F1 開始。")
        else:
            self._append("[啟動] 腳本已啟動（前台模式）。切到遊戲前景後按 F1 開始。")
        return True, "已啟動"

    def stop(self):
        if not self.running:
            return False, "腳本並未在執行"
        try:
            self.proc.terminate()
        except Exception as e:
            return False, f"停止失敗：{e}"
        self._append("[停止] 已由平台結束腳本。")
        return True, "已停止"

    def _pump_pipe(self):
        """開發模式：從子行程 stdout 管線讀 log。"""
        try:
            for line in self.proc.stdout:
                self._append(line.rstrip("\n"))
        except Exception:
            pass
        self._append("[結束] 腳本行程已結束。")

    def _pump_file(self):
        """打包模式：持續 tail 子腳本寫出的記錄檔。"""
        try:
            with open(self.logfile, "r", encoding="utf-8", errors="replace") as f:
                while True:
                    line = f.readline()
                    if line:
                        self._append(line.rstrip("\n"))
                        continue
                    if self.proc.poll() is not None:
                        for rest in f.read().splitlines():
                            self._append(rest)
                        break
                    time.sleep(0.1)
        except Exception:
            pass
        self._append("[結束] 腳本行程已結束。")

    def _append(self, line):
        with self._lock:
            self.logs.append(line)

    def snapshot_logs(self):
        with self._lock:
            return list(self.logs)


class Api:
    """暴露給前端 JS 呼叫的介面 (window.pywebview.api.*)。"""

    def __init__(self):
        self.runners = {}
        self.updater = UpdateChecker()
        self.updater.start()
        self._load_scripts()

    # ---- 版本 / 更新 ----
    def get_version(self):
        return APP_VERSION

    def get_update(self):
        """前端啟動後問一次；checked 還是 false 表示背景還在查。"""
        return dict(self.updater.result)

    def _load_scripts(self):
        self.runners.clear()
        if not os.path.isdir(SCRIPTS_DIR):
            return
        found = []
        for name in sorted(os.listdir(SCRIPTS_DIR)):
            folder = os.path.join(SCRIPTS_DIR, name)
            meta_path = os.path.join(folder, "meta.json")
            if not os.path.isfile(meta_path):
                continue
            try:
                with open(meta_path, encoding="utf-8") as f:
                    meta = json.load(f)
            except Exception:
                continue
            found.append((normalize_meta(meta, name), folder))
        # meta.json 的 order 決定卡片排序 (數字小的在前)；沒寫 order 的排最後，
        # 同名次則照資料夾名。新增腳本給一個比現有大的 order 就會排在後面。
        found.sort(key=lambda mf: (mf[0].get("order", 9999), os.path.basename(mf[1])))
        for meta, folder in found:
            self.runners[meta["id"]] = ScriptRunner(meta, folder)

    # ---- 前端呼叫的方法 ----
    def list_scripts(self):
        self._load_scripts_if_new()
        return [
            {**r.meta, "running": r.running, "mode": r.mode}
            for r in self.runners.values()
        ]

    def start_script(self, script_id, mode=None):
        r = self.runners.get(script_id)
        if not r:
            return {"ok": False, "message": "找不到該腳本"}
        ok, msg = r.start(mode)
        return {"ok": ok, "message": msg, "mode": r.mode}

    def stop_script(self, script_id):
        r = self.runners.get(script_id)
        if not r:
            return {"ok": False, "message": "找不到該腳本"}
        ok, msg = r.stop()
        return {"ok": ok, "message": msg}

    def get_state(self):
        """前端輪詢：回傳每個腳本的執行狀態與最新 log。"""
        return {
            sid: {"running": r.running, "mode": r.mode, "logs": r.snapshot_logs()}
            for sid, r in self.runners.items()
        }

    def refresh(self):
        self._load_scripts_if_new()
        return self.list_scripts()

    def open_url(self, url):
        """用系統預設瀏覽器開啟連結 (About 的 GitHub 連結)。"""
        try:
            webbrowser.open(url)
            return True
        except Exception:
            return False

    # ---- 內部 ----
    def _load_scripts_if_new(self):
        """偵測有無新增/移除的腳本資料夾，保留執行中的 runner。"""
        if not os.path.isdir(SCRIPTS_DIR):
            return
        current = set()
        for name in os.listdir(SCRIPTS_DIR):
            meta_path = os.path.join(SCRIPTS_DIR, name, "meta.json")
            if os.path.isfile(meta_path):
                current.add(name)
        known = set(self.runners.keys())
        # 有變動才重掃 (但不動到執行中的)
        if current != known:
            running = {sid: r for sid, r in self.runners.items() if r.running}
            self._load_scripts()
            self.runners.update(running)

    def shutdown(self):
        for r in self.runners.values():
            if r.running:
                try:
                    r.proc.terminate()
                except Exception:
                    pass


def main():
    ensure_external_scripts()  # 首次執行時把預設 scripts/ 解壓到 exe 旁
    api = Api()
    window = webview.create_window(
        "NTE 自動化平台",
        url=INDEX,
        js_api=api,
        width=940,
        height=700,
        min_size=(780, 580),
        background_color="#1c1c1e",
    )

    def on_closed():
        api.shutdown()

    window.events.closed += on_closed
    webview.start(gui="edgechromium")


def _redirect_output_to_log(logpath):
    """--run 模式：把 stdout/stderr 導到平台指定的記錄檔 (視窗程式下 stdout 為 None，
    直接以檔案路徑開檔最穩)。"""
    try:
        f = open(logpath, "w", encoding="utf-8", buffering=1)
        sys.stdout = f
        sys.stderr = f
    except Exception:
        pass


if __name__ == "__main__":
    if "--run" in sys.argv:
        if "--log" in sys.argv:
            _redirect_output_to_log(sys.argv[sys.argv.index("--log") + 1])
        run_script_entry(sys.argv[sys.argv.index("--run") + 1])
    else:
        main()
