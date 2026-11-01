"""
assistant/system_control.py
============================
Advanced Windows system control module for the Jarvis assistant.

Provides human-readable control over:
  - Audio volume (set / adjust / mute / get)  via pycaw → comtypes fallback
  - Screen brightness                          via screen_brightness_control / wmi
  - Battery status                             via psutil
  - Disk usage (all drives)                   via psutil
  - Network connectivity status               via socket / requests
  - Windows Settings pages                    via ms-settings: URI scheme
  - Lock workstation                           via ctypes LockWorkStation
  - Power actions (shutdown/restart/sleep/     via subprocess - REQUIRE confirmed=True
    signout)

Every public function returns a plain ``str`` (human-readable result).
No unhandled exceptions escape this module.

Tool schemas and callable dict are exported at the bottom as
``SYSTEM_CONTROL_TOOLS_SCHEMA`` and ``SYSTEM_CONTROL_AVAILABLE_TOOLS``
so they can be merged into the main agent's tool registry.
"""

from __future__ import annotations

import ctypes
import os
import socket
import subprocess
import sys
from typing import Optional

import psutil
import requests

# ---------------------------------------------------------------------------
# Optional: pycaw (preferred) → comtypes Core Audio fallback
# ---------------------------------------------------------------------------
_PYCAW_AVAILABLE = False
_COMTYPES_AUDIO_AVAILABLE = False

try:
    from ctypes import cast, POINTER  # noqa: F401 – used by pycaw helpers too
    from comtypes import CLSCTX_ALL  # type: ignore
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume  # type: ignore

    _PYCAW_AVAILABLE = True
except ImportError:
    pass

if not _PYCAW_AVAILABLE:
    # Attempt raw comtypes fallback (comtypes is installed even when pycaw isn't)
    try:
        import comtypes  # type: ignore
        import comtypes.client  # type: ignore
        from ctypes import cast, POINTER  # noqa: F811

        # Core Audio GUIDs & interfaces we need
        _IID_IAudioEndpointVolume = comtypes.GUID(
            "{5CDF2C82-841E-4546-9722-0CF74078229A}"
        )
        _CLSID_MMDeviceEnumerator = comtypes.GUID(
            "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
        )

        # IMMDeviceEnumerator interface (minimal subset)
        class IMMDevice(comtypes.IUnknown):  # type: ignore
            _iid_ = comtypes.GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
            _methods_ = [
                comtypes.STDMETHOD(
                    comtypes.HRESULT,
                    "Activate",
                    [
                        ctypes.POINTER(comtypes.GUID),
                        ctypes.c_uint,
                        ctypes.c_void_p,
                        ctypes.POINTER(ctypes.c_void_p),
                    ],
                ),
            ]

        class IMMDeviceEnumerator(comtypes.IUnknown):  # type: ignore
            _iid_ = comtypes.GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
            _methods_ = [
                comtypes.STDMETHOD(
                    comtypes.HRESULT,
                    "EnumAudioEndpoints",
                    [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p],
                ),
                comtypes.STDMETHOD(
                    comtypes.HRESULT,
                    "GetDefaultAudioEndpoint",
                    [
                        ctypes.c_uint,
                        ctypes.c_uint,
                        ctypes.POINTER(ctypes.POINTER(IMMDevice)),
                    ],
                ),
            ]

        # IAudioEndpointVolume (minimal)
        class IAudioEndpointVolume(comtypes.IUnknown):  # type: ignore
            _iid_ = _IID_IAudioEndpointVolume
            _methods_ = [
                comtypes.STDMETHOD(comtypes.HRESULT, "RegisterControlChangeNotify", [ctypes.c_void_p]),
                comtypes.STDMETHOD(comtypes.HRESULT, "UnregisterControlChangeNotify", [ctypes.c_void_p]),
                comtypes.STDMETHOD(comtypes.HRESULT, "GetChannelCount", [ctypes.POINTER(ctypes.c_uint)]),
                comtypes.STDMETHOD(comtypes.HRESULT, "SetMasterVolumeLevel", [ctypes.c_float, ctypes.c_void_p]),
                comtypes.STDMETHOD(comtypes.HRESULT, "SetMasterVolumeLevelScalar", [ctypes.c_float, ctypes.c_void_p]),
                comtypes.STDMETHOD(comtypes.HRESULT, "GetMasterVolumeLevel", [ctypes.POINTER(ctypes.c_float)]),
                comtypes.STDMETHOD(comtypes.HRESULT, "GetMasterVolumeLevelScalar", [ctypes.POINTER(ctypes.c_float)]),
                comtypes.STDMETHOD(comtypes.HRESULT, "SetChannelVolumeLevel", [ctypes.c_uint, ctypes.c_float, ctypes.c_void_p]),
                comtypes.STDMETHOD(comtypes.HRESULT, "SetChannelVolumeLevelScalar", [ctypes.c_uint, ctypes.c_float, ctypes.c_void_p]),
                comtypes.STDMETHOD(comtypes.HRESULT, "GetChannelVolumeLevel", [ctypes.c_uint, ctypes.POINTER(ctypes.c_float)]),
                comtypes.STDMETHOD(comtypes.HRESULT, "GetChannelVolumeLevelScalar", [ctypes.c_uint, ctypes.POINTER(ctypes.c_float)]),
                comtypes.STDMETHOD(comtypes.HRESULT, "SetMute", [ctypes.c_bool, ctypes.c_void_p]),
                comtypes.STDMETHOD(comtypes.HRESULT, "GetMute", [ctypes.POINTER(ctypes.c_bool)]),
                comtypes.STDMETHOD(comtypes.HRESULT, "GetVolumeStepInfo", [ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)]),
                comtypes.STDMETHOD(comtypes.HRESULT, "VolumeStepUp", [ctypes.c_void_p]),
                comtypes.STDMETHOD(comtypes.HRESULT, "VolumeStepDown", [ctypes.c_void_p]),
                comtypes.STDMETHOD(comtypes.HRESULT, "QueryHardwareSupport", [ctypes.POINTER(ctypes.c_uint)]),
                comtypes.STDMETHOD(comtypes.HRESULT, "GetVolumeRange", [ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float)]),
            ]

        _COMTYPES_AUDIO_AVAILABLE = True
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Optional: screen_brightness_control → wmi fallback
# ---------------------------------------------------------------------------
_SBC_AVAILABLE = False
_WMI_AVAILABLE = False

