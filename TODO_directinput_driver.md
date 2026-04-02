# TODO: Consumer Wheel FFB Drivers

## Context

The FFBEngine (in `src/ffb/engine.py`) calls a `hardware_callback(value: float)` where
`value` is in the range `[-1.0, 1.0]`. The callback is called at ~360Hz from a background
thread. A consumer wheel driver just needs to translate that float into a DirectInput
constant force effect magnitude.

The abstract base class is `src/hardware/base.py` — all drivers must implement it.

---

## Task 1: `src/wheel_detect.py` — FFB Wheel Enumerator

Create a standalone module that enumerates all DirectInput FFB-capable devices connected
to the system. This is used by both the driver and the GUI device picker.

### Requirements
- Use `ctypes` + Windows `dinput8.dll` to call `DirectInput8Create()`
- Call `IDirectInput8::EnumDevices()` with `DI8DEVCLASS_GAMECTRL` and
  `DIEDFL_ATTACHEDONLY | DIEDFL_FORCEFEEDBACK`
- For each device found, collect:
  - `device_name: str` — human-readable (e.g. "Logitech G923 Racing Wheel")
  - `instance_guid: str` — unique device GUID as string
  - `product_guid: str`
  - `num_axes: int`
  - `num_buttons: int`
- Return a list of `WheelDevice` dataclasses
- Must handle: no devices found (return empty list), DirectInput not available (return
  empty list with logged warning), multiple devices found

### Signature
```python
@dataclass
class WheelDevice:
    device_name: str
    instance_guid: str   # "{XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX}"
    product_guid: str

def enumerate_ffb_wheels() -> list[WheelDevice]:
    ...
```

### Reference
- DirectInput enumeration: https://docs.microsoft.com/en-us/previous-versions/windows/desktop/ee416568(v=vs.85)
- The `comtypes` package can generate Python bindings for DirectInput COM interfaces
  OR use raw `ctypes` with struct definitions for DIDEVICEINSTANCE

---

## Task 2: `src/hardware/directinput_driver.py` — DirectInput FFB Driver

This is the primary driver for **all** consumer FFB wheels. Fanatec, Logitech, 
Thrustmaster, Moza, Simucube, etc. all support DirectInput constant force effects.

### How DirectInput Constant Force Works
1. Create `IDirectInput8` interface
2. Create `IDirectInputDevice8` for the chosen wheel
3. Set cooperative level: `DISCL_BACKGROUND | DISCL_EXCLUSIVE`
4. Set data format: `c_dfDIJoystick2`
5. Enumerate axes, set range `[-10000, 10000]`, enable autocenter OFF
6. Create an `IDirectInputEffect` object with `DIEFT_CONSTANTFORCE`
7. Each FFB tick: call `IDirectInputEffect::SetParameters()` with new magnitude
8. `magnitude = int(value * 10000)` where value is [-1.0, 1.0]

### Class Structure
```python
class DirectInputFFBDriver(BaseHardwareDriver):
    def __init__(self, device_guid: str, max_force_pct: float = 75.0):
        # device_guid: from WheelDevice.instance_guid
        # max_force_pct: safety cap on maximum output (0-100)
        ...

    def connect(self) -> bool:
        # Create IDirectInput8, acquire device, set cooperative level
        # Enumerate force-feedback axes
        # Return False if device not found or no FFB support
        ...

    def disconnect(self):
        # Release IDirectInputEffect
        # Unacquire device
        # Release IDirectInputDevice8
        ...

    def enable(self) -> bool:
        # Create DIEFT_CONSTANTFORCE effect object
        # Set autocenter OFF (DIPROPAUTOCENTER)
        # Start effect (DIES_SOLO)
        ...

    def disable(self):
        # Stop effect
        # Set force to 0 before stopping
        ...

    def set_torque(self, value: float):
        # Convert [-1.0, 1.0] to [-10000, 10000]
        # Call IDirectInputEffect::SetParameters() with DIEP_TYPESPECIFICPARAMS
        # Only update if value changed by more than 10 units (reduce COM overhead)
        ...

    def get_status(self) -> HardwareStatus:
        # Return connected/enabled state
        # DirectInput doesn't expose motor RPM/temp, so those stay 0
        ...
```

### ctypes Structures Needed
Define these with `ctypes.Structure`:
- `GUID` — 16-byte GUID
- `DIDEVICEINSTANCE` — device enumeration result
- `DIEFFECT` — effect parameters
- `DICONSTANTFORCE` — constant force type-specific params
- `DIENVELOPE` — optional attack/fade envelope
- `DIPERIODIC` — not needed for constant force but define for completeness

