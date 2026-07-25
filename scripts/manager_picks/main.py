"""
異環 (NTE) 小遊戲腳本

輸入方式：前景硬體輸入
  - 讀畫面：直接截取 NTE 遊戲「視窗」畫面 (非整個螢幕)。
  - 點擊：移動真實游標做硬體點擊 (SetCursorPos + mouse_event)。
  - 按鍵：用 keyboard 函式庫送掃描碼 (ESC)。
  => 執行期間請保持 NTE 在前景，且不要手動搶動滑鼠鍵盤。

遊戲循環 (見 run_loop)：
  1. 等到 start.png → 點擊
  2. delay 1s
  3. 等到 tomato.png (不點) → 3-1 等到 prod0.png 點擊 → 3-2 delay 0.5s
     → 3-3 等到 prod1.png 點擊 → 計數器 +1 → delay 0.5s
     counter >= 2 → 按 ESC；否則回到步驟 3
  ESC 後：delay 1s → 等到 finish.png 點擊 → delay 1s
  → 印出「-- 已完成第 x 輪 --」→ 回到 1
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

# 讓座標系一致 (截圖像素 / GetWindowRect / SetCursorPos 都用實體像素)，避免 DPI 縮放造成點擊偏移
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
# template 圖片路徑：相對於本腳本所在資料夾 (打包後 scripts/ 外置於 exe 旁，使用者可自行替換圖片)
TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "template")
MATCH_THRESHOLD = 0.80        # 預設樣板比對信心門檻
# 個別圖的門檻覆寫 (差異較大的圖可調低)
THRESHOLDS = {
    "tomato.png": 0.65,
}
POLL_INTERVAL = 0.25          # 每次讀畫面間隔 (秒)
CLICK_DOWN_MS = 40            # 點擊按下→放開的毫秒數
PW_RENDERFULLCONTENT = 0x00000002

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
KEYEVENTF_KEYUP = 0x0002
VK_F1 = 0x70
VK_F2 = 0x71

# 熱鍵旗標
_EXIT = threading.Event()      # 整支腳本結束 (Ctrl+C)
_START = threading.Event()     # F1：開始執行
_STOP_RUN = threading.Event()  # F2：停止本次執行、回到待機

_round = 0                     # 已完成輪數 (跨停止/重啟保留)


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
    print("\n[待機] 按 F1 開始遊戲循環；執行中按 F2 停止回待機。")
    print("       按下 F1 後會自動切到遊戲視窗，期間請不要操作鍵盤滑鼠。")
    while not _START.is_set():
        if _EXIT.is_set():
            raise KeyboardInterrupt()
        time.sleep(0.05)
    # 開始前把旗標歸零，確保乾淨起跑
    _START.clear()
    _STOP_RUN.clear()
    print("[F1] 開始執行 ...")


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
    """盡量把遊戲視窗切到前景。"""
    try:
        if not _force_foreground(hwnd):
            print("[!] 切不到遊戲前景，請手動點一下遊戲視窗；點完腳本會繼續。")
    except Exception as e:
        print(f"[!] 切前景失敗(可忽略，只要遊戲本來就在前景)：{e}")


# ============ 截取視窗畫面 (整個視窗，原點=視窗左上角) ============
def capture_window(hwnd):
    """
    截取整個視窗畫面 (PrintWindow 本來就是畫整個視窗)。
    回傳影像的 (0,0) 對應到螢幕上的 GetWindowRect(left, top)，
    因此 click_screen_at 用視窗矩形原點換算即可精準對位。
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
    res = cv2.matchTemplate(screenshot_bgr, templ, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(res)
    return max_loc[0] + tw // 2, max_loc[1] + th // 2, max_val


def _threshold_for(name):
    return THRESHOLDS.get(name, MATCH_THRESHOLD)


def wait_for_template(hwnd, name, timeout=None):
    """
    持續截圖直到在畫面中找到 name (信心 >= 門檻)。回傳 (cx, cy) 座標。
    timeout=None 表示無限等待；超時回傳 None。
    """
    threshold = _threshold_for(name)
    print(f"    等待畫面出現 {name} ...")
    start = time.time()
    last_note = 0
    while True:
        check_stop()
        img = capture_window(hwnd)
        cx, cy, conf = match_template(img, name)
        if conf >= threshold:
            print(f"    → 找到 {name} @({cx},{cy}) 信心 {conf:.3f}")
            return cx, cy
        elapsed = time.time() - start
        if timeout is not None and elapsed > timeout:
            print(f"    ! 等待 {name} 逾時 ({timeout}s)")
            return None
        if elapsed - last_note >= 3:
            last_note = elapsed
            print(f"    ...仍在等 {name} ({elapsed:.0f}s)")
        time.sleep(POLL_INTERVAL)


# ============ 前景硬體輸入 ============
def click_client(hwnd, cx, cy):
    """
    (cx, cy) 是 capture_window 影像上的座標 (原點=視窗左上角)，
    加上視窗矩形原點即為螢幕座標，移動真實游標並做一次左鍵單擊。
    """
    left, top, _, _ = win32gui.GetWindowRect(hwnd)
    sx, sy = left + cx, top + cy
    win32api.SetCursorPos((sx, sy))
    time.sleep(0.02)
    win32api.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(CLICK_DOWN_MS / 1000.0)
    win32api.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    print(f"    ★ 點擊 screen({sx},{sy})")


def press_esc():
    # 用 keyboard 函式庫送掃描碼 (與已長期穩定運行的釣魚腳本一致)
    print("    ★ 按下 ESC")
    keyboard.press_and_release("esc")


# ============ 遊戲循環 ============
def run_loop(hwnd):
    global _round
    while True:
        print(f"\n===== 第 {_round + 1} 輪開始 =====")

        # 1. 等 start.png → 點擊
        hit = wait_for_template(hwnd, "start.png")
        click_client(hwnd, *hit)

        # 2. delay 1s
        sleep_check(1.0)

        # 3. 內層循環：counter 到 2 才結束
        counter = 0
        while True:
            # 3. 等 tomato.png (不點)
            wait_for_template(hwnd, "tomato.png")

            # 3-1. 等 prod0.png → 點擊
            click_client(hwnd, *wait_for_template(hwnd, "prod0.png"))
            # 3-3. 等 prod1.png → 點擊
            click_client(hwnd, *wait_for_template(hwnd, "prod1.png"))

            # 4. 計數器 +1
            counter += 1
            print(f"    計數器 = {counter}")
            # delay 1s
            sleep_check(1.0)
            if counter >= 2:
                press_esc()
                break
            # 否則回到步驟 3

        # ESC 後：delay 1s → 等到 finish.png 點擊 (無限等待)
        sleep_check(1.0)
        click_client(hwnd, *wait_for_template(hwnd, "finish.png"))

        sleep_check(1.0)
        _round += 1
        print(f"-- 已完成第 {_round} 輪  --")


def main():
    hwnd = find_nte_window()
    if not hwnd:
        print("[!] 找不到 NTE 視窗，請確認遊戲正在執行。")
        return
    print(f"[✓] 找到遊戲視窗 hwnd={hwnd}")

    watcher = threading.Thread(target=_key_watcher, daemon=True)
    watcher.start()

    try:
        while True:
            wait_for_start()      # 待機直到 F1
            bring_to_front(hwnd)  # 起跑前確保遊戲在前景
            try:
                run_loop(hwnd)
            except StopRun:
                print("[F2] 已停止，回到待機。再按 F1 會從頭開始 (輪數保留)。")
    except KeyboardInterrupt:
        print("\n[中止] Ctrl+C，關閉腳本。")
    finally:
        _EXIT.set()


if __name__ == "__main__":
    main()