try:
    import screen_brightness_control as sbc  # type: ignore  # noqa: F401
    _SBC_AVAILABLE = True
except ImportError:
    pass

try:
    import wmi as _wmi  # type: ignore  # noqa: F401
    _WMI_AVAILABLE = True
except ImportError:
    pass


# ===========================================================================
# INTERNAL HELPERS
# ===========================================================================

def _get_audio_endpoint_volume():
    """
    Return an IAudioEndpointVolume interface for the default audio output.

    Tries pycaw first; if unavailable, falls back to raw comtypes.
    Returns ``None`` on failure.
    """
    if _PYCAW_AVAILABLE:
        try:
            from ctypes import cast, POINTER  # noqa: F811
            from comtypes import CLSCTX_ALL  # type: ignore
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume as _IAudioEV  # type: ignore

            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(_IAudioEV._iid_, CLSCTX_ALL, None)
            return cast(interface, POINTER(_IAudioEV))
        except Exception:
            pass

    if _COMTYPES_AUDIO_AVAILABLE:
        try:
            comtypes.CoInitialize()
            enumerator = comtypes.CoCreateInstance(
                _CLSID_MMDeviceEnumerator,
                IMMDeviceEnumerator,
                comtypes.CLSCTX_INPROC_SERVER,
            )
            # eRender=0, eMultimedia=1
            device_ptr = ctypes.POINTER(IMMDevice)()
            enumerator.GetDefaultAudioEndpoint(0, 1, ctypes.byref(device_ptr))

            vol_ptr = ctypes.c_void_p()
            device_ptr.Activate(
                ctypes.byref(_IID_IAudioEndpointVolume),
                comtypes.CLSCTX_ALL,
                None,
                ctypes.byref(vol_ptr),
            )
            return ctypes.cast(vol_ptr, ctypes.POINTER(IAudioEndpointVolume))
        except Exception:
            pass

    return None


