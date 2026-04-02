"""
DirectInput constant force FFB driver for consumer steering wheels.

Works with Fanatec, Logitech, Thrustmaster, Moza, Simucube, and any wheel
that supports DirectInput force-feedback effects.

Implements BaseHardwareDriver so it drops in wherever AASDModbusDriver is used.
"""

import ctypes
import ctypes.wintypes
import logging
import platform
import threading

from src.hardware.base import BaseHardwareDriver, HardwareStatus

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DirectInput constants
# ---------------------------------------------------------------------------

DI_OK                   = 0x00000000
DIERR_DEVICENOTREG      = 0x80040154
DIERR_NOTACQUIRED       = 0x8007001E
DIERR_INPUTLOST         = 0x8007001E
DIERR_OTHERAPPHASPRIO   = 0x80070005   # E_ACCESSDENIED
DIERR_NOTINITIALIZED    = 0x80070015

DISCL_EXCLUSIVE         = 0x00000001
DISCL_NONEXCLUSIVE      = 0x00000002
DISCL_FOREGROUND        = 0x00000004
DISCL_BACKGROUND        = 0x00000008

DIEP_DURATION           = 0x00000001
DIEP_TYPESPECIFICPARAMS = 0x00000080
DIEP_START              = 0x20000000

DIES_SOLO               = 0x00000001

DIEFT_CONSTANTFORCE     = 0x00000003

DI_FFNOMINALMAX         = 10000

DIPROPAUTOCENTER        = 3   # property ID for auto-center
DIPH_DEVICE             = 1
DIPROP_AUTOCENTER       = ctypes.cast(
    ctypes.c_void_p(DIPROPAUTOCENTER), ctypes.c_void_p
)

INFINITE                = 0xFFFFFFFF

# Joystick2 data format GUID
# {c2a44f96-bf04-11d3-a5fa-00c04f7f2e57}
class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_components(cls, d1, d2, d3, d4_bytes):
        g = cls()
        g.Data1 = d1
        g.Data2 = d2
        g.Data3 = d3
        g.Data4 = (ctypes.c_ubyte * 8)(*d4_bytes)
        return g

    def __str__(self):
        d4 = self.Data4
        return (
            f"{{{self.Data1:08X}-{self.Data2:04X}-{self.Data3:04X}-"
            f"{d4[0]:02X}{d4[1]:02X}-"
            f"{d4[2]:02X}{d4[3]:02X}{d4[4]:02X}{d4[5]:02X}{d4[6]:02X}{d4[7]:02X}}}"
        )


class DIENVELOPE(ctypes.Structure):
    _fields_ = [
        ("dwSize",          ctypes.c_ulong),
        ("dwAttackLevel",   ctypes.c_ulong),
        ("dwAttackTime",    ctypes.c_ulong),
        ("dwFadeLevel",     ctypes.c_ulong),
        ("dwFadeTime",      ctypes.c_ulong),
    ]


class DICONSTANTFORCE(ctypes.Structure):
    _fields_ = [
        ("lMagnitude", ctypes.c_long),
    ]


class DIEFFECT(ctypes.Structure):
    _fields_ = [
        ("dwSize",                  ctypes.c_ulong),
        ("dwFlags",                 ctypes.c_ulong),
        ("dwDuration",              ctypes.c_ulong),
        ("dwSamplePeriod",          ctypes.c_ulong),
        ("dwGain",                  ctypes.c_ulong),
        ("dwTriggerButton",         ctypes.c_ulong),
        ("dwTriggerRepeatInterval", ctypes.c_ulong),
        ("cAxes",                   ctypes.c_ulong),
        ("rgdwAxes",                ctypes.POINTER(ctypes.c_ulong)),
        ("rglDirection",            ctypes.POINTER(ctypes.c_long)),
        ("lpEnvelope",              ctypes.POINTER(DIENVELOPE)),
        ("cbTypeSpecificParams",    ctypes.c_ulong),
        ("lpvTypeSpecificParams",   ctypes.c_void_p),
        ("dwStartDelay",            ctypes.c_ulong),
    ]


