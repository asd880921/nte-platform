"""
異環 (NTE) 自動釣魚腳本

支援兩種輸入模式，由平台在啟動時用環境變數 NTE_INPUT_MODE 指定
(foreground / background；沒帶就是 foreground)：

  前台 (foreground)  預設，最單純保險
    - 按鍵：keyboard 函式庫送掃描碼給「目前的前景視窗」。
    - 遊戲必須保持在最上層，執行期間請勿手動操作鍵盤滑鼠。

  後台 (background)  可以邊做別的事
    - 按鍵：WM_KEYDOWN / WM_KEYUP 直接 PostMessage 給遊戲視窗，
            不動到系統游標與實體鍵盤狀態。
    - 遊戲可以被其他視窗蓋住，但**不能最小化** (最小化後截不到畫面)。
    - 極低訊息模式不額外宣告視窗 active，只送鍵盤訊息。
    - 比前台多一層介入 (直接對遊戲視窗送訊息)，不確定就用前台。

讀畫面兩種模式都一樣：PrintWindow 截遊戲「視窗」畫面 (非整個螢幕)。
釣魚全程只用鍵盤 (F 拋竿/起竿、A/D 拉桿、ESC 關結算)，所以才能做到後台；
需要滑鼠點擊的腳本只能走前台 (這個遊戲吃不到訊息形式的滑鼠輸入)。

遊戲循環 (見 run_loop)：
  0. 按下 F1 起跑時先按一次 F 開場拋竿 (不然不會進到釣魚狀態)；
     每輪結尾那個 F 有它自己的作用，維持原樣
  1. 等到 trigger.png (咬竿提示) → 按 F 起竿
     等不到 (釣魚流程本身有變數，狀態會跟腳本對不上) → 跑一次「等 close.png」收尾
     (逾時就補按 F) 把狀態抵消掉讓循環接得下去，計一次自動修正，回到 1
  2. 拉桿小遊戲 (見 play_minigame)：持續在偵測區內找 greenbar / yellowbar，
     黃條在綠條左邊按 D、右邊按 A，把黃條推到綠條中心；
     連續 LOST_LIMIT 次找不到條 → 視為小遊戲結束
  3. 等到 close.png (結算畫面) → delay 2s → 按 ESC 關閉
  4. delay 2s → 按 F → delay 1s → 印出「-- 已完成第 x 輪 --」→ 回到 1
"""

import os
import time
import ctypes
import threading

import cv2
import numpy as np
import keyboard
import win32api
import win32con
import win32gui
import win32ui
import win32process

# 讓座標系一致 (截圖像素 / GetWindowRect 都用實體像素)，避免 DPI 縮放造成偵測區偏移
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# ---- 設定 ----
WINDOW_TITLE = "NTE"
PROCESS_NAME = "HTGame"
# 輸入模式：平台啟動時用環境變數帶進來，單獨執行 main.py 時預設前台
INPUT_MODE = os.environ.get("NTE_INPUT_MODE", "foreground").strip().lower()
BACKGROUND = INPUT_MODE == "background"
MODE_LABEL = "後台" if BACKGROUND else "前台"

# template 圖片路徑：相對於本腳本所在資料夾 (打包後 scripts/ 外置於 exe 旁，使用者可自行替換圖片)
TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "template")
MATCH_THRESHOLD = 0.80        # 預設樣板比對信心門檻
# 個別圖的門檻覆寫 (差異較大的圖可調低)
THRESHOLDS = {
    "trigger.png": 0.70,
    "close.png": 0.70,
    "greenbar.png": 0.60,
    "yellowbar.png": 0.60,
}
# log 用的中文名稱：主控台顯示「咬竿提示」比顯示檔名好懂
LABELS = {
    "trigger.png": "咬竿提示",
    "close.png": "結算畫面",
}
POLL_INTERVAL = 0.1           # 等待提示時每次讀畫面間隔 (秒)
PW_RENDERFULLCONTENT = 0x00000002