# ===========================================================================
# VOLUME CONTROL
# ===========================================================================

def _set_volume_impl(level: int) -> bool:
    """Internal helper to set volume using COM endpoint."""
    vol = _get_audio_endpoint_volume()
    if vol is not None:
        scalar = level / 100.0
        vol.SetMasterVolumeLevelScalar(scalar, None)
        return True
    return False


def set_volume(level: int) -> str:
    """
    Set the system master volume to the specified level.

    :param level: Integer 0–100 representing the desired volume percentage.
    :returns: Human-readable confirmation or error string.
    """
    try:
        level_int = int(level)
    except (ValueError, TypeError):
        return "Invalid volume level: must be an integer between 0 and 100."

    if level_int < 0 or level_int > 100:
        return f"Invalid volume level {level_int}. Volume must be between 0 and 100."

    try:
        if _set_volume_impl(level_int):
            return f"Volume set to {level_int}%."

        # Last-resort: send media-key events via ctypes (Windows)
        return _set_volume_keypress_fallback(level_int)

    except Exception as exc:
        return f"Failed to set volume: {exc}"


def _set_volume_keypress_fallback(target_level: int) -> str:
    """
    Approximate volume setting by pressing virtual media keys.

    First mutes then unmutes isn't reliable, so we estimate key presses.
    This is genuinely a best-effort fallback when no COM audio API is
    accessible (e.g., a headless service session).
    """
    try:
        # VK_VOLUME_MUTE = 0xAD, VK_VOLUME_DOWN = 0xAE, VK_VOLUME_UP = 0xAF
        # Mute then set approximate level via key presses (each ≈ 2%)
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        KEYEVENTF_KEYUP = 0x0002
        VK_VOLUME_DOWN = 0xAE
        VK_VOLUME_UP = 0xAF

        # Press Volume Down 50 times to reach ~0%, then up to target
        for _ in range(50):
            user32.keybd_event(VK_VOLUME_DOWN, 0, 0, 0)
            user32.keybd_event(VK_VOLUME_DOWN, 0, KEYEVENTF_KEYUP, 0)

        steps_up = round(target_level / 2)
        for _ in range(steps_up):
            user32.keybd_event(VK_VOLUME_UP, 0, 0, 0)
            user32.keybd_event(VK_VOLUME_UP, 0, KEYEVENTF_KEYUP, 0)

        return (
            f"Volume approximately set to {target_level}% via keyboard simulation "
            "(Core Audio API was unavailable)."
        )
    except Exception as exc:
        return (
            f"Could not set volume - Core Audio API and keyboard fallback both failed: {exc}. "
            "Please install 'pycaw' for full volume control: pip install pycaw"
        )


def adjust_volume(direction: str, amount: int = 10) -> str:
    """
    Adjust the system master volume up or down by a given percentage amount.

    :param direction: ``"up"`` to increase, ``"down"`` to decrease.
    :param amount:    Percentage points to change (default 10).
    :returns: Human-readable confirmation or error string.
    """
    try:
        direction = str(direction).strip().lower()
        amount = max(1, min(100, int(amount)))

        if direction not in ("up", "down"):
            return f"Invalid direction '{direction}'. Use 'up' or 'down'."

        vol = _get_audio_endpoint_volume()
        if vol is not None:
            current_scalar = ctypes.c_float()
            vol.GetMasterVolumeLevelScalar(ctypes.byref(current_scalar))
            current_pct = round(current_scalar.value * 100)

            if direction == "up":
                new_pct = min(100, current_pct + amount)
            else:
                new_pct = max(0, current_pct - amount)

            vol.SetMasterVolumeLevelScalar(new_pct / 100.0, None)
            return f"Volume {'increased' if direction == 'up' else 'decreased'} by {amount}% -> now at {new_pct}%."

        # Fallback: keypress
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        KEYEVENTF_KEYUP = 0x0002
        VK_KEY = 0xAF if direction == "up" else 0xAE  # UP / DOWN

        steps = max(1, round(amount / 2))
        for _ in range(steps):
            user32.keybd_event(VK_KEY, 0, 0, 0)
            user32.keybd_event(VK_KEY, 0, KEYEVENTF_KEYUP, 0)

        return (
            f"Volume {'increased' if direction == 'up' else 'decreased'} by ~{amount}% "
            "via keyboard simulation (Core Audio API unavailable)."
        )

    except Exception as exc:
        return f"Failed to adjust volume: {exc}"


