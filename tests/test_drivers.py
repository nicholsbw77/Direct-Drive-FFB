"""
Unit tests for consumer wheel FFB drivers.

These tests run without real hardware by using mock/stub implementations
that record calls without touching Windows APIs.
"""

import platform
import unittest
from unittest.mock import MagicMock, patch

from src.hardware.base import HardwareStatus


# ---------------------------------------------------------------------------
# Mock driver — records set_torque calls without touching hardware
# ---------------------------------------------------------------------------

class MockDirectInputDriver:
    """Test harness that records set_torque calls without touching hardware."""

    def __init__(self, device_guid: str = "{FAKE-GUID}", max_force_pct: float = 75.0):
        self.device_guid = device_guid
        self.max_force_pct = max_force_pct
        self._connected = False
        self._enabled = False
        self.torque_calls: list[float] = []
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.enable_calls = 0
        self.disable_calls = 0

    def connect(self) -> bool:
        self.connect_calls += 1
        self._connected = True
        return True

    def disconnect(self):
        self.disconnect_calls += 1
        self._connected = False
        self._enabled = False

    def enable(self) -> bool:
        self.enable_calls += 1
        self._enabled = True
        return True

    def disable(self):
        self.disable_calls += 1
        self._enabled = False

    def set_torque(self, value: float):
        if not self._connected or not self._enabled:
            return
        value = max(-1.0, min(1.0, value))
        self.torque_calls.append(value)

    def get_status(self) -> HardwareStatus:
        return HardwareStatus(connected=self._connected, enabled=self._enabled)

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def is_enabled(self) -> bool:
        return self._enabled


# ---------------------------------------------------------------------------
# Tests: MockDirectInputDriver
# ---------------------------------------------------------------------------

class TestMockDirectInputDriver(unittest.TestCase):

    def setUp(self):
        self.driver = MockDirectInputDriver()
        self.driver.connect()
        self.driver.enable()

    def test_set_torque_records_calls(self):
        self.driver.set_torque(0.5)
        self.driver.set_torque(-0.3)
        self.assertEqual(self.driver.torque_calls, [0.5, -0.3])

    def test_clamps_positive_above_one(self):
        self.driver.set_torque(2.5)
        self.assertEqual(self.driver.torque_calls[-1], 1.0)

    def test_clamps_negative_below_minus_one(self):
        self.driver.set_torque(-99.0)
        self.assertEqual(self.driver.torque_calls[-1], -1.0)

    def test_disconnected_does_not_record(self):
        self.driver.disconnect()
        self.driver.set_torque(0.5)
        self.assertEqual(len(self.driver.torque_calls), 0)

    def test_disabled_does_not_record(self):
        self.driver.disable()
        self.driver.set_torque(0.5)
        self.assertEqual(len(self.driver.torque_calls), 0)

    def test_zero_is_valid(self):
        self.driver.set_torque(0.0)
        self.assertIn(0.0, self.driver.torque_calls)

    def test_boundary_values(self):
        self.driver.set_torque(1.0)
        self.driver.set_torque(-1.0)
        self.assertEqual(self.driver.torque_calls, [1.0, -1.0])


# ---------------------------------------------------------------------------
# Tests: DirectInputFFBDriver (without real Windows APIs)
# ---------------------------------------------------------------------------