# 拉桿小遊戲的偵測區：畫面正上方那條進度軸。
# 以「遊戲畫面 (client area) 的比例」表示，不含 Windows 標題列與邊框，
# 所以視窗放在螢幕哪裡、標題列多高都不影響，換解析度也對得上。
# (原始值取自 2560×1440 螢幕、1920×1080 視窗化置中時的螢幕座標 x 900~1670 / y 230~280；
#  扣掉視窗置中的左邊距 320px 與標題列後 ≈ client 內 x 580~1350 / y 35~85，再各留一點餘裕。)
# 若小遊戲的軸偵測不到，調整這四個比例即可。
MINIGAME_ROI = {
    "left": 0.29,
    "top": 0.02,
    "right": 0.71,
    "bottom": 0.11,
}
MOVE_TOLERANCE = 3            # 黃條與綠條中心相差幾 px 內就算對準
# 按壓時長。後台送的是視窗訊息，遊戲要在某個 frame 看到「這顆鍵是按下的」才算收到，
# 所以單擊 (F / ESC) 按久一點當保險 —— 這兩顆鍵不需要精準，拉長沒有代價。
# 拉桿的 A/D 剛好相反：按太久會衝過綠條，兩種模式都用 65ms
# (實測後台用 150ms 平均偏移 35px，改 65ms 只剩 12px)。
KEY_DOWN_MS = 200 if BACKGROUND else 40   # 單次按鍵 (F / ESC)
MOVE_TAP_MS = 65              # 拉桿左右微調 (兩種模式同值)
KEY_REPEAT_MS = 40            # 後台 F 低頻補送重複訊息，避免整段落在兩個 frame 之間
ACTIVATE_KEYS = {"f"}         # F 在背景狀態下需要先補 active，其他鍵不需要
REPEAT_KEYS = {"f"}           # A/D/ESC 改成單次 down/up，降低窗口訊息量
LOST_LIMIT = 20               # 連續幾次找不到條就判定小遊戲結束
TRIGGER_TIMEOUT = 30          # 等咬竿提示的逾時 (秒)，逾時代表遊戲狀態卡住，走自動修正
CLOSE_TIMEOUT = 20            # 等結算畫面的逾時 (秒)

WAIT_LOG_INTERVAL = 5         # 等待中每隔幾秒回報一次 (避免洗版)
TRACK_LOG_INTERVAL = 1.5      # 拉桿追蹤每隔幾秒回報一次
STATS_EVERY = 10              # 每完成幾輪印一次統計

VK_F1 = 0x70
VK_F2 = 0x71

# 熱鍵旗標
_EXIT = threading.Event()      # 整支腳本結束 (Ctrl+C)
_START = threading.Event()     # F1：開始執行
_STOP_RUN = threading.Event()  # F2：停止本次執行、回到待機

_round = 0                     # 已完成輪數 (跨停止/重啟保留)
_fixed = 0                     # 自動修正次數：遊戲狀態卡住時腳本自行收尾重拋、讓循環接下去
_elapsed = 0.0                 # 已完成輪數的總耗時 (秒)，用來算平均


# ============ log ============
def log(text=""):
    print(text)


def log_step(mark, text):
    """輪內的單一步驟 (縮排一層，前面帶一個好掃描的符號)。"""
    print(f"  {mark} {text}")


def log_detail(text):
    """步驟底下的細節 (縮排兩層，例如等待進度、拉桿追蹤)。"""
    print(f"    {text}")


def fmt_dur(seconds):
    """把秒數印成 48s / 1m12s。"""
    seconds = int(seconds)
    return f"{seconds}s" if seconds < 60 else f"{seconds // 60}m{seconds % 60:02d}s"