class DIPROPHEADER(ctypes.Structure):
    _fields_ = [
        ("dwSize",      ctypes.c_ulong),
        ("dwHeaderSize", ctypes.c_ulong),
        ("dwObj",       ctypes.c_ulong),
        ("dwHow",       ctypes.c_ulong),
    ]


class DIPROPDWORD(ctypes.Structure):
    _fields_ = [
        ("diph",    DIPROPHEADER),
        ("dwData",  ctypes.c_ulong),
    ]


# ---------------------------------------------------------------------------
# GUID helpers
# ---------------------------------------------------------------------------

def _guid_from_string(guid_str: str) -> GUID:
    """Parse '{XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX}' into a GUID struct."""
    s = guid_str.strip("{}")
    parts = s.split("-")
    if len(parts) != 5:
        raise ValueError(f"Invalid GUID string: {guid_str!r}")
    d1 = int(parts[0], 16)
    d2 = int(parts[1], 16)
    d3 = int(parts[2], 16)
    d4_str = parts[3] + parts[4]
    d4 = [int(d4_str[i:i+2], 16) for i in range(0, 16, 2)]
    return GUID.from_components(d1, d2, d3, d4)


# IID_IDirectInputEffect
IID_IDirectInputEffect = GUID.from_components(
    0xE7E1F7C0, 0x88D2, 0x11D0,
    [0x9A, 0xD0, 0x00, 0xA0, 0xC9, 0xA0, 0x65, 0x00],
)

# GUID_ConstantForce
GUID_ConstantForce = GUID.from_components(
    0x13541C20, 0x8E33, 0x11D0,
    [0x9A, 0xD0, 0x00, 0xA0, 0xC9, 0xA0, 0x65, 0x00],
)

# IID_IDirectInput8W
IID_IDirectInput8W = GUID.from_components(
    0xBF798031, 0x483A, 0x11D2,
    [0xA0, 0x5F, 0x00, 0xA0, 0xC9, 0x0F, 0x2B, 0xB5],
)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

