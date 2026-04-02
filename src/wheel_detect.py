"""
Enumerate DirectInput FFB-capable steering wheels connected to the system.

Uses ctypes + Windows dinput8.dll to call DirectInput8Create() and
IDirectInput8::EnumDevices(). Returns a list of WheelDevice dataclasses.

On non-Windows systems or if DirectInput is unavailable, returns an empty list
with a logged warning.
"""

import ctypes
import ctypes.wintypes
import logging
import platform
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class WheelDevice:
    device_name: str
    instance_guid: str   # "{XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX}"
    product_guid: str


# ---------------------------------------------------------------------------
# GUID / DirectInput structs
# ---------------------------------------------------------------------------

class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    def __str__(self) -> str:
        d4 = self.Data4
        return (
            f"{{{self.Data1:08X}-{self.Data2:04X}-{self.Data3:04X}-"
            f"{d4[0]:02X}{d4[1]:02X}-"
            f"{d4[2]:02X}{d4[3]:02X}{d4[4]:02X}{d4[5]:02X}{d4[6]:02X}{d4[7]:02X}}}"
        )


MAX_PATH = 260

class DIDEVICEINSTANCEW(ctypes.Structure):
    _fields_ = [
        ("dwSize",           ctypes.c_ulong),
        ("guidInstance",     GUID),
        ("guidProduct",      GUID),
        ("dwDevType",        ctypes.c_ulong),
        ("tszInstanceName",  ctypes.c_wchar * MAX_PATH),
        ("tszProductName",   ctypes.c_wchar * MAX_PATH),
        ("guidFFDriver",     GUID),
        ("wUsagePage",       ctypes.c_ushort),
        ("wUsage",           ctypes.c_ushort),
    ]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DI8DEVCLASS_GAMECTRL   = 4
DIEDFL_ATTACHEDONLY    = 0x00000001
DIEDFL_FORCEFEEDBACK   = 0x00000100
DIENUM_CONTINUE        = 1
DIENUM_STOP            = 0

# IDirectInput8W vtable offsets (IUnknown=0,1,2; IDirectInput8=3..10)
# EnumDevices is at index 4
_IUNKNOWN_VTABLE_METHODS = 3
_ENUMDEVICES_VTABLE_OFFSET = _IUNKNOWN_VTABLE_METHODS + 1  # = 4


# ---------------------------------------------------------------------------
# Callback helpers
# ---------------------------------------------------------------------------

_EnumCallback = ctypes.WINFUNCTYPE(
    ctypes.c_int,
    ctypes.POINTER(DIDEVICEINSTANCEW),
    ctypes.c_void_p,
)


def enumerate_ffb_wheels() -> list[WheelDevice]:
    """
    Return a list of DirectInput FFB-capable game controllers (steering wheels).

    Returns an empty list if:
    - Running on a non-Windows platform
    - dinput8.dll cannot be loaded
    - No FFB devices are attached
    """
    if platform.system() != "Windows":
        logger.warning("DirectInput is only available on Windows; returning empty device list.")
        return []

    try:
        return _enumerate_via_ctypes()
    except Exception as exc:
        logger.warning("DirectInput enumeration failed: %s", exc)
        return []


def _enumerate_via_ctypes() -> list[WheelDevice]:
    """Internal: use raw ctypes to call DirectInput8Create + EnumDevices."""

    # Load DLLs
    try:
        dinput8 = ctypes.WinDLL("dinput8.dll")
        ole32   = ctypes.WinDLL("ole32.dll")
    except OSError as exc:
        raise RuntimeError(f"Could not load dinput8.dll: {exc}") from exc

    # CoInitialize (needed if not already done)
    ole32.CoInitialize(None)

    # CLSID_DirectInput8 / IID_IDirectInput8W
    CLSID_DirectInput8 = GUID()
    CLSID_DirectInput8.Data1 = 0x25E609E4
    CLSID_DirectInput8.Data2 = 0x9FE8
    CLSID_DirectInput8.Data3 = 0x11CF
    CLSID_DirectInput8.Data4 = (ctypes.c_ubyte * 8)(0xBF, 0xC7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00)

    IID_IDirectInput8W = GUID()
    IID_IDirectInput8W.Data1 = 0xBF798031
    IID_IDirectInput8W.Data2 = 0x483A
    IID_IDirectInput8W.Data3 = 0x11D2
    IID_IDirectInput8W.Data4 = (ctypes.c_ubyte * 8)(0xA0, 0x5F, 0x00, 0xA0, 0xC9, 0x0F, 0x2B, 0xB5)

    # DirectInput8Create prototype
    dinput8.DirectInput8Create.restype  = ctypes.c_long
    dinput8.DirectInput8Create.argtypes = [
        ctypes.wintypes.HINSTANCE,
        ctypes.c_ulong,
        ctypes.POINTER(GUID),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
    ]

    hinstance = ctypes.windll.kernel32.GetModuleHandleW(None)
    DIRECTINPUT_VERSION = 0x0800
    di_ptr = ctypes.c_void_p(None)

    hr = dinput8.DirectInput8Create(
        hinstance,
        DIRECTINPUT_VERSION,
        ctypes.byref(IID_IDirectInput8W),
        ctypes.byref(di_ptr),
        None,
    )
    if hr != 0:
        raise RuntimeError(f"DirectInput8Create failed: HRESULT=0x{hr & 0xFFFFFFFF:08X}")

    # IDirectInput8W vtable — EnumDevices is at slot 4
    # vtable: [QueryInterface, AddRef, Release, CreateDevice, EnumDevices, ...]
    vtable_ptr = ctypes.cast(di_ptr, ctypes.POINTER(ctypes.c_void_p))
    vtable     = ctypes.cast(vtable_ptr[0], ctypes.POINTER(ctypes.c_void_p))

    EnumDevicesProto = ctypes.WINFUNCTYPE(
        ctypes.c_long,
        ctypes.c_void_p,       # this
        ctypes.c_ulong,        # dwDevType
        _EnumCallback,         # lpCallback
        ctypes.c_void_p,       # pvRef
        ctypes.c_ulong,        # dwFlags
    )
    EnumDevices = EnumDevicesProto(vtable[4])

    devices: list[WheelDevice] = []

    @_EnumCallback
    def _callback(device_instance_ptr, _ref):
        inst = device_instance_ptr.contents
        devices.append(WheelDevice(
            device_name=inst.tszInstanceName,
            instance_guid=str(inst.guidInstance),
            product_guid=str(inst.guidProduct),
        ))
        return DIENUM_CONTINUE

    hr = EnumDevices(
        di_ptr,
        DI8DEVCLASS_GAMECTRL,
        _callback,
        None,
        DIEDFL_ATTACHEDONLY | DIEDFL_FORCEFEEDBACK,
    )

    # Release IDirectInput8 (vtable[2] = Release)
    ReleaseProto = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
    Release = ReleaseProto(vtable[2])
    Release(di_ptr)

    if hr != 0:
        logger.warning("IDirectInput8::EnumDevices returned 0x%08X", hr & 0xFFFFFFFF)

    return devices