def log_stats():
    """統計：完成輪數 / 自動修正次數 / 平均每輪耗時。"""
    avg = f"平均 {fmt_dur(_elapsed / _round)}/輪" if _round else "尚未完成整輪"
    print(f"── 統計 · 完成 {_round} 輪 · 自動修正 {_fixed} 次 · {avg} ──")


class StopRun(Exception):
    """使用者按 F2 要求停止本次執行 (回待機，不關腳本)。"""


def _key_watcher():
    """背景執行緒：持續偵測 F1 / F2，直到腳本結束。"""
    user32 = ctypes.windll.user32
    while not _EXIT.is_set():
        if user32.GetAsyncKeyState(VK_F1) & 0x8000:
            _START.set()
        if user32.GetAsyncKeyState(VK_F2) & 0x8000:
            _STOP_RUN.set()
        time.sleep(0.03)


def check_stop():
    """在流程各處呼叫；若已按 F2 則丟出 StopRun。"""
    if _STOP_RUN.is_set():
        raise StopRun()


def sleep_check(seconds):
    """可被 F2 中斷的 delay。"""
    if _STOP_RUN.wait(seconds):
        raise StopRun()


def wait_for_start():
    """待機：清掉舊旗標，阻塞直到按下 F1。"""
    _START.clear()
    _STOP_RUN.clear()
    log("\n[待機] 按 F1 開始自動釣魚；執行中按 F2 停止回待機。")
    if BACKGROUND:
        log("       (後台模式不用切到遊戲；但 F1/F2 是全域熱鍵，在其他程式按到也會生效，請注意。)")
    else:
        log("       按下 F1 後會自動切到遊戲視窗，期間請不要操作鍵盤滑鼠。")
    while not _START.is_set():
        if _EXIT.is_set():
            raise KeyboardInterrupt()
        time.sleep(0.05)
    # 開始前把旗標歸零，確保乾淨起跑
    _START.clear()
    _STOP_RUN.clear()
    log("[F1] 開始執行")


# ============ 找視窗 ============
def _pid_process_name(pid):
    try:
        import psutil
        return psutil.Process(pid).name().rsplit(".", 1)[0]
    except Exception:
        return ""