def mute_volume(mute: bool = True) -> str:
    """
    Mute or unmute the system master volume.

    :param mute: ``True`` to mute, ``False`` to unmute.
    :returns: Human-readable confirmation or error string.
    """
    try:
        vol = _get_audio_endpoint_volume()
        if vol is not None:
            vol.SetMute(mute, None)
            action = "muted" if mute else "unmuted"
            return f"Audio has been {action}."

        # Fallback: VK_VOLUME_MUTE keypress
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        VK_VOLUME_MUTE = 0xAD
        KEYEVENTF_KEYUP = 0x0002

        # Read current mute state isn't possible without COM; just toggle once
        user32.keybd_event(VK_VOLUME_MUTE, 0, 0, 0)
        user32.keybd_event(VK_VOLUME_MUTE, 0, KEYEVENTF_KEYUP, 0)

        action = "Mute" if mute else "Unmute"
        return (
            f"{action} key sent via keyboard simulation "
            "(Core Audio API unavailable - actual mute state may differ)."
        )

    except Exception as exc:
        return f"Failed to {'mute' if mute else 'unmute'} volume: {exc}"


def get_volume() -> str:
    """
    Report the current system master volume level and mute state.

    :returns: Human-readable volume report string.
    """
    try:
        vol = _get_audio_endpoint_volume()
        if vol is not None:
            scalar = ctypes.c_float()
            vol.GetMasterVolumeLevelScalar(ctypes.byref(scalar))
            level_pct = round(scalar.value * 100)

            muted = ctypes.c_bool()
            vol.GetMute(ctypes.byref(muted))
            mute_str = " (muted)" if muted.value else ""
            return f"Current volume: {level_pct}%{mute_str}."

        return (
            "Cannot read volume level - Core Audio API is unavailable. "
            "Install 'pycaw' for full support: pip install pycaw"
        )

    except Exception as exc:
        return f"Failed to get volume: {exc}"


# ===========================================================================
# SCREEN BRIGHTNESS
# ===========================================================================

def set_brightness(level: int) -> str:
    """
    Set the screen brightness to the specified percentage.

    Requires screen_brightness_control or wmi.
    On desktop systems or displays without a software-controllable backlight
    this operation may not be supported.

    :param level: Integer 0-100 representing brightness percentage.
    :returns: Human-readable confirmation, 'not supported' notice, or error.
    """
    try:
        level_int = int(level)
    except (ValueError, TypeError):
        return "Invalid brightness level: must be an integer between 0 and 100."

    if level_int < 0 or level_int > 100:
        return f"Invalid brightness level {level_int}. Brightness must be between 0 and 100."

    if _SBC_AVAILABLE:
        try:
            import screen_brightness_control as _sbc  # type: ignore

            _sbc.set_brightness(level_int)
            return f"Screen brightness set to {level_int}%."
        except Exception:
            pass

    if _WMI_AVAILABLE:
        try:
            import wmi as _wmi  # type: ignore

            w = _wmi.WMI(namespace="wmi")
            methods = w.WmiMonitorBrightnessMethods()
            if methods:
                methods[0].WmiSetBrightness(level_int, 0)
                return f"Screen brightness set to {level_int}% via WMI."
        except Exception:
            pass

    return (
        "Screen brightness control is not supported on this display hardware "
        "(no controllable backlight detected - desktop external monitors typically require physical buttons or DDC/CI)."
    )


def get_brightness() -> str:
    """
    Get the current screen brightness percentage.

    :returns: Human-readable brightness string or unsupported notice.
    """
    if _SBC_AVAILABLE:
        try:
            import screen_brightness_control as _sbc  # type: ignore

            cur = _sbc.get_brightness()
            if isinstance(cur, list) and cur:
                return f"Current screen brightness is {cur[0]}%."
            elif isinstance(cur, (int, float)):
                return f"Current screen brightness is {int(cur)}%."
        except Exception:
            pass

    if _WMI_AVAILABLE:
        try:
            import wmi as _wmi  # type: ignore

            w = _wmi.WMI(namespace="wmi")
            b = w.WmiMonitorBrightness()
            if b:
                return f"Current screen brightness is {b[0].CurrentBrightness}%."
        except Exception:
            pass

    return (
        "Brightness detection is not supported on this display hardware "
        "(no controllable backlight detected - desktop monitors typically require physical buttons or DDC/CI)."
    )


