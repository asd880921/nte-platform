"""
異環 (NTE)「九百九十九夜」鈕扣代幣自動循環腳本。

本腳本只支援前台模式。它會持續辨識左上角小地圖中的角色箭頭與地標，
透過滑鼠水平移動調整方向，並以 W / Shift 完成移動與閃避。
"""

import ctypes
import math
import os
import threading
import time

import cv2
import keyboard
import numpy as np
import win32api
import win32con
import win32gui
import win32process
import win32ui


try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


WINDOW_TITLE = "NTE"
PROCESS_NAME = "HTGame"
TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "template")

PW_RENDERFULLCONTENT = 0x00000002
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
VK_F1 = 0x70
VK_F2 = 0x71

POLL_INTERVAL = 0.12
MOVE_STEP_SECONDS = 0.18
CAMPFIRE_PROMPT_POLL_SECONDS = 0.015
DODGE_SETTLE_SECONDS = 1.0
DODGE_DURATION_SECONDS = 60.0
MARKER_CROSS_SECONDS = 0.25
CAMPFIRE_BACK_AWAY_SECONDS = 1.5
ARRIVAL_DISTANCE = 17.0
CAMPFIRE_ARRIVAL_DISTANCE = 8.0
CAMPFIRE_OCCLUSION_DISTANCE = 34.0
CAMPFIRE_OCCLUSION_STEP_SECONDS = 0.04
CAMPFIRE_OCCLUSION_STEPS = 4
ICON_OCCLUSION_DISTANCE = 34.0
PASS_DISTANCE = 32.0
PASS_MARGIN = 5.0
TARGET_LOST_LIMIT = 8
MOVE_ALIGNMENT_TOLERANCE = math.radians(3)
TURN_DEAD_ZONE = MOVE_ALIGNMENT_TOLERANCE
TURN_PROBE_TAP_SECONDS = 0.025
TURN_SETTLE_SECONDS = 0.05
TURN_PIXELS_PER_RADIAN = 240
TURN_MAX_PIXELS = 360

# 以 1920x1080 遊戲 client area 為基準，只讀左上小地圖區域。
MINIMAP_ROI = {"left": 0.0, "top": 0.0, "right": 0.15, "bottom": 0.27}

MATCH_THRESHOLD = 0.55
FIRE_MATCH_THRESHOLD = 0.72
UI_THRESHOLDS = {
    "press_f.png": 0.80,
    "mouse_click.png": 0.80,
}

# sample.png 內四個圖示的緊密裁切區域 (x, y, width, height)。
# 只取圖示本體，不包含外圍圓圈，避免小地圖底圖影響辨識。
ICON_CROPS = {
    "door": (63, 36, 34, 57),
    "boss": (190, 37, 54, 53),
    "route_1": (333, 33, 48, 61),
    "route_2": (467, 36, 53, 54),
}

TARGET_LABELS = {
    "campfire": "火堆",
    "door": "門口",
    "boss": "Boss 中心",
    "route_1": "折返路線 1",
    "route_2": "折返路線 2",
}

_EXIT = threading.Event()
_START = threading.Event()
_STOP_RUN = threading.Event()
_round = 0


class StopRun(Exception):
    """使用者按 F2，停止本輪並回到待機。"""


def log(text=""):
    print(text, flush=True)


def log_step(mark, text):
    log(f"{mark} {text}")


def _key_watcher():
    user32 = ctypes.windll.user32
    while not _EXIT.is_set():
        if user32.GetAsyncKeyState(VK_F1) & 0x8000:
            _START.set()
        if user32.GetAsyncKeyState(VK_F2) & 0x8000:
            _STOP_RUN.set()
        time.sleep(0.03)


def check_stop():
    if _STOP_RUN.is_set():
        raise StopRun()


def sleep_check(seconds):
    if _STOP_RUN.wait(seconds):
        raise StopRun()