class TestDirectInputFFBDriverStub(unittest.TestCase):
    """
    Tests for DirectInputFFBDriver logic that does NOT require Windows.
    Patches platform.system() to simulate non-Windows so connect() returns False
    gracefully, and verifies all guard logic.
    """

    def test_connect_returns_false_on_non_windows(self):
        from src.hardware.directinput_driver import DirectInputFFBDriver
        with patch("src.hardware.directinput_driver.platform.system", return_value="Linux"):
            driver = DirectInputFFBDriver(device_guid="{FAKE-0000}")
            result = driver.connect()
        self.assertFalse(result)
        self.assertFalse(driver.is_connected)

    def test_set_torque_silent_when_not_connected(self):
        from src.hardware.directinput_driver import DirectInputFFBDriver
        with patch("src.hardware.directinput_driver.platform.system", return_value="Linux"):
            driver = DirectInputFFBDriver(device_guid="{FAKE-0000}")
        # Should not raise even when not connected
        driver.set_torque(0.5)

    def test_set_torque_clamps_value(self):
        """Even with internal magnitude arithmetic, value must be clamped."""
        from src.hardware.directinput_driver import DirectInputFFBDriver
        driver = DirectInputFFBDriver.__new__(DirectInputFFBDriver)
        driver._connected = True
        driver._enabled = True
        driver._max_force_pct = 75.0
        driver._last_magnitude = None
        driver._lock = __import__("threading").Lock()
        driver._effect_ptr = None  # will short-circuit _update_effect

        # Monkey-patch _update_effect to capture magnitude
        captured = []
        driver._update_effect = lambda mag: captured.append(mag)

        driver.set_torque(2.0)   # should clamp to 1.0 → magnitude = 7500
        driver.set_torque(-2.0)  # should clamp to -1.0 → magnitude = -7500

        self.assertEqual(captured[0], 7500)
        self.assertEqual(captured[1], -7500)

    def test_max_force_pct_applied(self):
        from src.hardware.directinput_driver import DirectInputFFBDriver
        driver = DirectInputFFBDriver.__new__(DirectInputFFBDriver)
        driver._connected = True
        driver._enabled = True
        driver._max_force_pct = 50.0
        driver._last_magnitude = None
        driver._lock = __import__("threading").Lock()
        driver._effect_ptr = None

        captured = []
        driver._update_effect = lambda mag: captured.append(mag)

        driver.set_torque(1.0)
        # 1.0 * 50% of 10000 = 5000
        self.assertEqual(captured[0], 5000)

    def test_get_status_reflects_state(self):
        from src.hardware.directinput_driver import DirectInputFFBDriver
        with patch("src.hardware.directinput_driver.platform.system", return_value="Linux"):
            driver = DirectInputFFBDriver(device_guid="{FAKE-0000}")
        status = driver.get_status()
        self.assertFalse(status.connected)
        self.assertFalse(status.enabled)


# ---------------------------------------------------------------------------
# Tests: wheel_detect.enumerate_ffb_wheels
# ---------------------------------------------------------------------------

class TestEnumerateFFBWheels(unittest.TestCase):

    def test_returns_empty_list_on_non_windows(self):
        with patch("src.wheel_detect.platform.system", return_value="Linux"):
            from src import wheel_detect
            result = wheel_detect.enumerate_ffb_wheels()
        self.assertIsInstance(result, list)
        self.assertEqual(result, [])

    @unittest.skipUnless(platform.system() == "Windows", "Windows-only: ctypes.WinDLL unavailable on Linux")
    def test_returns_empty_list_if_dinput_unavailable(self):
        """enumerate_ffb_wheels() should return [] not raise if DLL missing."""
        with patch("src.wheel_detect.platform.system", return_value="Windows"):
            with patch("ctypes.WinDLL", side_effect=OSError("DLL not found")):
                from src import wheel_detect
                result = wheel_detect.enumerate_ffb_wheels()
        self.assertIsInstance(result, list)
        self.assertEqual(result, [])

    def test_wheel_device_dataclass_fields(self):
        from src.wheel_detect import WheelDevice
        w = WheelDevice(
            device_name="Logitech G923",
            instance_guid="{AAAABBBB-0000-0000-0000-000000000000}",
            product_guid="{CCCCDDDD-0000-0000-0000-000000000000}",
        )
        self.assertEqual(w.device_name, "Logitech G923")
        self.assertIn("{", w.instance_guid)


# ---------------------------------------------------------------------------
# Tests: LogitechSDKDriver
# ---------------------------------------------------------------------------

class TestLogitechSDKDriver(unittest.TestCase):

    def test_connect_raises_on_missing_dll(self):
        from src.hardware.logitech_driver import LogitechSDKDriver, LogitechSDKNotFoundError
        with patch("src.hardware.logitech_driver.platform.system", return_value="Windows"):
            with patch("src.hardware.logitech_driver._find_dll", return_value=None):
                driver = LogitechSDKDriver()
                with self.assertRaises(LogitechSDKNotFoundError):
                    driver.connect()

    def test_connect_returns_false_on_non_windows(self):
        from src.hardware.logitech_driver import LogitechSDKDriver
        with patch("src.hardware.logitech_driver.platform.system", return_value="Linux"):
            driver = LogitechSDKDriver()
            result = driver.connect()
        self.assertFalse(result)

    def test_set_torque_silent_when_not_connected(self):
        from src.hardware.logitech_driver import LogitechSDKDriver
        with patch("src.hardware.logitech_driver.platform.system", return_value="Linux"):
            driver = LogitechSDKDriver()
        driver.set_torque(0.8)  # should not raise

    def test_max_force_pct_clamps_constructor(self):
        from src.hardware.logitech_driver import LogitechSDKDriver
        driver = LogitechSDKDriver(max_force_pct=150.0)
        self.assertEqual(driver._max_force_pct, 100.0)
        driver2 = LogitechSDKDriver(max_force_pct=-10.0)
        self.assertEqual(driver2._max_force_pct, 0.0)


if __name__ == "__main__":
    unittest.main()