def adjust_brightness(direction: str, amount: int = 10) -> str:
    """
    Adjust screen brightness up or down by a given percentage amount.

    :param direction: 'up' (increase) or 'down' (decrease).
    :param amount: Percentage points to change (default 10).
    :returns: Human-readable confirmation or unsupported notice.
    """
    direction = str(direction).strip().lower()
    if direction not in ("up", "down"):
        return f"Invalid direction '{direction}'. Use 'up' or 'down'."

    try:
        amount = max(1, min(100, int(amount)))
    except (ValueError, TypeError):
        amount = 10

    current_level = None
    if _SBC_AVAILABLE:
        try:
            import screen_brightness_control as _sbc  # type: ignore

            cur = _sbc.get_brightness()
            if isinstance(cur, list) and cur:
                current_level = int(cur[0])
            elif isinstance(cur, (int, float)):
                current_level = int(cur)
        except Exception:
            pass

    if current_level is None and _WMI_AVAILABLE:
        try:
            import wmi as _wmi  # type: ignore

            w = _wmi.WMI(namespace="wmi")
            b = w.WmiMonitorBrightness()
            if b:
                current_level = int(b[0].CurrentBrightness)
        except Exception:
            pass

    if current_level is not None:
        if direction == "up":
            target = min(100, current_level + amount)
        else:
            target = max(0, current_level - amount)
        res = set_brightness(target)
        if "set to" in res.lower():
            action = "increased" if direction == "up" else "decreased"
            return f"Brightness {action} by {amount}% -> now at {target}%."
        return res

    test_target = 60 if direction == "up" else 40
    res = set_brightness(test_target)
    if "not supported" in res.lower() or "not available" in res.lower() or "failed" in res.lower():
        return res
    action = "increased" if direction == "up" else "decreased"
    return f"Brightness {action} by ~{amount}%."


# ===========================================================================
# BATTERY STATUS
# ===========================================================================

def get_battery_status() -> str:
    """
    Report the current battery level, charging status, and time remaining.

    :returns: Human-readable battery report or 'no battery' notice.
    """
    try:
        battery = psutil.sensors_battery()
        if battery is None:
            return "No battery detected - this system appears to be a desktop PC or battery reporting is unavailable."

        pct = battery.percent
        plugged = battery.power_plugged
        secs_left = battery.secsleft  # may be psutil.POWER_TIME_UNKNOWN / UNLIMITED

        status = "plugged in and charging" if plugged else "on battery power"
        msg = f"Battery is at {pct:.0f}%, {status}."

        if not plugged:
            if secs_left == psutil.POWER_TIME_UNKNOWN:
                msg += " Time remaining is unknown."
            elif secs_left == psutil.POWER_TIME_UNLIMITED:
                msg += " Power time is unlimited (AC connected)."
            else:
                hours, remainder = divmod(int(secs_left), 3600)
                minutes = remainder // 60
                if hours > 0:
                    msg += f" Approximately {hours}h {minutes}m remaining."
                else:
                    msg += f" Approximately {minutes} minutes remaining."

        if pct < 15 and not plugged:
            msg += " ⚠️ Battery is critically low - please plug in soon."
        elif pct < 30 and not plugged:
            msg += " Consider plugging in soon."

        return msg

    except Exception as exc:
        return f"Failed to retrieve battery status: {exc}"


# ===========================================================================
# DISK USAGE
# ===========================================================================