def find_nte_window():
    result = []

    def _enum(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if title.strip() == WINDOW_TITLE:
            result.append(hwnd)
            return
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if _pid_process_name(pid).lower() == PROCESS_NAME.lower():
            result.append(hwnd)

    win32gui.EnumWindows(_enum, None)
    return result[0] if result else None


def _force_foreground(hwnd):
    """
    把 hwnd 搶到前景，回傳是否成功。

    Windows 只允許「擁有目前前景視窗」的執行緒指定新的前景視窗。腳本是獨立子行程，
    按 F1 時前景是平台視窗 (屬於 launcher 行程)，直接呼叫 SetForegroundWindow 會被擋掉、
    只閃一下工作列。這裡把自己的輸入佇列暫時附掛到前景視窗的執行緒上借到資格，用完卸掉；
    附掛不成再退回 SwitchToThisWindow (不受前景權限限制)。
    """
    user32 = ctypes.windll.user32
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    if win32gui.GetForegroundWindow() == hwnd:
        return True

    fg = win32gui.GetForegroundWindow()
    cur_tid = win32api.GetCurrentThreadId()
    fg_tid = win32process.GetWindowThreadProcessId(fg)[0] if fg else 0
    attached = bool(fg_tid and fg_tid != cur_tid
                    and user32.AttachThreadInput(cur_tid, fg_tid, True))
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
        win32gui.BringWindowToTop(hwnd)
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass          # 被擋下就交給下面的退路，不算致命
    finally:
        if attached:
            user32.AttachThreadInput(cur_tid, fg_tid, False)

    if win32gui.GetForegroundWindow() != hwnd:
        try:
            user32.SwitchToThisWindow(hwnd, True)
        except Exception:
            pass

    time.sleep(0.05)      # 前景切換不是同步完成的，給它一點時間再驗收
    return win32gui.GetForegroundWindow() == hwnd


def bring_to_front(hwnd):
    """盡量把遊戲視窗切到前景 (只有前台模式需要)。"""
    try:
        if not _force_foreground(hwnd):
            log("[!] 切不到遊戲前景，請手動點一下遊戲視窗；點完腳本會繼續。")
    except Exception as e:
        log(f"[!] 切前景失敗(可忽略，只要遊戲本來就在前景)：{e}")


def window_minimized(hwnd):
    return bool(win32gui.IsIconic(hwnd))


def wait_until_restored(hwnd):
    """
    最小化的視窗沒有內容可畫，PrintWindow 會截到空白。
    這時停下來等使用者還原，而不是繼續空轉找圖 (後台模式唯一的視窗要求)。
    """
    if not window_minimized(hwnd):
        return
    how = "請還原視窗 (不用切到前景)" if BACKGROUND else "請還原視窗並切回遊戲"
    log_step("!", f"遊戲視窗被最小化了，讀不到畫面 — {how}")
    while window_minimized(hwnd):
        check_stop()
        time.sleep(0.5)
    log_step("✓", "視窗已還原，繼續執行")


def prepare_window(hwnd):
    """起跑前把視窗弄成該模式需要的狀態。"""
    if BACKGROUND:
        wait_until_restored(hwnd)      # 後台只要求別最小化，蓋住沒關係
    else:
        bring_to_front(hwnd)           # 前台要在最上層才收得到按鍵


# ============ 截取視窗畫面 (整個視窗，原點=視窗左上角) ============
def capture_window(hwnd):
    """
    截取整個視窗畫面 (PrintWindow 本來就是畫整個視窗)。
    回傳影像的 (0,0) 對應到螢幕上的 GetWindowRect(left, top)。
    """
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    w, h = right - left, bottom - top
    if w <= 0 or h <= 0:
        raise RuntimeError("視窗大小異常")

    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(mfc_dc, w, h)
    save_dc.SelectObject(bmp)
    ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), PW_RENDERFULLCONTENT)

    bmpinfo = bmp.GetInfo()
    bmpstr = bmp.GetBitmapBits(True)
    img = np.frombuffer(bmpstr, dtype=np.uint8).reshape(
        (bmpinfo["bmHeight"], bmpinfo["bmWidth"], 4)
    )
    img = np.ascontiguousarray(img[:, :, :3])  # BGRA -> BGR

    win32gui.DeleteObject(bmp.GetHandle())
    save_dc.DeleteDC()
    mfc_dc.DeleteDC()
    win32gui.ReleaseDC(hwnd, hwnd_dc)
    return img


def client_box(hwnd):
    """
    遊戲畫面 (client area) 在 capture_window 影像中的位置 (x, y, w, h)。
    視窗化時影像最上面是 Windows 標題列、兩側是邊框，扣掉它們才是真正的遊戲畫面。
    """
    cw, ch = win32gui.GetClientRect(hwnd)[2:]
    cx, cy = win32gui.ClientToScreen(hwnd, (0, 0))
    left, top, _, _ = win32gui.GetWindowRect(hwnd)
    return cx - left, cy - top, cw, ch


def capture_minigame_roi(hwnd):
    """
    只取拉桿小遊戲那一條的區域 (MINIGAME_ROI 比例 × 遊戲畫面大小，再加上畫面在視窗中的位移)。
    小遊戲只比較綠條/黃條的 x 差距，不需要換回螢幕座標，直接用 ROI 內座標即可。
    """
    img = capture_window(hwnd)
    ox, oy, cw, ch = client_box(hwnd)
    x0 = ox + int(cw * MINIGAME_ROI["left"])
    x1 = ox + int(cw * MINIGAME_ROI["right"])
    y0 = oy + int(ch * MINIGAME_ROI["top"])
    y1 = oy + int(ch * MINIGAME_ROI["bottom"])
    return img[y0:y1, x0:x1]


