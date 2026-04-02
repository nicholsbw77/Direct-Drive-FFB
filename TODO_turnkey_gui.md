# TODO: Turnkey GUI for Consumer Wheel Users

## Context

This is a **separate** GUI from `src/gui/main_window.py` (which is for advanced AASD
servo users). This GUI is for non-technical users who have Logitech/Fanatec/Thrustmaster
wheels and just want to install and run.

Design principles:
- **Zero configuration required** — auto-detect wheel, auto-connect iRacing
- **Three screens only**: Setup Wizard, Main (Running), Settings
- **Big, obvious controls** — not a wall of sliders
- **Clear status at all times** — user always knows if it's working

Entry point: `src/gui/plugin_window.py`
Main script: `plugin_main.py` (see `TODO_packaging.md` for how it's launched)

---

## Screen 1: Setup Wizard

Shown **only on first launch** (or if no wheel was previously detected).
Stored completion flag in `%APPDATA%\DirtFFB\settings.json`.

### Step 1 of 3 — Welcome
```
┌─────────────────────────────────────────────┐
│  🏁 Dirt FFB Plugin                         │
│                                             │
│  Add telemetry-based force feedback         │
│  for dirt cars in iRacing.                  │
│                                             │
│  Works with Fanatec, Logitech,              │
│  Thrustmaster, and more.                    │
│                                             │
│              [ Get Started → ]              │
└─────────────────────────────────────────────┘
```

### Step 2 of 3 — Select Your Wheel
```
┌─────────────────────────────────────────────┐
│  Select Your Steering Wheel                 │
│                                             │
│  Detected devices:                          │
│  ┌──────────────────────────────────────┐   │
│  │ ● Logitech G923 Racing Wheel         │   │
│  │   Fanatec CSL DD (not detected)      │   │
│  └──────────────────────────────────────┘   │
│                                             │
│  Don't see your wheel? Make sure it's       │
│  plugged in and drivers are installed.      │
│  [ Refresh ]                                │
│                                             │
│  [ ← Back ]           [ Next → ]           │
└─────────────────────────────────────────────┘
```
- Calls `enumerate_ffb_wheels()` to populate the list
- Refresh button re-runs enumeration
- Selected device GUID saved to settings

### Step 3 of 3 — iRacing Setup
```
┌─────────────────────────────────────────────┐
│  One-Time iRacing Setting                   │
│                                             │
│  You must set iRacing's Force Feedback      │
│  Strength to 0% so this app can control     │
│  your wheel directly.                       │
│                                             │
│  In iRacing:                                │
│  Options → Controls → Force Feedback        │
│  Set "Force Feedback Strength" to 0         │
│                                             │
│  [Show me a screenshot]                     │
│                                             │
│  [ ← Back ]     [ Done — Launch App ]       │
└─────────────────────────────────────────────┘
```
- "Show me a screenshot" opens `assets/setup_guide.png` in a QLabel dialog
- Stores wizard_complete=True in settings on "Done"

---

## Screen 2: Main Window (Running State)

This is what users see 99% of the time. Keep it simple.

```
┌─────────────────────────────────────────────────────────┐
│  Dirt FFB Plugin                    [_][□][X]           │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  iRacing  ●  Connected — Dirt Track @ Williams Grove    │
│  Wheel    ●  Logitech G923 — Active                     │
│                                                         │
│ ┌─────────────────────────────────────────────────────┐ │
│ │              TORQUE OUTPUT                          │ │
│ │  ◄──────────────●──────────────►                   │ │
│ │             +0.312                                  │ │
│ │  [waveform scrolling display]                       │ │
│ └─────────────────────────────────────────────────────┘ │
│                                                         │
│  Profile:  [Dirt Sprint Car  ▼]   [ Load ]  [ Save ]   │
│                                                         │
│  Overall Strength  ──────────●────────  85%            │
│  Max Force Cap     ──────●──────────── 60%             │
│                                                         │
│  [ Advanced Effects ▼ ]  (collapsed by default)        │
│                                                         │
│  ┌── Advanced Effects ──────────────────────────────┐  │
│  │  ■ Rear Traction Loss  ────────●──────── 1.20    │  │
│  │  ■ Yaw Feedback        ───────●───────── 0.80    │  │
│  │  ■ Throttle-Steer      ──────●────────── 0.60    │  │
│  │  ■ Surface Rumble      ─────●─────────── 0.45    │  │
│  │  ■ Engine Vibration    ──●────────────── 0.20    │  │
│  │  □ Self-Aligning Torque ──────────────── (off)   │  │
│  └──────────────────────────────────────────────────┘  │
│                                                         │
│                        [ ⚙ Settings ]                   │
└─────────────────────────────────────────────────────────┘
```

### Status Indicators
Use colored dots:
- 🔴 Red dot = disconnected / error
- 🟡 Yellow dot = connected but not in session
- 🟢 Green dot = active, FFB running

### Profile Dropdown
- Populated from `config/` directory JSON files
- Switching profiles applies immediately (no restart needed)
- Calls the same `load_profile()` + `apply_*` functions as main_window.py

### Overall Strength Slider
- Maps directly to `engine.config.master_gain` (0.0 to 2.0 displayed as 0–200%)
- Label shows integer percentage
- Updates in real time

### Max Force Cap Slider
- Maps to `engine.config.max_torque_pct` (0–100%)
- Hard safety ceiling — no FFB output beyond this
- Default 75% to protect wheels

### Advanced Effects Section
- Collapsed by default — click "Advanced Effects ▼" to expand
- Shows only the effects active in the current profile
- Each row: checkbox (enabled), label, slider (gain 0–2.0), value label
- Uses `EffectSliderGroup` from existing code where possible

### Tray Icon
- App should minimize to system tray (not close)
- Tray icon shows green/red status dot
- Right-click tray menu: "Open", "Disable FFB", "Exit"
- Implement with `QSystemTrayIcon`

---

## Screen 3: Settings Window

Opened via the ⚙ Settings button. Separate `QDialog`.

```
┌──────────────────────────────────────────┐
│  Settings                          [X]   │
├──────────────────────────────────────────┤
│                                          │
│  Steering Wheel                          │
│  Device: [Logitech G923         ▼]  [Refresh] │
│                                          │
│  FFB Engine                              │
│  Update Rate:  [360 Hz ▼]               │
│  Slew Limit:   [──────●────] 0.50       │
│  Deadzone:     [●───────────] 0.01      │
│                                          │
│  Centering Spring (off-track)           │
│  Strength: [●───────────] 0.05         │
│  Damping:  [●───────────] 0.02         │
│                                          │
│  ☑ Start with Windows                   │
│  ☑ Minimize to tray on close            │
│  ☑ Show FFB waveform                    │
│                                          │
│  [ Restore Defaults ]    [ Close ]       │
└──────────────────────────────────────────┘
```

### Start with Windows
- Creates/removes registry key: `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`
- Key name: `DirtFFBPlugin`
- Value: path to the exe

### Settings Persistence
All settings saved to `%APPDATA%\DirtFFB\settings.json`:
```json
{
    "wizard_complete": true,
    "device_guid": "{XXXXXXXX-...}",
    "device_name": "Logitech G923 Racing Wheel",
    "driver_type": "directinput",
    "last_profile": "dirt_sprint_car",
    "start_with_windows": false,
    "minimize_to_tray": true,
    "show_waveform": true
}
```
Create `src/settings.py` to manage this file.

---

## Implementation Details

### File to Create
`src/gui/plugin_window.py` — contains:
- `PluginMainWindow(QMainWindow)` — main running screen
- `SetupWizard(QDialog)` — first-run wizard
- `SettingsDialog(QDialog)` — settings screen
- `StatusDot(QWidget)` — small colored circle widget for status indicators

### `plugin_main.py` (root of repo)
```python
"""Entry point for the consumer wheel plugin exe."""
import sys
from PyQt5.QtWidgets import QApplication
from src.settings import AppSettings
from src.gui.plugin_window import PluginMainWindow, SetupWizard
from src.telemetry.iracing_reader import IRacingTelemetry
from src.ffb.engine import FFBEngine
from src.hardware.directinput_driver import DirectInputFFBDriver
from src.wheel_detect import enumerate_ffb_wheels
from src.config import load_profile

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Dirt FFB Plugin")
    app.setQuitOnLastWindowClosed(False)  # keep alive in tray

    settings = AppSettings()

    # First run wizard
    if not settings.wizard_complete:
        wizard = SetupWizard(settings)
        if wizard.exec_() != QDialog.Accepted:
            sys.exit(0)

    # Wire up components
    telemetry = IRacingTelemetry()
    hardware = DirectInputFFBDriver(device_guid=settings.device_guid)
    engine = FFBEngine(telemetry)

    window = PluginMainWindow(telemetry, engine, hardware, settings)
    window.show()

    sys.exit(app.exec_())
```

### Styling
- Use PyQt5 Fusion style (same as main app)
- Dark theme (same palette as `main.py`)
- Minimum window size: 520 x 600
- Resizable but effects section uses stretch
- Font: "Segoe UI" 10pt (standard Windows UI font)

### Error States to Handle Gracefully
| Situation | What to show |
|-----------|-------------|
| No wheel detected at startup | Wizard step 2, prompt to connect |
| Wheel disconnected mid-session | Yellow status dot, "Wheel disconnected — reconnect and click Refresh" |
| iRacing not running | Yellow iRacing dot, "Waiting for iRacing..." |
| DirectInput exclusive access denied | Red dot, "Another app is controlling your wheel. Close Logitech G Hub / Fanatec software." |
| Profile file missing | Show error, load default_profile.json as fallback |

---

## Files to Create

| File | Description |
|------|-------------|
| `src/gui/plugin_window.py` | All three screens (main, wizard, settings) |
| `src/settings.py` | AppSettings class, reads/writes %APPDATA%\DirtFFB\settings.json |
| `plugin_main.py` | Entry point for consumer exe |
| `assets/icon.ico` | App icon (256x256, multi-size ICO) |
| `assets/setup_guide.png` | Screenshot showing iRacing FFB=0 setting |