def get_disk_usage() -> str:
    """
    Report disk space usage for every mounted physical drive/partition.

    :returns: Human-readable multi-line disk usage report.
    """
    try:
        partitions = psutil.disk_partitions(all=False)
        if not partitions:
            return "No disk partitions found."

        lines: list[str] = ["Disk usage:"]
        for part in partitions:
            try:
                usage = psutil.disk_usage(part.mountpoint)
                total_gb = usage.total / (1024 ** 3)
                used_gb = usage.used / (1024 ** 3)
                free_gb = usage.free / (1024 ** 3)
                pct = usage.percent
                lines.append(
                    f"  {part.device} ({part.mountpoint}) - "
                    f"{used_gb:.1f} GB used / {total_gb:.1f} GB total "
                    f"({pct:.0f}% full, {free_gb:.1f} GB free)"
                )
            except PermissionError:
                lines.append(f"  {part.device} ({part.mountpoint}) - access denied.")
            except Exception as inner_exc:
                lines.append(f"  {part.device} ({part.mountpoint}) - error: {inner_exc}")

        return "\n".join(lines)

    except Exception as exc:
        return f"Failed to retrieve disk usage: {exc}"


# ===========================================================================
# NETWORK STATUS
# ===========================================================================

def get_network_status() -> str:
    """
    Report network connectivity and basic interface statistics.

    Checks:
    1. Whether any network interface is up.
    2. DNS resolution (Google's public DNS hostname).
    3. HTTP reachability (Google homepage, brief timeout).

    :returns: Human-readable network status string.
    """
    lines: list[str] = []

    try:
        # --- Interface summary ---
        net_io = psutil.net_io_counters(pernic=False)
        addrs = psutil.net_if_addrs()
        stats = psutil.net_if_stats()

        active_ifaces = [name for name, st in stats.items() if st.isup]
        if active_ifaces:
            lines.append(f"Active network interfaces: {', '.join(active_ifaces)}.")
        else:
            lines.append("No active network interfaces found.")

        # --- Bytes transferred ---
        mb_sent = net_io.bytes_sent / (1024 ** 2)
        mb_recv = net_io.bytes_recv / (1024 ** 2)
        lines.append(f"Data this session - Sent: {mb_sent:.1f} MB, Received: {mb_recv:.1f} MB.")

    except Exception as exc:
        lines.append(f"Interface info unavailable: {exc}")

    # --- DNS resolution ---
    try:
        socket.setdefaulttimeout(3)
        socket.gethostbyname("dns.google")
        lines.append("DNS resolution: ✔ Working.")
    except Exception:
        lines.append("DNS resolution: ✘ Failed (may be offline).")

    # --- HTTP reachability ---
    try:
        resp = requests.get("https://www.google.com", timeout=4)
        if resp.status_code < 400:
            lines.append("Internet connectivity: ✔ Online (reached google.com).")
        else:
            lines.append(f"Internet connectivity: ⚠ Reachable but HTTP {resp.status_code}.")
    except requests.ConnectionError:
        lines.append("Internet connectivity: ✘ Offline (cannot reach google.com).")
    except requests.Timeout:
        lines.append("Internet connectivity: ⚠ Request timed out.")
    except Exception as exc:
        lines.append(f"Internet connectivity check failed: {exc}")

    return "\n".join(lines)


# ===========================================================================
# WINDOWS SETTINGS
# ===========================================================================

#: Mapping of friendly page names to ms-settings: URIs
_SETTINGS_PAGES: dict[str, str] = {
    "home":       "ms-settings:",
    "wifi":       "ms-settings:network-wifi",
    "bluetooth":  "ms-settings:bluetooth",
    "display":    "ms-settings:display",
    "sound":      "ms-settings:sound",
    "apps":       "ms-settings:appsfeatures",
    "update":     "ms-settings:windowsupdate",
    "power":      "ms-settings:powersleep",
    "privacy":    "ms-settings:privacy",
    "accounts":   "ms-settings:accounts",
    "time":       "ms-settings:dateandtime",
    "region":     "ms-settings:regionlanguage",
    "storage":    "ms-settings:storagesense",
    "taskbar":    "ms-settings:taskbar",
    "themes":     "ms-settings:themes",
    "mouse":      "ms-settings:mousetouchpad",
    "keyboard":   "ms-settings:typing",
    "camera":     "ms-settings:camera",
    "battery":    "ms-settings:batterysaver",
}