# ============ 樣板比對 ============
_TEMPL_CACHE = {}


def _load_template(name):
    path = os.path.join(TEMPLATE_DIR, name)
    if name not in _TEMPL_CACHE:
        img = cv2.imread(path, cv2.IMREAD_COLOR)
        if img is None:
            raise FileNotFoundError(f"讀不到樣板圖：{path}")
        _TEMPL_CACHE[name] = img
    return _TEMPL_CACHE[name]


def match_template(screenshot_bgr, name):
    """回傳畫面中最像的 (中心x, 中心y, 最高信心值)，一律回傳 (不套門檻)。"""
    templ = _load_template(name)
    th, tw = templ.shape[:2]
    if screenshot_bgr.shape[0] < th or screenshot_bgr.shape[1] < tw:
        # 樣板比待比對的畫面大 (多半是偵測區設太小或解析度不符)
        return 0, 0, 0.0
    res = cv2.matchTemplate(screenshot_bgr, templ, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(res)
    return max_loc[0] + tw // 2, max_loc[1] + th // 2, max_val


def _threshold_for(name):
    return THRESHOLDS.get(name, MATCH_THRESHOLD)


def _label_for(name):
    return LABELS.get(name, name)


def find_template(screenshot_bgr, name):
    """在已截好的畫面中找 name；信心達門檻回傳 (cx, cy)，否則回傳 None。"""
    cx, cy, conf = match_template(screenshot_bgr, name)
    return (cx, cy) if conf >= _threshold_for(name) else None


def wait_for_template(hwnd, name, timeout=None):
    """
    持續截圖直到在畫面中找到 name (信心 >= 門檻)。回傳 (cx, cy) 座標。
    timeout=None 表示無限等待；超時回傳 None。
    """
    threshold = _threshold_for(name)
    label = _label_for(name)
    log_step("⋯", f"等待{label}")
    start = time.time()
    last_note = 0
    while True:
        check_stop()
        if window_minimized(hwnd):
            # 最小化的這段時間不算進逾時，還原後重新計時
            wait_until_restored(hwnd)
            start, last_note = time.time(), 0
            continue
        img = capture_window(hwnd)
        cx, cy, conf = match_template(img, name)
        if conf >= threshold:
            log_step("✓", f"偵測到{label} (信心 {conf:.2f})")
            return cx, cy
        elapsed = time.time() - start
        if timeout is not None and elapsed > timeout:
            return None      # 逾時後續怎麼處理交給呼叫端說明，這裡不重複印
        if elapsed - last_note >= WAIT_LOG_INTERVAL:
            last_note = elapsed
            log_detail(f"已等 {elapsed:.0f}s …")
        time.sleep(POLL_INTERVAL)


# ============ 鍵盤輸入 (前台 / 後台兩種送法) ============
# 兩種模式的差別全部集中在這一段，其餘流程共用。
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_ACTIVATE = 0x0006
WA_ACTIVE = 1
MAPVK_VK_TO_VSC = 0

# 這支腳本用到的鍵 → 虛擬鍵碼 (後台送訊息要用)
VK_CODES = {
    "f": 0x46,
    "a": 0x41,
    "d": 0x44,
    "esc": 0x1B,
}


def _lparam(vk, down, repeat=False):
    """
    組出 WM_KEYDOWN / WM_KEYUP 的 lParam：
      bit 0-15  重複次數 (1)
      bit 16-23 掃描碼 (用 MapVirtualKey 取，不寫死，換鍵盤配置也對)
      bit 30    前一個狀態 (放開、或按住期間的重複訊息要標成「原本是按下的」)
      bit 31    transition state (放開時為 1)
    少了後面兩個 bit，有些遊戲會把放開又當成一次按下。
    """
    scan = ctypes.windll.user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
    lp = 1 | (scan << 16)
    if repeat or not down:
        lp |= 1 << 30
    if not down:
        lp |= 1 << 31
    return lp


def ensure_active(hwnd):
    """F 前宣告一次視窗 active，避免背景狀態下關鍵按鍵被遊戲忽略。"""
    win32api.PostMessage(hwnd, WM_ACTIVATE, WA_ACTIVE, 0)
    time.sleep(0.05)


def key_down(hwnd, key):
    if BACKGROUND:
        vk = VK_CODES[key]
        win32api.PostMessage(hwnd, WM_KEYDOWN, vk, _lparam(vk, True))
    else:
        keyboard.press(key)


def key_up(hwnd, key):
    if BACKGROUND:
        vk = VK_CODES[key]
        win32api.PostMessage(hwnd, WM_KEYUP, vk, _lparam(vk, False))
    else:
        keyboard.release(key)


def press_key(hwnd, key, action):
    """按一下就放 (F / ESC)；action 是這次按鍵在流程上的意義，會印進 log。"""
    log_step("▸", f"{action} ({key.upper()})")
    tap_key(hwnd, key, KEY_DOWN_MS)


def tap_key(hwnd, key, ms):
    """
    按住 key 指定毫秒再放開。
    F 保留低頻重複 WM_KEYDOWN，避免背景 frame 慢時漏掉關鍵按鍵。
    A / D / ESC 改成單次 down/up，降低窗口訊息量。
    """
    if BACKGROUND and key in ACTIVATE_KEYS:
        ensure_active(hwnd)
    key_down(hwnd, key)
    try:
        if BACKGROUND and key in REPEAT_KEYS:
            vk = VK_CODES[key]
            lp = _lparam(vk, True, repeat=True)
            end = time.time() + ms / 1000.0
            while time.time() < end:
                time.sleep(KEY_REPEAT_MS / 1000.0)
                if time.time() >= end:
                    break
                win32api.PostMessage(hwnd, WM_KEYDOWN, vk, lp)
        else:
            time.sleep(ms / 1000.0)
    finally:
        key_up(hwnd, key)


# ============ 拉桿小遊戲 ============
def move_yellow_bar(hwnd, yellow_x, target_x):
    """把黃直條往綠橫條中心推一格。"""
    diff = target_x - yellow_x
    if abs(diff) <= MOVE_TOLERANCE:
        return  # 已經在目標位置附近
    tap_key(hwnd, "d" if diff > 0 else "a", MOVE_TAP_MS)


def play_minigame(hwnd):
    """拉桿小遊戲主循環：持續把黃條對到綠條中心，直到條消失 (小遊戲結束)。"""
    log_step("▶", "拉桿小遊戲")
    lost = 0
    last_note = 0
    start = time.time()
    while True:
        check_stop()
        roi = capture_minigame_roi(hwnd)
        green = find_template(roi, "greenbar.png")
        yellow = find_template(roi, "yellowbar.png")

        if green and yellow:
            lost = 0
            green_x, yellow_x = green[0], yellow[0]
            diff = green_x - yellow_x
            elapsed = time.time() - start
            if elapsed - last_note >= TRACK_LOG_INTERVAL:   # 節流，避免洗版
                last_note = elapsed
                if abs(diff) <= MOVE_TOLERANCE:
                    state = "已對準"
                else:
                    state = "往右追" if diff > 0 else "往左追"
                log_detail(f"追蹤 {elapsed:4.1f}s   偏移 {diff:+4d}px   {state}")
            move_yellow_bar(hwnd, yellow_x, green_x)
        else:
            lost += 1
            if lost >= LOST_LIMIT:
                log_step("■", f"拉桿結束 (歷時 {fmt_dur(time.time() - start)})")
                return


# ============ 遊戲循環 ============
def wait_for_bite(hwnd):
    """
    等咬竿提示 → 按 F 起竿。
    等不到代表遊戲狀態跟腳本對不上 (釣魚流程本身有變數)，
    這時走一次「收尾 + 重拋」把狀態抵消掉，讓循環可以繼續，並計一次自動修正。
    回傳本輪用掉幾次自動修正。
    """
    global _fixed
    fixes = 0
    while True:
        if wait_for_template(hwnd, "trigger.png", timeout=TRIGGER_TIMEOUT):
            press_key(hwnd, "f", "起竿")
            sleep_check(0.5)
            return fixes
        fixes += 1
        _fixed += 1
        log_step("⟳", f"{TRIGGER_TIMEOUT}s 沒等到咬竿提示，自動修正狀態後重拋"
                      f" (本輪第 {fixes} 次)")
        wait_for_close(hwnd, timeout_key="f")


def wait_for_close(hwnd, timeout_key=None):
    """等結算畫面 → delay 2s → 按 ESC 關閉；逾時則改按 timeout_key (可為 None)。"""
    if wait_for_template(hwnd, "close.png", timeout=CLOSE_TIMEOUT):
        sleep_check(2.0)
        press_key(hwnd, "esc", "關閉結算")
        sleep_check(0.5)
        return True
    log_detail(f"{CLOSE_TIMEOUT}s 沒等到結算畫面，直接往下走")
    if timeout_key:
        press_key(hwnd, timeout_key, "補送拋竿鍵")
    return False


def run_loop(hwnd):
    global _round, _elapsed
    prepare_window(hwnd)

    # F1 起跑時先幫玩家拋一次竿，才會進到釣魚狀態；
    # 之後每輪結尾那個 F 有它自己的作用 (接著下一輪)，維持原樣不動。
    log("[起跑] 先幫你拋竿，接著進入循環")
    press_key(hwnd, "f", "開場拋竿")
    sleep_check(1.0)

    while True:
        log(f"\n── 第 {_round + 1} 輪 ──────────────────────")
        started = time.time()

        # 1. 等咬竿提示 → 按 F
        fixes = wait_for_bite(hwnd)

        # 2. 拉桿小遊戲
        play_minigame(hwnd)

        # 3. 等結算畫面 → 按 ESC
        wait_for_close(hwnd)

        # 4. delay 2s → 按 F 重新拋竿 → delay 1s
        sleep_check(2.0)
        press_key(hwnd, "f", "重新拋竿")
        sleep_check(1.0)

        used = time.time() - started
        _round += 1
        _elapsed += used
        note = f"　自動修正 {fixes} 次" if fixes else ""
        log(f"✔ 第 {_round} 輪完成　用時 {fmt_dur(used)}{note}")
        if _round % STATS_EVERY == 0:
            log_stats()


def main():
    hwnd = find_nte_window()
    if not hwnd:
        log("[!] 找不到 NTE 視窗，請確認遊戲正在執行。")
        return
    log(f"[✓] 找到遊戲視窗 hwnd={hwnd}")
    if BACKGROUND:
        log("[模式] 後台：按鍵直送遊戲視窗，不搶你的鍵盤滑鼠")
        log("       遊戲視窗可以被其他視窗蓋住，但請不要最小化 (會讀不到畫面)")
    else:
        log("[模式] 前台：按鍵送給最上層視窗")
        log("       請保持遊戲在最上層，執行期間不要手動操作鍵盤滑鼠")

    watcher = threading.Thread(target=_key_watcher, daemon=True)
    watcher.start()

    try:
        while True:
            wait_for_start()      # 待機直到 F1
            try:
                run_loop(hwnd)
            except StopRun:
                log("[F2] 已停止，回到待機。再按 F1 會從頭開始 (統計保留)。")
                log_stats()
    except KeyboardInterrupt:
        log("\n[中止] Ctrl+C，關閉腳本。")
        log_stats()
    finally:
        _EXIT.set()


if __name__ == "__main__":
    main()