class DirectInputFFBDriver(BaseHardwareDriver):
    """
    DirectInput constant force FFB driver for consumer steering wheels.

    Args:
        device_guid: Instance GUID string from WheelDevice.instance_guid.
        max_force_pct: Hard safety cap — output is scaled to this percentage
                       of DI_FFNOMINALMAX (10 000). Default 75%.
        hwnd: Optional HWND for SetCooperativeLevel. If None, uses desktop window.
    """

    def __init__(
        self,
        device_guid: str,
        max_force_pct: float = 75.0,
        hwnd: int | None = None,
    ):
        self._device_guid_str = device_guid
        self._max_force_pct = max(0.0, min(100.0, max_force_pct))
        self._hwnd = hwnd

        self._connected = False
        self._enabled = False
        self._lock = threading.Lock()

        # COM interface pointers (void*)
        self._di_ptr = ctypes.c_void_p(None)
        self._dev_ptr = ctypes.c_void_p(None)
        self._effect_ptr = ctypes.c_void_p(None)

        # Cached vtable callables
        self._dev_vtable = None
        self._eff_vtable = None
        self._SetParameters = None
        self._Start = None
        self._Stop = None

        self._last_magnitude = None
        self._status = HardwareStatus()

        if platform.system() != "Windows":
            logger.warning("DirectInputFFBDriver is Windows-only.")

    # ------------------------------------------------------------------
    # BaseHardwareDriver interface
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        if platform.system() != "Windows":
            return False
        try:
            return self._do_connect()
        except Exception as exc:
            logger.error("DirectInput connect error: %s", exc)
            self._connected = False
            return False

    def disconnect(self):
        try:
            self._do_disconnect()
        except Exception as exc:
            logger.error("DirectInput disconnect error: %s", exc)
        self._connected = False
        self._enabled = False

    def enable(self) -> bool:
        if not self._connected:
            return False
        try:
            return self._do_enable()
        except Exception as exc:
            logger.error("DirectInput enable error: %s", exc)
            return False

    def disable(self):
        try:
            self._do_disable()
        except Exception as exc:
            logger.error("DirectInput disable error: %s", exc)
        self._enabled = False

    def set_torque(self, value: float):
        if not self._connected or not self._enabled:
            return

        # Clamp to [-1, 1]
        value = max(-1.0, min(1.0, value))

        # Convert to DirectInput magnitude and apply safety cap
        max_mag = int(DI_FFNOMINALMAX * self._max_force_pct / 100.0)
        magnitude = int(value * max_mag)

        # Skip update if change is less than 10 units (reduce COM overhead)
        with self._lock:
            if self._last_magnitude is not None and abs(magnitude - self._last_magnitude) < 10:
                return
            self._last_magnitude = magnitude

        self._update_effect(magnitude)

    def get_status(self) -> HardwareStatus:
        self._status.connected = self._connected
        self._status.enabled = self._enabled
        return self._status

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    # ------------------------------------------------------------------
    # Internal implementation
    # ------------------------------------------------------------------

    def _load_vtable(self, ptr: ctypes.c_void_p) -> ctypes.Array:
        vtable_ptr = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_void_p))
        return ctypes.cast(vtable_ptr[0], ctypes.POINTER(ctypes.c_void_p))

    def _do_connect(self) -> bool:
        dinput8 = ctypes.WinDLL("dinput8.dll")
        ole32   = ctypes.WinDLL("ole32.dll")
        ole32.CoInitialize(None)

        dinput8.DirectInput8Create.restype  = ctypes.c_long
        dinput8.DirectInput8Create.argtypes = [
            ctypes.wintypes.HINSTANCE,
            ctypes.c_ulong,
            ctypes.POINTER(GUID),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_void_p,
        ]

        hinstance = ctypes.windll.kernel32.GetModuleHandleW(None)
        hr = dinput8.DirectInput8Create(
            hinstance,
            0x0800,
            ctypes.byref(IID_IDirectInput8W),
            ctypes.byref(self._di_ptr),
            None,
        )
        if hr != 0:
            raise RuntimeError(f"DirectInput8Create failed: 0x{hr & 0xFFFFFFFF:08X}")

        di_vtable = self._load_vtable(self._di_ptr)

        # IDirectInput8::CreateDevice — vtable slot 3
        CreateDeviceProto = ctypes.WINFUNCTYPE(
            ctypes.c_long,
            ctypes.c_void_p,            # this
            ctypes.POINTER(GUID),       # rguid (instance GUID)
            ctypes.POINTER(ctypes.c_void_p),  # lplpDirectInputDevice
            ctypes.c_void_p,            # pUnkOuter
        )
        CreateDevice = CreateDeviceProto(di_vtable[3])

        device_guid = _guid_from_string(self._device_guid_str)
        hr = CreateDevice(
            self._di_ptr,
            ctypes.byref(device_guid),
            ctypes.byref(self._dev_ptr),
            None,
        )
        if hr != 0:
            raise RuntimeError(f"CreateDevice failed: 0x{hr & 0xFFFFFFFF:08X}")

        self._dev_vtable = self._load_vtable(self._dev_ptr)

        # SetDataFormat — vtable slot 11
        # Use c_dfDIJoystick2 by passing a pre-defined data format pointer.
        # The simplest cross-version way is to call SetDataFormat with a known
        # format descriptor; here we use the exported symbol from dinput8.
        try:
            c_dfDIJoystick2_ptr = ctypes.c_void_p.in_dll(dinput8, "c_dfDIJoystick2")
        except AttributeError:
            # Not exported — fall back: skip SetDataFormat (device still works for FFB only)
            c_dfDIJoystick2_ptr = None

        SetDataFormatProto = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p
        )
        SetDataFormat = SetDataFormatProto(self._dev_vtable[11])

        if c_dfDIJoystick2_ptr is not None:
            hr = SetDataFormat(self._dev_ptr, c_dfDIJoystick2_ptr)
            if hr != 0:
                logger.warning("SetDataFormat returned 0x%08X (non-fatal)", hr & 0xFFFFFFFF)

        # SetCooperativeLevel — vtable slot 13
        hwnd = self._hwnd
        if hwnd is None:
            hwnd = ctypes.windll.user32.GetDesktopWindow()

        SetCoopLevelProto = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, ctypes.wintypes.HWND, ctypes.c_ulong
        )
        SetCooperativeLevel = SetCoopLevelProto(self._dev_vtable[13])
        flags = DISCL_BACKGROUND | DISCL_EXCLUSIVE
        hr = SetCooperativeLevel(self._dev_ptr, hwnd, flags)
        if hr != 0:
            if hr & 0xFFFFFFFF == 0x80070005:  # E_ACCESSDENIED
                self._status.error_message = (
                    "Another application has exclusive access to the wheel. "
                    "Close Logitech G Hub / Fanatec software and try again."
                )
            logger.warning("SetCooperativeLevel returned 0x%08X", hr & 0xFFFFFFFF)

        # Acquire — vtable slot 7
        AcquireProto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)
        Acquire = AcquireProto(self._dev_vtable[7])
        hr = Acquire(self._dev_ptr)
        if hr != 0 and (hr & 0xFFFFFFFF) not in (0x00000001, 0x000001E5):
            # 0x1 = S_FALSE (already acquired), 0x1E5 = DIERR_OTHERAPPHASPRIO
            raise RuntimeError(f"Acquire failed: 0x{hr & 0xFFFFFFFF:08X}")

        self._connected = True
        self._status.connected = True
        self._status.error_message = ""
        logger.info("DirectInput device connected: %s", self._device_guid_str)
        return True

    def _do_enable(self) -> bool:
        # Disable autocenter spring
        self._set_autocenter(False)

        # Create constant force effect
        cf = DICONSTANTFORCE()
        cf.lMagnitude = 0

        axes = (ctypes.c_ulong * 1)(0)       # axis 0 (X = steering)
        directions = (ctypes.c_long * 1)(0)

        eff = DIEFFECT()
        eff.dwSize                  = ctypes.sizeof(DIEFFECT)
        eff.dwFlags                 = DIEP_DURATION | DIEP_TYPESPECIFICPARAMS
        eff.dwDuration              = INFINITE
        eff.dwSamplePeriod          = 0
        eff.dwGain                  = DI_FFNOMINALMAX
        eff.dwTriggerButton         = 0xFFFFFFFF  # DIEB_NOTRIGGER
        eff.dwTriggerRepeatInterval = 0
        eff.cAxes                   = 1
        eff.rgdwAxes                = axes
        eff.rglDirection            = directions
        eff.lpEnvelope              = None
        eff.cbTypeSpecificParams    = ctypes.sizeof(DICONSTANTFORCE)
        eff.lpvTypeSpecificParams   = ctypes.cast(ctypes.byref(cf), ctypes.c_void_p)
        eff.dwStartDelay            = 0

        # IDirectInputDevice8::CreateEffect — vtable slot 17
        CreateEffectProto = ctypes.WINFUNCTYPE(
            ctypes.c_long,
            ctypes.c_void_p,                    # this
            ctypes.POINTER(GUID),               # rguid (effect type)
            ctypes.POINTER(DIEFFECT),           # lpeff
            ctypes.POINTER(ctypes.c_void_p),    # ppdeff
            ctypes.c_void_p,                    # pUnkOuter
        )
        CreateEffect = CreateEffectProto(self._dev_vtable[17])
        hr = CreateEffect(
            self._dev_ptr,
            ctypes.byref(GUID_ConstantForce),
            ctypes.byref(eff),
            ctypes.byref(self._effect_ptr),
            None,
        )
        if hr != 0:
            raise RuntimeError(f"CreateEffect failed: 0x{hr & 0xFFFFFFFF:08X}")

        self._eff_vtable = self._load_vtable(self._effect_ptr)

        # Cache vtable callables for set_torque hot-path
        self._SetParameters = ctypes.WINFUNCTYPE(
            ctypes.c_long,
            ctypes.c_void_p,          # this
            ctypes.POINTER(DIEFFECT), # peff
            ctypes.c_ulong,           # dwFlags
        )(self._eff_vtable[3])

        self._Start = ctypes.WINFUNCTYPE(
            ctypes.c_long,
            ctypes.c_void_p,   # this
            ctypes.c_ulong,    # dwIterations
            ctypes.c_ulong,    # dwFlags
        )(self._eff_vtable[5])

        self._Stop = ctypes.WINFUNCTYPE(
            ctypes.c_long,
            ctypes.c_void_p,
        )(self._eff_vtable[6])

        # Start the effect (DIES_SOLO stops other effects first)
        hr = self._Start(self._effect_ptr, 1, DIES_SOLO)
        if hr != 0:
            logger.warning("IDirectInputEffect::Start returned 0x%08X", hr & 0xFFFFFFFF)

        self._enabled = True
        self._status.enabled = True
        self._last_magnitude = None
        logger.info("DirectInput FFB effect started.")
        return True

    def _do_disable(self):
        if self._effect_ptr and self._effect_ptr.value:
            # Zero force then stop
            self._update_effect(0)
            if self._Stop:
                self._Stop(self._effect_ptr)
            # Release IDirectInputEffect (vtable[2] = Release)
            ReleaseProto = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
            Release = ReleaseProto(self._eff_vtable[2])
            Release(self._effect_ptr)
            self._effect_ptr = ctypes.c_void_p(None)
        self._enabled = False
        self._status.enabled = False

    def _do_disconnect(self):
        self._do_disable()
        if self._dev_ptr and self._dev_ptr.value:
            # Unacquire — vtable slot 8
            UnacquireProto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)
            Unacquire = UnacquireProto(self._dev_vtable[8])
            Unacquire(self._dev_ptr)
            # Release device
            ReleaseProto = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
            Release = ReleaseProto(self._dev_vtable[2])
            Release(self._dev_ptr)
            self._dev_ptr = ctypes.c_void_p(None)
        if self._di_ptr and self._di_ptr.value:
            vtable = self._load_vtable(self._di_ptr)
            ReleaseProto = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
            Release = ReleaseProto(vtable[2])
            Release(self._di_ptr)
            self._di_ptr = ctypes.c_void_p(None)
        self._connected = False
        self._status.connected = False

    def _update_effect(self, magnitude: int):
        if not self._effect_ptr or not self._effect_ptr.value:
            return
        if not self._SetParameters:
            return

        cf = DICONSTANTFORCE()
        cf.lMagnitude = magnitude

        eff = DIEFFECT()
        eff.dwSize               = ctypes.sizeof(DIEFFECT)
        eff.dwFlags              = DIEP_TYPESPECIFICPARAMS | DIEP_START
        eff.cbTypeSpecificParams = ctypes.sizeof(DICONSTANTFORCE)
        eff.lpvTypeSpecificParams = ctypes.cast(ctypes.byref(cf), ctypes.c_void_p)

        with self._lock:
            hr = self._SetParameters(
                self._effect_ptr,
                ctypes.byref(eff),
                DIEP_TYPESPECIFICPARAMS | DIEP_START,
            )

        if hr != 0:
            hr_u = hr & 0xFFFFFFFF
            if hr_u in (0x8007001E, 0x80070015):  # NOTACQUIRED / not initialised
                logger.debug("Effect lost, attempting re-acquire.")
                self._reacquire()
            else:
                logger.debug("SetParameters returned 0x%08X", hr_u)

    def _reacquire(self):
        if not self._dev_ptr or not self._dev_ptr.value:
            return
        AcquireProto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)
        Acquire = AcquireProto(self._dev_vtable[7])
        Acquire(self._dev_ptr)

    def _set_autocenter(self, enabled: bool):
        if not self._dev_ptr or not self._dev_ptr.value:
            return
        prop = DIPROPDWORD()
        prop.diph.dwSize       = ctypes.sizeof(DIPROPDWORD)
        prop.diph.dwHeaderSize = ctypes.sizeof(DIPROPHEADER)
        prop.diph.dwObj        = 0
        prop.diph.dwHow        = DIPH_DEVICE
        prop.dwData            = 1 if enabled else 0

        SetPropertyProto = ctypes.WINFUNCTYPE(
            ctypes.c_long,
            ctypes.c_void_p,   # this
            ctypes.c_void_p,   # rguidProp (DIPROPAUTOCENTER = 3 as ptr)
            ctypes.POINTER(DIPROPHEADER),
        )
        SetProperty = SetPropertyProto(self._dev_vtable[14])
        hr = SetProperty(
            self._dev_ptr,
            ctypes.c_void_p(DIPROPAUTOCENTER),
            ctypes.byref(prop.diph),
        )
        if hr != 0:
            logger.debug("SetProperty(AUTOCENTER) returned 0x%08X", hr & 0xFFFFFFFF)