def open_settings(page: str = "home") -> str:
    """
    Open a Windows Settings page using the ``ms-settings:`` URI scheme.

    :param page: Friendly page name. Supported values:
                 home, wifi, bluetooth, display, sound, apps, update, power,
                 privacy, accounts, time, region, storage, taskbar, themes,
                 mouse, keyboard, camera, battery.
    :returns: Human-readable confirmation or error string.
    """
    try:
        page_key = str(page).strip().lower()
        uri = _SETTINGS_PAGES.get(page_key)

        if uri is None:
            available = ", ".join(sorted(_SETTINGS_PAGES.keys()))
            return (
                f"Unknown settings page '{page}'. "
                f"Available pages: {available}."
            )

        subprocess.Popen(["explorer.exe", uri], shell=False)
        return f"Opened Windows Settings → {page_key} ({uri})."

    except Exception as exc:
        return f"Failed to open Settings page '{page}': {exc}"


# ===========================================================================
# LOCK COMPUTER
# ===========================================================================

def lock_computer() -> str:
    """
    Lock the Windows workstation immediately (equivalent to Win+L).

    :returns: Confirmation string (executes before control returns, so the
              string is typically returned before the screen actually locks).
    """
    try:
        result = ctypes.windll.user32.LockWorkStation()
        if result:
            return "Workstation locked. Press Ctrl+Alt+Delete or enter your PIN/password to unlock."
        else:
            err = ctypes.get_last_error()
            return f"LockWorkStation call failed (error code {err})."
    except Exception as exc:
        return f"Failed to lock workstation: {exc}"


# ===========================================================================
# POWER ACTIONS  (require confirmed=True)
# ===========================================================================

#: Warning/preview messages shown when confirmed=False
_POWER_WARNINGS: dict[str, str] = {
    "shutdown": (
        "⚠️  SHUTDOWN WARNING: This will immediately shut down the computer. "
        "All unsaved work will be lost. "
        "To proceed, call power_action with confirmed=True."
    ),
    "restart": (
        "⚠️  RESTART WARNING: This will immediately restart the computer. "
        "All unsaved work will be lost. "
        "To proceed, call power_action with confirmed=True."
    ),
    "sleep": (
        "💤  SLEEP PREVIEW: This will put the computer to sleep. "
        "Your session will be preserved in RAM. "
        "To proceed, call power_action with confirmed=True."
    ),
    "signout": (
        "🔑  SIGN-OUT WARNING: This will sign out the current Windows user. "
        "All unsaved work in open apps will be lost. "
        "To proceed, call power_action with confirmed=True."
    ),
}


def power_action(action: str, confirmed: bool = False) -> str:
    """
    Execute a system power or session management action.

    **Safety gate**: Every destructive action requires ``confirmed=True``.
    If ``confirmed`` is ``False``, a warning/preview string is returned and
    nothing is executed.

    :param action:    One of ``shutdown``, ``restart``, ``sleep``, ``signout``.
    :param confirmed: Must be ``True`` to actually execute the action.
    :returns: Warning string (if unconfirmed) or execution result.

    Examples::

        power_action("shutdown")           # returns warning only
        power_action("shutdown", True)     # actually shuts down
    """
    try:
        action_key = str(action).strip().lower()

        if action_key not in _POWER_WARNINGS:
            valid = ", ".join(_POWER_WARNINGS.keys())
            return f"Unknown power action '{action}'. Valid actions: {valid}."

        # Safety gate - return preview/warning if not confirmed
        if not confirmed:
            return _POWER_WARNINGS[action_key]

        # --- Execute confirmed actions ---
        if action_key == "shutdown":
            subprocess.run(["shutdown", "/s", "/t", "0"], check=True)
            return "Shutdown command issued. The computer will shut down momentarily."

        elif action_key == "restart":
            subprocess.run(["shutdown", "/r", "/t", "0"], check=True)
            return "Restart command issued. The computer will restart momentarily."

        elif action_key == "sleep":
            # rundll32 powrprof.dll,SetSuspendState 0,1,0
            # arg1=hibernate(0=sleep), arg2=forceCritical(1), arg3=disableWakeEvent(0)
            subprocess.run(
                ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
                check=True,
            )
            return "Sleep command issued. The computer will enter sleep mode shortly."

        elif action_key == "signout":
            subprocess.run(["logoff"], check=True)
            return "Sign-out command issued. The current session will end shortly."

        # Should never reach here given the guard above
        return f"Unhandled action: {action_key}"

    except subprocess.CalledProcessError as exc:
        return f"Power action '{action}' failed (system command error): {exc}"
    except Exception as exc:
        return f"Power action '{action}' failed: {exc}"


