"""
Consumer wheel plugin GUI.

Three screens:
  - SetupWizard  — first-run device picker and iRacing instructions
  - PluginMainWindow — main running screen with status, waveform, sliders
  - SettingsDialog  — update rate, slew, centering, tray preferences

All three are dark-themed (Fusion palette) and sized for non-technical users.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt5.QtCore import Qt, QTimer, QSize
from PyQt5.QtGui import (
    QColor, QFont, QIcon, QPainter, QPen, QPixmap,
)
from PyQt5.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFrame, QGroupBox, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QPushButton, QScrollArea, QSizePolicy, QSlider,
    QSpinBox, QStackedWidget, QSystemTrayIcon, QVBoxLayout, QWidget,
)

from src.settings import AppSettings
from src.wheel_detect import WheelDevice, enumerate_ffb_wheels

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Small helper widgets
# ---------------------------------------------------------------------------

class StatusDot(QWidget):
    """Small filled circle that indicates connection state."""

    RED    = QColor(220,  50,  50)
    YELLOW = QColor(220, 180,  30)
    GREEN  = QColor( 50, 200,  80)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._color = self.RED
        self.setFixedSize(14, 14)

    def set_red(self):
        self._color = self.RED
        self.update()

    def set_yellow(self):
        self._color = self.YELLOW
        self.update()

    def set_green(self):
        self._color = self.GREEN
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(self._color)
        p.setPen(Qt.NoPen)
        p.drawEllipse(1, 1, 12, 12)
        p.end()


class TorqueBar(QWidget):
    """Horizontal bar showing current torque output with center at zero."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._value = 0.0
        self.setMinimumHeight(32)

    def set_value(self, value: float):
        self._value = max(-1.0, min(1.0, value))
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        cx = w // 2

        p.fillRect(0, 0, w, h, QColor(30, 30, 30))
        p.setPen(QPen(QColor(80, 80, 80), 1))
        p.drawLine(cx, 0, cx, h)

        bar_w = int(abs(self._value) * cx)
        if self._value > 0:
            p.fillRect(cx, 4, bar_w, h - 8, QColor(0, 150, 255))
        elif self._value < 0:
            p.fillRect(cx - bar_w, 4, bar_w, h - 8, QColor(255, 100, 0))

        p.setPen(QColor(220, 220, 220))
        p.setFont(QFont("Segoe UI", 9))
        p.drawText(0, 0, w, h, Qt.AlignCenter, f"{self._value:+.3f}")
        p.end()