def release_movement_keys():
    """任何中止或例外都確保不留下按住的移動鍵。"""
    keyboard.release("w")
    keyboard.release("s")
    keyboard.release("shift")


def hold_key_for(key, seconds):
    """按住指定按鍵一段可被 F2 中止的時間，任何情況都保證放開。"""
    keyboard.press(key)
    try:
        sleep_check(seconds)
    finally:
        keyboard.release(key)


def wait_for_start():
    _START.clear()
    _STOP_RUN.clear()
    log("\n[待機] 請先讓角色位於九百九十九夜副本內，再按 F1 開始循環。")
    log("       執行中按 F2 可停止並回到待機；期間請勿操作鍵盤滑鼠。")
    while not _START.is_set():
        if _EXIT.is_set():
            raise KeyboardInterrupt()
        time.sleep(0.05)
    _START.clear()
    _STOP_RUN.clear()
    log("[F1] 開始執行")


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
    user32 = ctypes.windll.user32
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    if win32gui.GetForegroundWindow() == hwnd:
        return True

    foreground = win32gui.GetForegroundWindow()
    current_tid = win32api.GetCurrentThreadId()
    foreground_tid = (
        win32process.GetWindowThreadProcessId(foreground)[0] if foreground else 0
    )
    attached = bool(
        foreground_tid
        and foreground_tid != current_tid
        and user32.AttachThreadInput(current_tid, foreground_tid, True)
    )
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
        win32gui.BringWindowToTop(hwnd)
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
    finally:
        if attached:
            user32.AttachThreadInput(current_tid, foreground_tid, False)

    if win32gui.GetForegroundWindow() != hwnd:
        try:
            user32.SwitchToThisWindow(hwnd, True)
        except Exception:
            pass
    time.sleep(0.08)
    return win32gui.GetForegroundWindow() == hwnd


def bring_to_front(hwnd):
    try:
        if not _force_foreground(hwnd):
            log("[!] 無法自動切到遊戲，請手動點一下遊戲視窗。")
    except Exception as exc:
        log(f"[!] 切換遊戲前景失敗：{exc}")


def capture_window(hwnd):
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        raise RuntimeError("遊戲視窗大小異常")

    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    try:
        bitmap.CreateCompatibleBitmap(mfc_dc, width, height)
        save_dc.SelectObject(bitmap)
        ctypes.windll.user32.PrintWindow(
            hwnd, save_dc.GetSafeHdc(), PW_RENDERFULLCONTENT
        )
        info = bitmap.GetInfo()
        bits = bitmap.GetBitmapBits(True)
        image = np.frombuffer(bits, dtype=np.uint8).reshape(
            (info["bmHeight"], info["bmWidth"], 4)
        )
        return np.ascontiguousarray(image[:, :, :3])
    finally:
        if bitmap.GetHandle():
            win32gui.DeleteObject(bitmap.GetHandle())
        save_dc.DeleteDC()
        mfc_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwnd_dc)


def client_box(hwnd):
    client_width, client_height = win32gui.GetClientRect(hwnd)[2:]
    client_x, client_y = win32gui.ClientToScreen(hwnd, (0, 0))
    window_left, window_top, _, _ = win32gui.GetWindowRect(hwnd)
    return (
        client_x - window_left,
        client_y - window_top,
        client_width,
        client_height,
    )


def capture_minimap(hwnd):
    image = capture_window(hwnd)
    offset_x, offset_y, width, height = client_box(hwnd)
    x0 = offset_x + int(width * MINIMAP_ROI["left"])
    x1 = offset_x + int(width * MINIMAP_ROI["right"])
    y0 = offset_y + int(height * MINIMAP_ROI["top"])
    y1 = offset_y + int(height * MINIMAP_ROI["bottom"])
    return image[y0:y1, x0:x1]


_UI_TEMPLATE_CACHE = {}
_ICON_TEMPLATE_CACHE = None
_FIRE_TEMPLATE_CACHE = None