# ===========================================================================
# TOOL SCHEMAS  (OpenAI/OpenRouter function-calling format)
# ===========================================================================

SYSTEM_CONTROL_TOOLS_SCHEMA: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "set_volume",
            "description": (
                "Set the Windows system master volume to an exact percentage level. "
                "Use this when the user asks to set the volume to a specific value."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "level": {
                        "type": "integer",
                        "description": "Target volume level, 0 (silent) to 100 (maximum).",
                    }
                },
                "required": ["level"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "adjust_volume",
            "description": (
                "Increase or decrease the system volume by a relative amount. "
                "Use this for 'turn up the volume', 'lower volume a bit', etc."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string",
                        "enum": ["up", "down"],
                        "description": "'up' to increase volume, 'down' to decrease.",
                    },
                    "amount": {
                        "type": "integer",
                        "description": "Percentage points to change (default 10, range 1–100).",
                    },
                },
                "required": ["direction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mute_volume",
            "description": "Mute or unmute the system audio.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mute": {
                        "type": "boolean",
                        "description": "true to mute, false to unmute.",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_volume",
            "description": "Get the current system volume level and mute state.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_brightness",
            "description": (
                "Set the screen brightness to a percentage. "
                "May report 'not supported' on desktop displays without backlight control."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "level": {
                        "type": "integer",
                        "description": "Brightness level 0 (darkest) to 100 (brightest).",
                    }
                },
                "required": ["level"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_battery_status",
            "description": "Report the current battery percentage, charge state, and estimated time remaining.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_disk_usage",
            "description": "Report disk usage (used, free, total) for every mounted drive or partition.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_network_status",
            "description": (
                "Check network connectivity: active interfaces, data usage, DNS resolution, "
                "and internet reachability."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_settings",
            "description": (
                "Open a Windows Settings page. "
                "Supported pages: home, wifi, bluetooth, display, sound, apps, update, power, "
                "privacy, accounts, time, region, storage, taskbar, themes, mouse, keyboard, "
                "camera, battery."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "page": {
                        "type": "string",
                        "description": (
                            "Settings page to open. One of: home, wifi, bluetooth, display, sound, "
                            "apps, update, power, privacy, accounts, time, region, storage, taskbar, "
                            "themes, mouse, keyboard, camera, battery. Defaults to 'home'."
                        ),
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lock_computer",
            "description": "Lock the Windows workstation immediately (equivalent to pressing Win+L).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "power_action",
            "description": (
                "Perform a system power or session action: shutdown, restart, sleep, or signout. "
                "IMPORTANT: These are destructive actions. Always call first with confirmed=false "
                "to show the user a warning, then with confirmed=true only after explicit user approval."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["shutdown", "restart", "sleep", "signout"],
                        "description": "Power action to perform.",
                    },
                    "confirmed": {
                        "type": "boolean",
                        "description": (
                            "Must be true to actually execute. "
                            "If false or omitted, returns a warning preview only - nothing executes."
                        ),
                    },
                },
                "required": ["action"],
            },
        },
    },
]

# ---------------------------------------------------------------------------
# Callable dictionary - merge this into the agent's AVAILABLE_TOOLS
# ---------------------------------------------------------------------------
SYSTEM_CONTROL_AVAILABLE_TOOLS: dict[str, object] = {
    "set_volume":        set_volume,
    "adjust_volume":     adjust_volume,
    "mute_volume":       mute_volume,
    "get_volume":        get_volume,
    "set_brightness":    set_brightness,
    "adjust_brightness": adjust_brightness,
    "get_brightness":    get_brightness,
    "get_battery_status": get_battery_status,
    "get_disk_usage":    get_disk_usage,
    "get_network_status": get_network_status,
    "open_settings":     open_settings,
    "lock_computer":     lock_computer,
    "power_action":      power_action,
}
