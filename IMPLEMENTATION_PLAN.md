# Direct Drive FFB — Implementation Plan for Consumer Wheel Plugin

## Project Goal

Extend the existing AASD direct-drive FFB system into a **standalone Windows application**
that non-technical users (Fanatec, Logitech, Thrustmaster, Moza wheel owners) can install
and run with no Python knowledge required.

The dirt sprint car FFB profile — and all other profiles — should work on ANY force feedback
wheel, not just the custom AASD servo build.

---

## What Already Exists (Do Not Re-Implement)

```
src/
├── telemetry/iracing_reader.py   ✅ iRacing shared memory reader, 360Hz, TelemetryData dataclass
├── ffb/effects.py                ✅ All FFB effects including 4 dirt-specific ones
├── ffb/engine.py                 ✅ Mixer, output filter, slew limiting, safety clamping
├── hardware/base.py              ✅ Abstract BaseHardwareDriver interface
├── hardware/modbus_driver.py     ✅ AASD Modbus RTU driver (keep, don't touch)
├── config.py                     ✅ JSON profile load/save system
└── gui/main_window.py            ✅ Existing GUI (advanced users / AASD servo)

config/
├── default_profile.json          ✅ Pavement profile
└── dirt_sprint_car.json          ✅ Dirt sprint car profile
```

The FFB engine calls `hardware_callback(value: float)` where value is [-1.0, 1.0].
The new consumer wheel driver just needs to implement that callback — nothing else changes.

---

## What Needs to Be Built

### 1. Consumer Wheel Hardware Drivers (`src/hardware/`)

#### Primary: DirectInput FFB Driver (`directinput_driver.py`)
- Works with **all** DirectInput FFB wheels: Fanatec, Logitech, Thrustmaster, Moza, etc.
- Uses Windows DirectInput8 COM API via `ctypes`
- Sends a `DIEFT_CONSTANTFORCE` effect updated each FFB tick
- Auto-discovers attached FFB-capable devices
- See `TODO_directinput_driver.md` for full spec

#### Optional: Logitech SDK Driver (`logitech_driver.py`)
- Uses Logitech Steering Wheel SDK (LogiSteeringWheelSDK.dll)
- Better low-latency path for G923, G29, G27, G920 owners
- See `TODO_directinput_driver.md` for details

### 2. Turnkey GUI (`src/gui/plugin_window.py`)
- Completely separate from `main_window.py` (which is for AASD advanced users)
- Designed for zero technical knowledge
- Auto-detects iRacing and connected wheels
- Big, simple controls — not a sea of parameters
- See `TODO_turnkey_gui.md` for full spec

### 3. Windows Executable Packaging
- PyInstaller `--onefile --windowed` build
- Installer script (Inno Setup or NSIS)
- See `TODO_packaging.md` for full spec

---

## Architecture Diagram

```
[iRacing Shared Memory]
         |
         v  (pyirsdk, 360Hz background thread)
[IRacingTelemetry]  <-- no changes needed
         |
         v
[FFBEngine]  <-- no changes needed
  ├── SelfAligningTorque
  ├── RearTractionLoss        <-- dirt primary
  ├── DirtYawFeedback
  ├── ThrottleSteer
  ├── DirtSurfaceRumble
  └── ... (all existing effects)
         |
         v  (hardware_callback, [-1.0, 1.0], ~360Hz)
[Hardware Driver]  <-- NEW: swap in consumer wheel driver
  ├── DirectInputFFBDriver    <-- NEW (any wheel, Windows)
  ├── LogitechSDKDriver       <-- NEW (optional, G-series)
  └── AASDModbusDriver        <-- existing (custom servo)
         |
         v
[Physical Wheel Motor]
```

---

## Key Technical Decisions

### DirectInput vs SDL2
- **Use DirectInput8 (ctypes)** — SDL2 haptic API has gaps with wheel FFB types,
  and many Fanatec/Logitech drivers expose richer DirectInput interfaces
- DirectInput constant force (`DIEFT_CONSTANTFORCE`) is universally supported
- One `IDirectInputEffect` object is created at startup and updated each tick via
  `SetParameters()` with the new magnitude — no re-create overhead

### Why Not Use the Sim's Own FFB
- iRacing's built-in DirectInput FFB is limited to ~60Hz and uses coarse effect types
- Our telemetry approach reads raw physics at 360Hz
- The user will need to **set in-game FFB to 0** and let our app drive the wheel
- The installer/GUI should make this clear with a setup guide

### Update Rate
- 360Hz telemetry read
- FFBEngine processes at 360Hz
- DirectInput effect update: target 100–200Hz (DirectInput has own internal smoothing)
- Logitech SDK: can sustain ~200Hz constant force updates

### Wheel Auto-Detection
- Enumerate DirectInput devices, filter by `DIDC_FORCEFEEDBACK` capability
- Show detected devices in GUI dropdown
- Persist last-used device in settings

---

## File Layout for New Work

```
src/
├── hardware/
│   ├── directinput_driver.py    <-- TO BUILD
│   └── logitech_driver.py       <-- TO BUILD (optional)
├── gui/
│   └── plugin_window.py         <-- TO BUILD
├── wheel_detect.py              <-- TO BUILD (enumerate FFB wheels)
└── plugin_main.py               <-- TO BUILD (entry point for consumer exe)

build/
├── build_exe.spec               <-- TO BUILD (PyInstaller spec)
├── build_exe.bat                <-- TO BUILD (one-click build script)
└── installer/
    └── setup.iss                <-- TO BUILD (Inno Setup installer script)

assets/
├── icon.ico                     <-- TO CREATE (app icon)
├── splash.png                   <-- TO CREATE (loading splash)
└── setup_guide.png              <-- TO CREATE (in-game FFB setup screenshot)
```

---

## Dependencies to Add to requirements.txt

```
# Existing
pyirsdk>=1.3.0
pymodbus>=3.5.0
PyQt5>=5.15.0
numpy>=1.24.0

# New for consumer wheel support
pywin32>=306          # Windows COM / ctypes helpers
comtypes>=1.4.1       # DirectInput COM interface binding (optional, may use raw ctypes)

# New for packaging
pyinstaller>=6.3.0
```

---

## Implementation Order

1. **`wheel_detect.py`** — enumerate DirectInput FFB devices (foundational, needed by GUI)
2. **`directinput_driver.py`** — the actual FFB output driver
3. **`plugin_window.py`** — turnkey GUI
4. **`plugin_main.py`** — entry point wiring everything together
5. **`logitech_driver.py`** — optional, after above works
6. **Packaging** — PyInstaller spec + build scripts

---

## Testing Checklist

- [ ] Logitech G29/G923 detects and receives FFB
- [ ] Fanatec CSL DD / ClubSport detects and receives FFB
- [ ] Thrustmaster T300 detects and receives FFB
- [ ] iRacing not running: app starts, shows "waiting for iRacing"
- [ ] iRacing running but not in session: no FFB output, no crash
- [ ] iRacing in session (pavement): default profile loads, FFB active
- [ ] Switching to dirt_sprint_car profile mid-session works
- [ ] App closes cleanly (FFB stops, no zombie process)
- [ ] Exe runs on clean Windows 10/11 with no Python installed
- [ ] Installer adds Start Menu shortcut and uninstaller