def _load_ui_template(name):
    if name not in _UI_TEMPLATE_CACHE:
        path = os.path.join(TEMPLATE_DIR, name)
        image = cv2.imread(path, cv2.IMREAD_COLOR)
        if image is None:
            raise FileNotFoundError(f"讀不到樣板圖：{path}")
        _UI_TEMPLATE_CACHE[name] = image
    return _UI_TEMPLATE_CACHE[name]


def match_ui_template(image, name):
    template = _load_ui_template(name)
    template_height, template_width = template.shape[:2]
    if image.shape[0] < template_height or image.shape[1] < template_width:
        return 0, 0, 0.0
    result = cv2.matchTemplate(image, template, cv2.TM_CCOEFF_NORMED)
    _, confidence, _, location = cv2.minMaxLoc(result)
    return (
        location[0] + template_width // 2,
        location[1] + template_height // 2,
        confidence,
    )


def wait_for_ui(hwnd, name, timeout=None):
    started = time.monotonic()
    threshold = UI_THRESHOLDS[name]
    while True:
        check_stop()
        x, y, confidence = match_ui_template(capture_window(hwnd), name)
        if confidence >= threshold:
            log_step("✓", f"偵測到 {name}（信心 {confidence:.2f}）")
            return x, y
        if timeout is not None and time.monotonic() - started >= timeout:
            return None
        sleep_check(POLL_INTERVAL)


def campfire_prompt_visible(hwnd):
    """火堆導航的真實停止條件：畫面一出現 F 互動提示就立刻回傳。"""
    _, _, confidence = match_ui_template(capture_window(hwnd), "press_f.png")
    return confidence >= UI_THRESHOLDS["press_f.png"]


