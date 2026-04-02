"""
Logitech Steering Wheel SDK driver (optional, lower-latency path).

Uses LogitechSteeringWheelEnginesWrapper.dll installed by Logitech G Hub.
Falls back gracefully if the DLL is not found — the caller should then
use DirectInputFFBDriver instead.

Only works on Windows.
"""

import ctypes
import logging
import platform
from pathlib import Path

from src.hardware.base import BaseHardwareDriver, HardwareStatus

logger = logging.getLogger(__name__)

# Known installation paths for the Logitech SDK DLL
_DLL_SEARCH_PATHS = [
    r"C:\Program Files\Logitech\Gaming Software\SDK\LogitechSteeringWheelEnginesWrapper.dll",
    r"C:\Program Files (x86)\Logitech\Gaming Software\SDK\LogitechSteeringWheelEnginesWrapper.dll",
    r"C:\Program Files\Logitech G HUB\SDK\LogitechSteeringWheelEnginesWrapper.dll",
    r"C:\Program Files (x86)\Logitech G HUB\SDK\LogitechSteeringWheelEnginesWrapper.dll",
]


class LogitechSDKNotFoundError(RuntimeError):
    """Raised when LogitechSteeringWheelEnginesWrapper.dll cannot be found."""


def _find_dll() -> Path | None:
    for path_str in _DLL_SEARCH_PATHS:
        p = Path(path_str)
        if p.exists():
            return p
    return None


class LogitechSDKDriver(BaseHardwareDriver):
    """
    Logitech Steering Wheel SDK driver.

    Args:
        device_index: Logitech SDK device index (0 = first connected wheel).
        max_force_pct: Safety cap — output is scaled to this percentage (0–100).
        dll_path: Override DLL path; if None, searches known locations.

    Raises:
        LogitechSDKNotFoundError: If DLL cannot be found at startup.
    """

    def __init__(
        self,
        device_index: int = 0,
        max_force_pct: float = 75.0,
        dll_path: str | None = None,
    ):
        self._device_index = device_index
        self._max_force_pct = max(0.0, min(100.0, max_force_pct))
        self._dll_path_override = dll_path
        self._dll = None
        self._connected = False
        self._enabled = False
        self._status = HardwareStatus()

        if platform.system() != "Windows":
            logger.warning("LogitechSDKDriver is Windows-only.")

    # ------------------------------------------------------------------
    # BaseHardwareDriver interface
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        if platform.system() != "Windows":
            return False
        try:
            return self._do_connect()
        except LogitechSDKNotFoundError:
            raise
        except Exception as exc:
            logger.error("Logitech SDK connect error: %s", exc)
            return False

    def disconnect(self):
        try:
            if self._dll:
                self._dll.LogiSteeringShutdown()
        except Exception as exc:
            logger.error("Logitech SDK disconnect error: %s", exc)
        self._dll = None
        self._connected = False
        self._enabled = False

    def enable(self) -> bool:
        if not self._connected:
            return False
        self._enabled = True
        self._status.enabled = True
        return True

    def disable(self):
        if self._dll and self._connected:
            try:
                self._dll.LogiStopConstantForce(self._device_index)
            except Exception as exc:
                logger.debug("LogiStopConstantForce error: %s", exc)
        self._enabled = False
        self._status.enabled = False

    def set_torque(self, value: float):
        if not self._connected or not self._enabled or not self._dll:
            return

        value = max(-1.0, min(1.0, value))
        magnitude = int(value * self._max_force_pct)
        try:
            self._dll.LogiPlayConstantForce(self._device_index, magnitude)
        except Exception as exc:
            logger.debug("LogiPlayConstantForce error: %s", exc)

    def get_status(self) -> HardwareStatus:
        if self._dll and self._connected:
            try:
                still_connected = bool(self._dll.LogiIsConnected(self._device_index))
                if not still_connected:
                    self._connected = False
                    self._enabled = False
                    self._status.connected = False
                    self._status.enabled = False
                    logger.warning("Logitech wheel disconnected.")
            except Exception:
                pass
        return self._status

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _do_connect(self) -> bool:
        if self._dll_path_override:
            dll_path = Path(self._dll_path_override)
        else:
            dll_path = _find_dll()

        if dll_path is None or not dll_path.exists():
            raise LogitechSDKNotFoundError(
                "LogitechSteeringWheelEnginesWrapper.dll not found. "
                "Install Logitech G Hub or provide the path manually."
            )

        self._dll = ctypes.WinDLL(str(dll_path))
        self._setup_prototypes()

        ok = bool(self._dll.LogiSteeringInitialize(True))
        if not ok:
            logger.error("LogiSteeringInitialize returned False.")
            return False

        connected = bool(self._dll.LogiIsConnected(self._device_index))
        if not connected:
            logger.warning("Logitech SDK: no wheel at index %d.", self._device_index)
            return False

        self._connected = True
        self._status.connected = True
        logger.info("Logitech SDK driver connected (index %d).", self._device_index)
        return True

    def _setup_prototypes(self):
        dll = self._dll

        dll.LogiSteeringInitialize.restype  = ctypes.c_bool
        dll.LogiSteeringInitialize.argtypes = [ctypes.c_bool]

        dll.LogiIsConnected.restype  = ctypes.c_bool
        dll.LogiIsConnected.argtypes = [ctypes.c_int]

        dll.LogiPlayConstantForce.restype  = ctypes.c_bool
        dll.LogiPlayConstantForce.argtypes = [ctypes.c_int, ctypes.c_int]

        dll.LogiStopConstantForce.restype  = ctypes.c_bool
        dll.LogiStopConstantForce.argtypes = [ctypes.c_int]

        dll.LogiSetOperatingRange.restype  = ctypes.c_bool
        dll.LogiSetOperatingRange.argtypes = [ctypes.c_int, ctypes.c_int]

        dll.LogiSteeringShutdown.restype  = None
        dll.LogiSteeringShutdown.argtypes = []