class WaveformWidget(QWidget):
    """Scrolling waveform display."""

    def __init__(self, parent=None, buffer_size: int = 300):
        super().__init__(parent)
        self._buf = [0.0] * buffer_size
        self._size = buffer_size
        self.setMinimumHeight(90)

    def add_sample(self, value: float):
        self._buf.append(max(-1.0, min(1.0, value)))
        if len(self._buf) > self._size:
            self._buf.pop(0)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        cy = h // 2

        p.fillRect(0, 0, w, h, QColor(20, 20, 25))
        p.setPen(QPen(QColor(50, 50, 60), 1))
        p.drawLine(0, cy, w, cy)
        p.drawLine(0, h // 4, w, h // 4)
        p.drawLine(0, 3 * h // 4, w, 3 * h // 4)

        if len(self._buf) >= 2:
            p.setPen(QPen(QColor(0, 200, 100), 1))
            step = w / (self._size - 1)
            for i in range(1, len(self._buf)):
                x0 = int((i - 1) * step)
                x1 = int(i * step)
                y0 = int(cy - self._buf[i - 1] * (cy - 4))
                y1 = int(cy - self._buf[i] * (cy - 4))
                p.drawLine(x0, y0, x1, y1)
        p.end()


# ---------------------------------------------------------------------------
# Setup Wizard
# ---------------------------------------------------------------------------

class SetupWizard(QDialog):
    """
    Three-step first-run wizard:
      Page 0 — Welcome
      Page 1 — Select wheel
      Page 2 — iRacing FFB=0 instructions
    """

    def __init__(self, settings: AppSettings, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._devices: list[WheelDevice] = []
        self._selected_device: WheelDevice | None = None

        self.setWindowTitle("Dirt FFB Plugin — Setup")
        self.setMinimumSize(480, 360)
        self.setModal(True)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._make_page_welcome())
        self._stack.addWidget(self._make_page_wheel())
        self._stack.addWidget(self._make_page_iracing())

        root = QVBoxLayout(self)
        root.addWidget(self._stack)

    # ---- Pages -----------------------------------------------------------

    def _make_page_welcome(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(16)
        lay.setContentsMargins(40, 40, 40, 40)

        title = QLabel("Dirt FFB Plugin")
        title.setFont(QFont("Segoe UI", 18, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        lay.addWidget(title)

        body = QLabel(
            "Add telemetry-based force feedback for dirt cars in iRacing.\n\n"
            "Works with Fanatec, Logitech, Thrustmaster, and more.\n\n"
            "This wizard takes about 1 minute."
        )
        body.setWordWrap(True)
        body.setAlignment(Qt.AlignCenter)
        lay.addWidget(body)

        lay.addStretch()

        btn = QPushButton("Get Started →")
        btn.setMinimumHeight(40)
        btn.clicked.connect(lambda: self._stack.setCurrentIndex(1))
        lay.addWidget(btn, alignment=Qt.AlignCenter)

        return w

    def _make_page_wheel(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(12)
        lay.setContentsMargins(32, 32, 32, 32)

        lay.addWidget(QLabel("Step 2 of 3 — Select Your Steering Wheel"))

        self._wheel_list = QComboBox()
        self._wheel_list.setMinimumHeight(36)
        lay.addWidget(self._wheel_list)

        self._wheel_hint = QLabel(
            "Don't see your wheel? Make sure it is plugged in and drivers are installed."
        )
        self._wheel_hint.setWordWrap(True)
        self._wheel_hint.setStyleSheet("color: #aaa;")
        lay.addWidget(self._wheel_hint)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.setFixedWidth(100)
        refresh_btn.clicked.connect(self._refresh_wheels)
        lay.addWidget(refresh_btn)

        lay.addStretch()

        nav = QHBoxLayout()
        back_btn = QPushButton("← Back")
        back_btn.clicked.connect(lambda: self._stack.setCurrentIndex(0))
        nav.addWidget(back_btn)
        nav.addStretch()
        next_btn = QPushButton("Next →")
        next_btn.setMinimumWidth(120)
        next_btn.clicked.connect(self._on_wheel_next)
        nav.addWidget(next_btn)
        lay.addLayout(nav)

        self._refresh_wheels()
        return w

    def _make_page_iracing(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(12)
        lay.setContentsMargins(32, 32, 32, 32)

        lay.addWidget(QLabel("Step 3 of 3 — One-Time iRacing Setting"))

        instructions = QLabel(
            "You must set iRacing's Force Feedback Strength to 0% so this app can "
            "control your wheel directly.\n\n"
            "In iRacing:\n"
            "  Options → Controls → Force Feedback\n"
            "  Set \"Force Feedback Strength\" to 0"
        )
        instructions.setWordWrap(True)
        lay.addWidget(instructions)

        screenshot_btn = QPushButton("Show me a screenshot")
        screenshot_btn.setFixedWidth(200)
        screenshot_btn.clicked.connect(self._show_screenshot)
        lay.addWidget(screenshot_btn)

        lay.addStretch()

        nav = QHBoxLayout()
        back_btn = QPushButton("← Back")
        back_btn.clicked.connect(lambda: self._stack.setCurrentIndex(1))
        nav.addWidget(back_btn)
        nav.addStretch()
        done_btn = QPushButton("Done — Launch App")
        done_btn.setMinimumWidth(160)
        done_btn.setMinimumHeight(40)
        done_btn.clicked.connect(self._on_done)
        nav.addWidget(done_btn)
        lay.addLayout(nav)

        return w

    # ---- Actions ---------------------------------------------------------

    def _refresh_wheels(self):
        self._devices = enumerate_ffb_wheels()
        self._wheel_list.clear()
        if self._devices:
            for d in self._devices:
                self._wheel_list.addItem(d.device_name)
            self._wheel_hint.setText("")
        else:
            self._wheel_hint.setText(
                "No FFB wheels detected. Make sure your wheel is plugged in "
                "and its drivers are installed, then click Refresh."
            )

    def _on_wheel_next(self):
        idx = self._wheel_list.currentIndex()
        if idx < 0 or not self._devices:
            QMessageBox.warning(self, "No Wheel Selected",
                                "Please connect a wheel and click Refresh.")
            return
        self._selected_device = self._devices[idx]
        self._stack.setCurrentIndex(2)

    def _show_screenshot(self):
        from src.resource_path import resource_path
        img_path = resource_path("assets/setup_guide.png")
        if not img_path.exists():
            QMessageBox.information(self, "Screenshot",
                                    "Screenshot not available.\n"
                                    "In iRacing: Options → Controls → Force Feedback → set Strength to 0.")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("iRacing FFB Setting")
        lay = QVBoxLayout(dlg)
        lbl = QLabel()
        pix = QPixmap(str(img_path))
        lbl.setPixmap(pix.scaledToWidth(600, Qt.SmoothTransformation))
        lay.addWidget(lbl)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dlg.accept)
        lay.addWidget(close_btn)
        dlg.exec_()

    def _on_done(self):
        if self._selected_device:
            self._settings.device_guid = self._selected_device.instance_guid
            self._settings.device_name = self._selected_device.device_name
        self._settings.wizard_complete = True
        self._settings.save()
        self.accept()


# ---------------------------------------------------------------------------
# Settings Dialog
# ---------------------------------------------------------------------------

class SettingsDialog(QDialog):

    def __init__(self, settings: AppSettings, engine_config, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._engine_config = engine_config
        self.setWindowTitle("Settings")
        self.setMinimumWidth(400)
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setSpacing(16)

        # --- Wheel ---
        wheel_group = QGroupBox("Steering Wheel")
        wlay = QHBoxLayout(wheel_group)
        self._device_combo = QComboBox()
        self._device_combo.setMinimumWidth(220)
        self._populate_devices()
        wlay.addWidget(QLabel("Device:"))
        wlay.addWidget(self._device_combo, stretch=1)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.setFixedWidth(72)
        refresh_btn.clicked.connect(self._populate_devices)
        wlay.addWidget(refresh_btn)
        lay.addWidget(wheel_group)

        # --- FFB Engine ---
        eng_group = QGroupBox("FFB Engine")
        elay = self._make_form()

        self._rate_combo = QComboBox()
        for r in [120, 180, 240, 360]:
            self._rate_combo.addItem(f"{r} Hz", r)
        target_rate = int(getattr(self._engine_config, "output_rate_hz", 360))
        for i in range(self._rate_combo.count()):
            if self._rate_combo.itemData(i) == target_rate:
                self._rate_combo.setCurrentIndex(i)
                break
        elay.addRow("Update Rate:", self._rate_combo)

        self._slew_spin = self._double_spin(
            getattr(self._engine_config, "slew_rate_limit", 0.5), 0.01, 1.0, 0.05
        )
        elay.addRow("Slew Limit:", self._slew_spin)

        self._dz_spin = self._double_spin(
            getattr(self._engine_config, "deadzone", 0.01), 0.0, 0.1, 0.005, decimals=3
        )
        elay.addRow("Deadzone:", self._dz_spin)

        eng_group.setLayout(elay)
        lay.addWidget(eng_group)

        # --- Centering spring ---
        spring_group = QGroupBox("Centering Spring (off-track)")
        slay = self._make_form()

        self._spring_spin = self._double_spin(
            getattr(self._engine_config, "center_spring_gain", 0.05), 0.0, 1.0, 0.05
        )
        slay.addRow("Strength:", self._spring_spin)

        self._damp_spin = self._double_spin(
            getattr(self._engine_config, "center_spring_damping", 0.02), 0.0, 1.0, 0.01
        )
        slay.addRow("Damping:", self._damp_spin)

        spring_group.setLayout(slay)
        lay.addWidget(spring_group)

        # --- Preferences ---
        pref_group = QGroupBox("Preferences")
        play = QVBoxLayout(pref_group)

        self._startup_cb = QCheckBox("Start with Windows")
        self._startup_cb.setChecked(self._settings.start_with_windows)
        play.addWidget(self._startup_cb)

        self._tray_cb = QCheckBox("Minimize to tray on close")
        self._tray_cb.setChecked(self._settings.minimize_to_tray)
        play.addWidget(self._tray_cb)

        self._waveform_cb = QCheckBox("Show FFB waveform")
        self._waveform_cb.setChecked(self._settings.show_waveform)
        play.addWidget(self._waveform_cb)

        lay.addWidget(pref_group)

        # --- Buttons ---
        btn_row = QHBoxLayout()
        restore_btn = QPushButton("Restore Defaults")
        restore_btn.clicked.connect(self._on_restore_defaults)
        btn_row.addWidget(restore_btn)
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setDefault(True)
        close_btn.clicked.connect(self._on_close)
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)

    # ---- Helpers ---------------------------------------------------------

    @staticmethod
    def _make_form():
        from PyQt5.QtWidgets import QFormLayout
        f = QFormLayout()
        f.setSpacing(8)
        return f

    @staticmethod
    def _double_spin(value, lo, hi, step, decimals=2):
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setSingleStep(step)
        s.setDecimals(decimals)
        s.setValue(value)
        return s

    def _populate_devices(self):
        self._device_combo.clear()
        devices = enumerate_ffb_wheels()
        for d in devices:
            self._device_combo.addItem(d.device_name, d)
        # Try to select currently saved device
        for i in range(self._device_combo.count()):
            d = self._device_combo.itemData(i)
            if d and d.instance_guid == self._settings.device_guid:
                self._device_combo.setCurrentIndex(i)
                break
        if not devices:
            self._device_combo.addItem("No FFB wheels detected")

    def _on_close(self):
        # Persist settings
        dev = self._device_combo.currentData()
        if dev and isinstance(dev, WheelDevice):
            self._settings.device_guid = dev.instance_guid
            self._settings.device_name = dev.device_name

        self._engine_config.output_rate_hz  = self._rate_combo.currentData()
        self._engine_config.slew_rate_limit  = self._slew_spin.value()
        self._engine_config.deadzone         = self._dz_spin.value()
        self._engine_config.center_spring_gain    = self._spring_spin.value()
        self._engine_config.center_spring_damping = self._damp_spin.value()

        self._settings.start_with_windows = self._startup_cb.isChecked()
        self._settings.minimize_to_tray   = self._tray_cb.isChecked()
        self._settings.show_waveform       = self._waveform_cb.isChecked()
        self._settings.save()
        self.accept()

    def _on_restore_defaults(self):
        if QMessageBox.question(self, "Restore Defaults",
                                "Reset all settings to defaults?") == QMessageBox.Yes:
            self._settings.reset_to_defaults()
            self.reject()   # close; caller should reopen if needed


# ---------------------------------------------------------------------------
# Main Window
# ---------------------------------------------------------------------------

_EFFECT_DISPLAY_NAMES = {
    "rear_traction_loss":  "Rear Traction Loss",
    "dirt_yaw_feedback":   "Yaw Feedback",
    "throttle_steer":      "Throttle-Steer",
    "dirt_surface_rumble": "Surface Rumble",
    "engine_vibration":    "Engine Vibration",
    "self_aligning_torque": "Self-Aligning Torque",
    "curb_rumble":         "Curb Rumble",
    "tire_slip":           "Tire Slip",
    "collision_impact":    "Collision Impact",
    "suspension":          "Suspension",
}


class PluginMainWindow(QMainWindow):
    """
    Main consumer wheel plugin window.

    Shows iRacing + wheel status, a torque bar + waveform, profile selector,
    master gain / max force sliders, and an expandable advanced effects panel.
    """

    def __init__(self, telemetry, engine, hardware, settings: AppSettings, parent=None):
        super().__init__(parent)
        self._telemetry = telemetry
        self._engine    = engine
        self._hardware  = hardware
        self._settings  = settings

        self.setWindowTitle("Dirt FFB Plugin")
        self.setMinimumSize(520, 600)

        self._adv_expanded = False
        self._build_ui()
        self._setup_tray()
        self._setup_timers()
        self._load_last_profile()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        lay = QVBoxLayout(root)
        lay.setSpacing(8)
        lay.setContentsMargins(12, 10, 12, 10)

        # Status row
        status_frame = QFrame()
        status_frame.setFrameShape(QFrame.StyledPanel)
        slay = QVBoxLayout(status_frame)
        slay.setSpacing(4)

        ir_row = QHBoxLayout()
        self._ir_dot = StatusDot()
        ir_row.addWidget(self._ir_dot)
        self._ir_label = QLabel("iRacing  —  Not connected")
        ir_row.addWidget(self._ir_label)
        ir_row.addStretch()
        slay.addLayout(ir_row)

        wheel_row = QHBoxLayout()
        self._wheel_dot = StatusDot()
        wheel_row.addWidget(self._wheel_dot)
        self._wheel_label = QLabel("Wheel  —  Not connected")
        wheel_row.addWidget(self._wheel_label)
        wheel_row.addStretch()
        slay.addLayout(wheel_row)

        lay.addWidget(status_frame)

        # Torque output group
        out_group = QGroupBox("Torque Output")
        olay = QVBoxLayout(out_group)
        self._torque_bar = TorqueBar()
        olay.addWidget(self._torque_bar)
        self._waveform = WaveformWidget()
        self._waveform.setVisible(self._settings.show_waveform)
        olay.addWidget(self._waveform)
        lay.addWidget(out_group)

        # Profile row
        prof_row = QHBoxLayout()
        prof_row.addWidget(QLabel("Profile:"))
        self._profile_combo = QComboBox()
        self._profile_combo.setMinimumWidth(160)
        self._refresh_profiles()
        prof_row.addWidget(self._profile_combo, stretch=1)
        load_btn = QPushButton("Load")
        load_btn.setFixedWidth(60)
        load_btn.clicked.connect(self._on_load_profile)
        prof_row.addWidget(load_btn)
        save_btn = QPushButton("Save")
        save_btn.setFixedWidth(60)
        save_btn.clicked.connect(self._on_save_profile)
        prof_row.addWidget(save_btn)
        lay.addLayout(prof_row)

        # Master gain slider
        gain_group = QGroupBox("Overall Strength")
        glay = QHBoxLayout(gain_group)
        self._gain_slider = QSlider(Qt.Horizontal)
        self._gain_slider.setRange(0, 200)
        self._gain_slider.setValue(int(self._engine.config.master_gain * 100))
        self._gain_slider.setTickInterval(25)
        self._gain_slider.setTickPosition(QSlider.TicksBelow)
        self._gain_label = QLabel(f"{int(self._engine.config.master_gain * 100)}%")
        self._gain_label.setMinimumWidth(40)
        self._gain_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._gain_slider.valueChanged.connect(self._on_gain_changed)
        glay.addWidget(self._gain_slider, stretch=1)
        glay.addWidget(self._gain_label)
        lay.addWidget(gain_group)

        # Max force cap slider
        cap_group = QGroupBox("Max Force Cap")
        clay = QHBoxLayout(cap_group)
        self._cap_slider = QSlider(Qt.Horizontal)
        self._cap_slider.setRange(0, 100)
        self._cap_slider.setValue(int(self._engine.config.max_torque_pct))
        self._cap_slider.setTickInterval(10)
        self._cap_slider.setTickPosition(QSlider.TicksBelow)
        self._cap_label = QLabel(f"{int(self._engine.config.max_torque_pct)}%")
        self._cap_label.setMinimumWidth(40)
        self._cap_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._cap_slider.valueChanged.connect(self._on_cap_changed)
        clay.addWidget(self._cap_slider, stretch=1)
        clay.addWidget(self._cap_label)
        lay.addWidget(cap_group)

        # Advanced effects toggle
        self._adv_toggle = QPushButton("Advanced Effects ▼")
        self._adv_toggle.setFlat(True)
        self._adv_toggle.setStyleSheet("text-align: left; padding: 4px;")
        self._adv_toggle.clicked.connect(self._toggle_advanced)
        lay.addWidget(self._adv_toggle)

        self._adv_frame = QFrame()
        self._adv_frame.setFrameShape(QFrame.StyledPanel)
        adv_lay = QVBoxLayout(self._adv_frame)
        adv_lay.setSpacing(4)

        self._effect_rows: dict[str, tuple] = {}  # name → (checkbox, slider, label)
        for eff_name, display in _EFFECT_DISPLAY_NAMES.items():
            if eff_name not in self._engine.effects:
                continue
            row = QHBoxLayout()
            cb = QCheckBox()
            cb.setChecked(self._engine.effects[eff_name].config.enabled)
            cb.setFixedWidth(20)
            cb.stateChanged.connect(lambda state, n=eff_name:
                self._engine.set_effect_enabled(n, state == Qt.Checked))
            row.addWidget(cb)

            lbl = QLabel(display)
            lbl.setMinimumWidth(140)
            row.addWidget(lbl)

            sl = QSlider(Qt.Horizontal)
            sl.setRange(0, 200)
            sl.setValue(int(self._engine.effects[eff_name].config.gain * 100))
            val_lbl = QLabel(f"{self._engine.effects[eff_name].config.gain:.2f}")
            val_lbl.setMinimumWidth(36)
            val_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            sl.valueChanged.connect(lambda v, n=eff_name, vl=val_lbl: (
                self._engine.set_effect_gain(n, v / 100.0),
                vl.setText(f"{v / 100.0:.2f}"),
            ))
            row.addWidget(sl, stretch=1)
            row.addWidget(val_lbl)

            self._effect_rows[eff_name] = (cb, sl, val_lbl)
            adv_lay.addLayout(row)

        self._adv_frame.setVisible(False)
        lay.addWidget(self._adv_frame)

        lay.addStretch()

        # Bottom settings button
        settings_btn = QPushButton("⚙  Settings")
        settings_btn.setFixedWidth(120)
        settings_btn.clicked.connect(self._open_settings)
        lay.addWidget(settings_btn, alignment=Qt.AlignRight)

    def _setup_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self._tray = None
            return

        from src.resource_path import resource_path
        icon_path = resource_path("assets/icon.ico")
        if icon_path.exists():
            icon = QIcon(str(icon_path))
        else:
            # Fallback: generate a tiny green square icon
            pix = QPixmap(16, 16)
            pix.fill(QColor(50, 200, 80))
            icon = QIcon(pix)

        self._tray = QSystemTrayIcon(icon, self)
        self._tray.setToolTip("Dirt FFB Plugin")

        from PyQt5.QtWidgets import QMenu, QAction
        menu = QMenu()
        open_act = QAction("Open", self)
        open_act.triggered.connect(self.show_and_raise)
        menu.addAction(open_act)

        disable_act = QAction("Disable FFB", self)
        disable_act.triggered.connect(self._disable_ffb)
        menu.addAction(disable_act)

        menu.addSeparator()
        exit_act = QAction("Exit", self)
        exit_act.triggered.connect(self._quit_app)
        menu.addAction(exit_act)

        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _setup_timers(self):
        self._ui_timer = QTimer()
        self._ui_timer.timeout.connect(self._update_ui)
        self._ui_timer.start(33)

        self._status_timer = QTimer()
        self._status_timer.timeout.connect(self._update_status)
        self._status_timer.start(500)

    # ------------------------------------------------------------------
    # Slots / event handlers
    # ------------------------------------------------------------------

    def show_and_raise(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _toggle_advanced(self):
        self._adv_expanded = not self._adv_expanded
        self._adv_frame.setVisible(self._adv_expanded)
        arrow = "▲" if self._adv_expanded else "▼"
        self._adv_toggle.setText(f"Advanced Effects {arrow}")

    def _on_gain_changed(self, value: int):
        self._engine.config.master_gain = value / 100.0
        self._gain_label.setText(f"{value}%")

    def _on_cap_changed(self, value: int):
        self._engine.config.max_torque_pct = float(value)
        self._cap_label.setText(f"{value}%")

    def _open_settings(self):
        dlg = SettingsDialog(self._settings, self._engine.config, self)
        dlg.exec_()
        # Reflect any waveform visibility change
        self._waveform.setVisible(self._settings.show_waveform)

    def _disable_ffb(self):
        self._engine.set_hardware_callback(None)
        if hasattr(self._hardware, "disable"):
            self._hardware.disable()

    def _quit_app(self):
        self._cleanup()
        QApplication.quit()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self.show_and_raise()

    def _refresh_profiles(self):
        from src.config import list_profiles
        self._profile_combo.clear()
        for p in list_profiles():
            self._profile_combo.addItem(p.stem, str(p))

    def _load_last_profile(self):
        """Load the profile saved in settings, or default if unavailable."""
        from src.config import list_profiles, load_profile, apply_engine_config, apply_effect_configs
        from src.resource_path import resource_path

        last = self._settings.last_profile
        for i in range(self._profile_combo.count()):
            if self._profile_combo.itemText(i) == last:
                self._profile_combo.setCurrentIndex(i)
                break

        path_str = self._profile_combo.currentData()
        if not path_str:
            # Try default_profile.json
            default = resource_path("config/default_profile.json")
            if default.exists():
                path_str = str(default)

        if path_str:
            try:
                self._apply_profile_path(path_str)
            except Exception as exc:
                logger.warning("Could not load last profile: %s", exc)

    def _on_load_profile(self):
        path_str = self._profile_combo.currentData()
        if not path_str:
            return
        try:
            self._apply_profile_path(path_str)
            self._settings.last_profile = self._profile_combo.currentText()
            self._settings.save()
        except Exception as exc:
            QMessageBox.warning(self, "Load Error", str(exc))

    def _apply_profile_path(self, path_str: str):
        from src.config import load_profile, apply_engine_config, apply_effect_configs
        profile = load_profile(path_str)
        eng = apply_engine_config(profile)
        self._engine.config.master_gain         = eng.master_gain
        self._engine.config.max_torque_pct      = eng.max_torque_pct
        self._engine.config.slew_rate_limit     = eng.slew_rate_limit
        self._engine.config.deadzone            = eng.deadzone
        self._engine.config.center_spring_gain  = eng.center_spring_gain
        self._engine.config.center_spring_damping = eng.center_spring_damping
        self._engine.config.invert_output        = eng.invert_output

        # Sync sliders
        self._gain_slider.setValue(int(eng.master_gain * 100))
        self._cap_slider.setValue(int(eng.max_torque_pct))

        effects = apply_effect_configs(profile)
        for name, cfg in effects.items():
            if name in self._engine.effects:
                self._engine.effects[name].config.gain    = cfg.get("gain", 1.0)
                self._engine.effects[name].config.enabled = cfg.get("enabled", True)
            if name in self._effect_rows:
                cb, sl, vl = self._effect_rows[name]
                cb.setChecked(cfg.get("enabled", True))
                gain = cfg.get("gain", 1.0)
                sl.setValue(int(gain * 100))
                vl.setText(f"{gain:.2f}")

    def _on_save_profile(self):
        from PyQt5.QtWidgets import QFileDialog
        from src.config import save_profile, build_profile_from_state
        from src.resource_path import resource_path
        name, _ = QFileDialog.getSaveFileName(
            self, "Save Profile",
            str(resource_path("config")),
            "JSON Files (*.json)",
        )
        if not name:
            return
        profile = build_profile_from_state(
            profile_name=Path(name).stem,
            engine_config=self._engine.config,
            hardware_config={},
            effects=self._engine.effects,
        )
        try:
            save_profile(profile, name)
            self._refresh_profiles()
        except Exception as exc:
            QMessageBox.warning(self, "Save Error", str(exc))

    # ------------------------------------------------------------------
    # Periodic updates
    # ------------------------------------------------------------------

    def _update_ui(self):
        output = self._engine.output
        self._torque_bar.set_value(output)
        if self._settings.show_waveform:
            self._waveform.add_sample(output)

    def _update_status(self):
        # iRacing status
        if self._telemetry.connected:
            telem = self._telemetry.latest
            if telem.is_on_track:
                self._ir_dot.set_green()
                track_info = "On Track"
            else:
                self._ir_dot.set_yellow()
                track_info = "In Garage / Not on track"
            self._ir_label.setText(f"iRacing  —  {track_info}")
        else:
            self._ir_dot.set_yellow()
            self._ir_label.setText("iRacing  —  Waiting for iRacing...")

        # Wheel / hardware status
        if self._hardware.is_enabled:
            self._wheel_dot.set_green()
            name = self._settings.device_name or "Wheel"
            self._wheel_label.setText(f"Wheel  —  {name} Active")
        elif self._hardware.is_connected:
            self._wheel_dot.set_yellow()
            self._wheel_label.setText("Wheel  —  Connected (FFB disabled)")
        else:
            err = ""
            if hasattr(self._hardware, "_status"):
                err = self._hardware._status.error_message
            self._wheel_dot.set_red()
            if err:
                self._wheel_label.setText(f"Wheel  —  {err}")
            else:
                self._wheel_label.setText("Wheel  —  Disconnected")

        # Update tray icon color
        if self._tray:
            if self._hardware.is_enabled and self._telemetry.connected:
                pix = QPixmap(16, 16)
                pix.fill(QColor(50, 200, 80))
            else:
                pix = QPixmap(16, 16)
                pix.fill(QColor(220, 50, 50))
            self._tray.setIcon(QIcon(pix))

    # ------------------------------------------------------------------
    # Window / app lifecycle
    # ------------------------------------------------------------------

    def closeEvent(self, event):
        if self._settings.minimize_to_tray and self._tray:
            event.ignore()
            self.hide()
        else:
            self._cleanup()
            event.accept()

    def _cleanup(self):
        self._engine.set_hardware_callback(None)
        self._engine.stop()
        if self._hardware.is_enabled:
            self._hardware.disable()
        if self._hardware.is_connected:
            self._hardware.disconnect()
        self._telemetry.stop()
        if self._tray:
            self._tray.hide()