### Important Notes
- `IDirectInputDevice8::SetCooperativeLevel()` requires a window handle (HWND)
  Pass the PyQt5 main window HWND: `int(window.winId())`
- The device must be **acquired** before setting effects
- DirectInput FFB effects use magnitude range `[-10000, 10000]` (DI_FFNOMINALMAX = 10000)
- Set `DIPROPAUTOCENTER` to 0 — disables the wheel's built-in centering spring
  so our software effects are the only force
- `DISCL_BACKGROUND` is needed so FFB continues when the iRacing window is focused
- Thread safety: `set_torque()` will be called from the FFB engine thread, not the GUI
  thread. Use a threading.Lock() around the COM call.

### COM Interface Definitions (ctypes)
The DirectInput COM interfaces can be defined using `comtypes`:
```python
# Option A: comtypes (cleaner)
import comtypes
import comtypes.client
# Generate bindings: comtypes.client.GetModule("dinput8.dll")

# Option B: raw ctypes (more portable, no comtypes dep)
# Define vtable offsets manually for IDirectInputEffect methods
```
Prefer `comtypes` for maintainability. If `comtypes` causes PyInstaller issues,
fall back to raw ctypes vtable approach.

### Error Handling
- `DIERR_DEVICENOTREG` — device disconnected, set connected=False
- `DIERR_NOTACQUIRED` — re-acquire and retry once
- `DIERR_INPUTLOST` — same as NOTACQUIRED
- `E_ACCESSDENIED` — another app has exclusive access, show user message
- Log all DirectInput HRESULT errors with hex code for debugging

---

## Task 3: `src/hardware/logitech_driver.py` — Logitech SDK Driver (Optional)

Logitech G923, G29, G920, G27 owners can use the official Logitech Steering Wheel SDK
for a potentially lower-latency path than DirectInput.

### SDK Location
- DLL: `LogitechSteeringWheelEnginesWrapper.dll`
- Logitech G Hub installs it to: `C:\Program Files\Logitech\Gaming Software\SDK\`
  or `C:\Program Files\Logitech G HUB\SDK\`
- If DLL not found, fall back gracefully to DirectInputFFBDriver

### Key SDK Functions (via ctypes)
```c
// Initialize
bool LogiSteeringInitialize(bool ignoreXInputControllers);

// Check device connected
bool LogiIsConnected(int index);  // index 0 = first wheel

// Set constant force (-100 to 100, integer)
bool LogiPlayConstantForce(int index, int magnitudePercentage);

// Stop all forces
bool LogiStopConstantForce(int index);

// Set operating range (degrees)
bool LogiSetOperatingRange(int index, int range);

// Shutdown
void LogiSteeringShutdown();
```

### Class Structure
```python
class LogitechSDKDriver(BaseHardwareDriver):
    def __init__(self, device_index: int = 0, max_force_pct: float = 75.0):
        ...

    def connect(self) -> bool:
        # Try to load DLL from known paths
        # Call LogiSteeringInitialize(True)
        # Check LogiIsConnected(self.device_index)
        ...

    def set_torque(self, value: float):
        # Convert [-1.0, 1.0] to [-100, 100]
        magnitude = int(value * self.max_force_pct)
        self._dll.LogiPlayConstantForce(self.device_index, magnitude)
        ...
```

### Notes
- SDK only works on Windows
- If DLL not found at any known path, raise `LogitechSDKNotFoundError`
- The GUI should detect this and offer DirectInput fallback automatically

---

## Task 4: Unit Tests for Drivers

Create `tests/test_drivers.py`:

```python
class MockDirectInputDriver:
    """Test harness that records set_torque calls without touching hardware."""
    ...

def test_directinput_clamps_value():
    """set_torque should reject values outside [-1, 1]."""
    ...

def test_directinput_disconnected_does_not_crash():
    """set_torque when not connected should silently do nothing."""
    ...

def test_wheel_detect_no_crash_if_no_wheels():
    """enumerate_ffb_wheels() returns [] if no wheels connected."""
    ...
```

---

## Dependencies

Add to `requirements.txt`:
```
comtypes>=1.4.1    # DirectInput COM bindings (preferred)
pywin32>=306       # Windows API helpers (backup, also needed by PyInstaller)
```

---

## Files to Create

| File | Description |
|------|-------------|
| `src/wheel_detect.py` | Enumerate connected FFB wheels via DirectInput |
| `src/hardware/directinput_driver.py` | DirectInput constant force driver |
| `src/hardware/logitech_driver.py` | Logitech SDK driver (optional) |
| `tests/test_drivers.py` | Basic unit tests |