def wait_for_campfire_prompt(hwnd, timeout):
    """角色持續前進時高頻檢查提示，避免下一個導航 frame 才放開 W。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        sleep_check(min(CAMPFIRE_PROMPT_POLL_SECONDS, remaining))
        if campfire_prompt_visible(hwnd):
            return True
    return False


def _load_icon_templates():
    global _ICON_TEMPLATE_CACHE
    if _ICON_TEMPLATE_CACHE is not None:
        return _ICON_TEMPLATE_CACHE

    path = os.path.join(TEMPLATE_DIR, "sample.png")
    sample = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if sample is None:
        raise FileNotFoundError(f"讀不到小地圖圖示樣板：{path}")

    templates = {}
    for name, (x, y, width, height) in ICON_CROPS.items():
        templates[name] = sample[y:y + height, x:x + width]
    _ICON_TEMPLATE_CACHE = templates
    return templates


def find_icon(minimap, name):
    gray = cv2.cvtColor(minimap, cv2.COLOR_BGR2GRAY)
    source = _load_icon_templates()[name]
    best = (0.0, None)
    # sample.png 是說明用的大圖示；實際小地圖約為其 40%～60%。
    for scale in (0.40, 0.45, 0.50, 0.55, 0.60):
        template = cv2.resize(
            source, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA
        )
        template_height, template_width = template.shape[:2]
        if gray.shape[0] < template_height or gray.shape[1] < template_width:
            continue
        result = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
        _, confidence, _, location = cv2.minMaxLoc(result)
        if confidence > best[0]:
            best = (
                float(confidence),
                (
                    location[0] + template_width / 2,
                    location[1] + template_height / 2,
                ),
            )
    confidence, center = best
    return (center if confidence >= MATCH_THRESHOLD else None), confidence


def _colored_components(minimap, lower_hsv, upper_hsv):
    hsv = cv2.cvtColor(minimap, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array(lower_hsv), np.array(upper_hsv))
    mask = cv2.morphologyEx(
        mask, cv2.MORPH_CLOSE, np.ones((3, 3), dtype=np.uint8)
    )
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return mask, [contour for contour in contours if cv2.contourArea(contour) >= 8]


def find_player_pose(minimap):
    _, contours = _colored_components(
        minimap, (10, 55, 145), (45, 255, 255)
    )
    if not contours:
        return None

    map_center = np.array([minimap.shape[1] * 0.5, minimap.shape[0] * 0.5])
    candidates = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if not 18 <= area <= 650:
            continue
        moments = cv2.moments(contour)
        if not moments["m00"]:
            continue
        center = np.array(
            [moments["m10"] / moments["m00"], moments["m01"] / moments["m00"]]
        )
        candidates.append((np.linalg.norm(center - map_center), contour, center))
    if not candidates:
        return None

    _, contour, center = min(candidates, key=lambda item: item[0])
    points = contour.reshape(-1, 2).astype(np.float64)
    tip = points[np.argmax(np.linalg.norm(points - center, axis=1))]
    heading = math.atan2(tip[1] - center[1], tip[0] - center[0])
    return float(center[0]), float(center[1]), heading


def find_campfire(minimap):
    global _FIRE_TEMPLATE_CACHE
    if _FIRE_TEMPLATE_CACHE is None:
        path = os.path.join(TEMPLATE_DIR, "fire.png")
        _FIRE_TEMPLATE_CACHE = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if _FIRE_TEMPLATE_CACHE is None:
            raise FileNotFoundError(f"讀不到火堆樣板圖：{path}")

    gray = cv2.cvtColor(minimap, cv2.COLOR_BGR2GRAY)
    best = (0.0, None)
    for scale in (0.70, 0.75, 0.80, 0.85, 0.90):
        template = cv2.resize(
            _FIRE_TEMPLATE_CACHE,
            None,
            fx=scale,
            fy=scale,
            interpolation=cv2.INTER_AREA,
        )
        height, width = template.shape[:2]
        result = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
        _, confidence, _, location = cv2.minMaxLoc(result)
        if confidence > best[0]:
            best = (
                float(confidence),
                (location[0] + width / 2, location[1] + height / 2),
            )
    confidence, center = best
    return (center if confidence >= FIRE_MATCH_THRESHOLD else None), confidence


def observe_target(hwnd, target_name):
    minimap = capture_minimap(hwnd)
    pose = find_player_pose(minimap)
    if target_name == "campfire":
        target, confidence = find_campfire(minimap)
    else:
        target, confidence = find_icon(minimap, target_name)
    return pose, target, confidence


def normalize_angle(angle):
    return (angle + math.pi) % (2 * math.pi) - math.pi


def heading_error_to_target(pose, target):
    player_x, player_y, heading = pose
    target_angle = math.atan2(target[1] - player_y, target[0] - player_x)
    return normalize_angle(target_angle - heading)


def steer_toward(pose, target):
    error = heading_error_to_target(pose, target)
    if abs(error) <= TURN_DEAD_ZONE:
        return error
    pixels = int(max(-TURN_MAX_PIXELS, min(TURN_MAX_PIXELS, error * TURN_PIXELS_PER_RADIAN)))
    win32api.mouse_event(MOUSEEVENTF_MOVE, pixels, 0, 0, 0)
    return error


def target_distance(pose, target):
    return math.hypot(target[0] - pose[0], target[1] - pose[1])


def navigate_to(hwnd, target_name, dodge=False, deadline=None):
    label = TARGET_LABELS[target_name]
    mode = "閃避移動" if dodge else "移動"
    arrival_distance = (
        CAMPFIRE_ARRIVAL_DISTANCE
        if target_name == "campfire"
        else ARRIVAL_DISTANCE
    )
    log_step("➜", f"{mode}至{label}")
    best_distance = float("inf")
    last_distance = None
    lost_count = 0
    last_status = 0.0
    walking = False

    def hold_w():
        nonlocal walking
        if not walking:
            keyboard.press("w")
            walking = True

    def release_w():
        nonlocal walking
        if walking:
            keyboard.release("w")
            walking = False

    def finish_marker(message):
        """一般地標命中後再往前穿越一小段，避免圖示剛重疊就過早結束。"""
        log_step("✓", message)
        hold_w()
        duration = MARKER_CROSS_SECONDS
        if deadline is not None:
            duration = min(duration, max(0.0, deadline - time.monotonic()))
        if duration > 0:
            sleep_check(duration)
        return True

    try:
        while deadline is None or time.monotonic() < deadline:
            check_stop()
            if target_name == "campfire" and campfire_prompt_visible(hwnd):
                log_step("✓", "偵測到 F 互動提示，已抵達火堆")
                return True
            pose, target, confidence = observe_target(hwnd, target_name)
            if pose is None:
                release_w()
                sleep_check(POLL_INTERVAL)
                continue
            if target is None:
                lost_count += 1
                if (
                    target_name == "campfire"
                    and last_distance is not None
                    and last_distance <= CAMPFIRE_OCCLUSION_DISTANCE
                    and lost_count <= CAMPFIRE_OCCLUSION_STEPS
                ):
                    if lost_count == 1:
                        log("    …火堆圖示被角色遮住，沿最後方向等待 F 提示")
                    hold_w()
                    if wait_for_campfire_prompt(
                        hwnd, CAMPFIRE_OCCLUSION_STEP_SECONDS
                    ):
                        log_step("✓", "偵測到 F 互動提示，已抵達火堆")
                        return True
                    continue

                release_w()
                if (
                    target_name != "campfire"
                    and last_distance is not None
                    and last_distance <= ICON_OCCLUSION_DISTANCE
                ):
                    return finish_marker(f"已抵達{label}（圖示被角色遮住）")
                if lost_count == TARGET_LOST_LIMIT:
                    log(
                        f"    …暫時找不到{label}（最高信心 {confidence:.2f}），"
                        "持續重試"
                    )
                sleep_check(POLL_INTERVAL)
                continue

            lost_count = 0
            distance = target_distance(pose, target)
            if distance <= arrival_distance:
                if target_name == "campfire":
                    log_step("✓", f"已抵達{label}（距離 {distance:.1f}px）")
                    return True
                return finish_marker(f"已抵達{label}（距離 {distance:.1f}px）")
            if (
                target_name != "campfire"
                and best_distance <= PASS_DISTANCE
                and distance >= best_distance + PASS_MARGIN
            ):
                return finish_marker(
                    f"已通過{label}（最近距離 {best_distance:.1f}px）"
                )

            best_distance = min(best_distance, distance)
            last_distance = distance
            heading_error = heading_error_to_target(pose, target)
            if abs(heading_error) > MOVE_ALIGNMENT_TOLERANCE:
                release_w()
                steer_toward(pose, target)
                hold_key_for("w", TURN_PROBE_TAP_SECONDS)
                sleep_check(TURN_SETTLE_SECONDS)
                continue

            now = time.monotonic()
            if now - last_status >= 3:
                last_status = now
                log(f"    …距離{label} {distance:.1f}px")

            if dodge:
                keyboard.press("w")
                try:
                    keyboard.press_and_release("shift")
                finally:
                    keyboard.release("w")
                remaining = (
                    max(0.0, deadline - time.monotonic())
                    if deadline
                    else DODGE_SETTLE_SECONDS
                )
                sleep_check(min(DODGE_SETTLE_SECONDS, remaining))
            else:
                hold_w()
                if target_name == "campfire":
                    if wait_for_campfire_prompt(hwnd, MOVE_STEP_SECONDS):
                        log_step("✓", "偵測到 F 互動提示，已抵達火堆")
                        return True
                else:
                    sleep_check(MOVE_STEP_SECONDS)
        return False
    finally:
        release_w()
        if dodge:
            # 若 Shift 送出期間中止，也確保不會留下按鍵狀態。
            keyboard.release("shift")


def tap_key(key, settle=0.0):
    check_stop()
    keyboard.press_and_release(key)
    if settle:
        sleep_check(settle)


def click_window_at(hwnd, x, y):
    left, top, _, _ = win32gui.GetWindowRect(hwnd)
    screen_x, screen_y = left + int(x), top + int(y)
    win32api.SetCursorPos((screen_x, screen_y))
    sleep_check(0.08)
    win32api.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    sleep_check(0.04)
    win32api.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def rest_and_refresh(hwnd):
    log_step("◆", "靠近火堆，等待互動提示")
    prompt = wait_for_ui(hwnd, "press_f.png", timeout=8.0)
    if prompt is None:
        log("    未看到 F 提示，重新校正火堆位置")
        navigate_to(hwnd, "campfire")
        prompt = wait_for_ui(hwnd, "press_f.png")

    log_step("▸", "按 F 進入休息")
    tap_key("f", 0.5)
    log_step("⋯", "等待『稍作休息』按鈕")
    button = wait_for_ui(hwnd, "mouse_click.png")
    click_window_at(hwnd, *button)
    sleep_check(0.5)
    log_step("▸", "按 ESC 關閉休息畫面")
    tap_key("esc", 0.5)
    log_step("◀", f"退離火堆 {CAMPFIRE_BACK_AWAY_SECONDS:.1f} 秒 (S)")
    hold_key_for("s", CAMPFIRE_BACK_AWAY_SECONDS)
    log_step("▸", "短按 W 恢復角色正面箭頭")
    hold_key_for("w", TURN_PROBE_TAP_SECONDS)
    sleep_check(TURN_SETTLE_SECONDS)


def run_dodge_loop(hwnd):
    deadline = time.monotonic() + DODGE_DURATION_SECONDS
    target = "route_1"
    log_step("⏱", f"開始 {DODGE_DURATION_SECONDS:.0f} 秒折返閃避")
    while time.monotonic() < deadline:
        reached = navigate_to(hwnd, target, dodge=True, deadline=deadline)
        if not reached:
            break
        target = "route_2" if target == "route_1" else "route_1"
    release_movement_keys()
    log_step("✓", "60 秒折返閃避結束")


def run_loop(hwnd):
    global _round
    while True:
        log(f"\n===== 第 {_round + 1} 輪開始 =====")
        navigate_to(hwnd, "campfire")
        rest_and_refresh(hwnd)
        navigate_to(hwnd, "door")
        navigate_to(hwnd, "boss")
        run_dodge_loop(hwnd)
        navigate_to(hwnd, "boss")
        navigate_to(hwnd, "door")
        _round += 1
        log_step("✔", f"第 {_round} 輪完成，開始下一輪")


def main():
    if os.environ.get("NTE_INPUT_MODE", "foreground").lower() != "foreground":
        log("[!] 九百九十九夜腳本只支援前台模式。")
        return

    hwnd = find_nte_window()
    if not hwnd:
        log("[!] 找不到 NTE 視窗，請確認遊戲正在執行。")
        return
    log(f"[✓] 找到遊戲視窗 hwnd={hwnd}")
    log("[模式] 前台：腳本會使用真實鍵盤與滑鼠，請勿操作其他視窗。")
    _, _, client_width, client_height = client_box(hwnd)
    if (client_width, client_height) != (1920, 1080):
        log(
            f"[!] 目前遊戲畫面為 {client_width} × {client_height}；"
            "本腳本以 1920 × 1080 製作，辨識可能不準確。"
        )

    watcher = threading.Thread(target=_key_watcher, daemon=True)
    watcher.start()
    try:
        while True:
            wait_for_start()
            bring_to_front(hwnd)
            try:
                run_loop(hwnd)
            except StopRun:
                release_movement_keys()
                log("[F2] 已停止並放開移動鍵，回到待機。")
            except Exception as exc:
                release_movement_keys()
                log(f"[錯誤] {type(exc).__name__}: {exc}")
                log("       已安全停止並回到待機，可修正狀態後再按 F1。")
    except KeyboardInterrupt:
        log("\n[中止] 關閉腳本。")
    finally:
        release_movement_keys()
        _EXIT.set()


if __name__ == "__main__":
    main()
