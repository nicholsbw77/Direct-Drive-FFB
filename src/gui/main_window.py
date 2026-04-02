"""
PyQt5 GUI for Direct Drive FFB configuration and monitoring.

Features:
- Real-time torque output visualization (bar + waveform)
- Per-effect gain sliders with enable/disable toggles
- Engine settings (master gain, max torque, slew rate, etc.)
- Hardware connection controls
- Profile load/save
- iRacing connection status
"""

import time
from pathlib import Path

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QGroupBox, QLabel, QSlider, QCheckBox, QPushButton, QComboBox,
    QSpinBox, QDoubleSpinBox, QStatusBar, QFileDialog, QTabWidget,
    QFrame, QSplitter, QLineEdit, QMessageBox,
)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QPainter, QColor, QPen, QFont

from src.ffb.engine import FFBEngine, EngineConfig
from src.ffb.effects import BaseEffect
from src.hardware.modbus_driver import AASDModbusDriver
from src.hardware.base import HardwareStatus
from src.telemetry.iracing_reader import IRacingTelemetry
from src.config import (
    load_profile, save_profile, list_profiles, build_profile_from_state,
    apply_engine_config, get_hardware_config, apply_effect_configs,
)


class TorqueBar(QWidget):
    """Horizontal bar showing current torque output with center at zero."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._value = 0.0
        self.setMinimumHeight(40)
        self.setMinimumWidth(200)

    def set_value(self, value: float):
        self._value = max(-1.0, min(1.0, value))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        center_x = w // 2

        # Background
        painter.fillRect(0, 0, w, h, QColor(30, 30, 30))

        # Center line
        painter.setPen(QPen(QColor(80, 80, 80), 1))
        painter.drawLine(center_x, 0, center_x, h)

        # Torque bar
        bar_width = int(abs(self._value) * center_x)
        if self._value > 0:
            color = QColor(0, 150, 255)
            painter.fillRect(center_x, 4, bar_width, h - 8, color)
        elif self._value < 0:
            color = QColor(255, 100, 0)
            painter.fillRect(center_x - bar_width, 4, bar_width, h - 8, color)

        # Value text
        painter.setPen(QColor(220, 220, 220))
        painter.setFont(QFont("Consolas", 10))
        painter.drawText(0, 0, w, h, Qt.AlignCenter, f"{self._value:+.3f}")

        painter.end()


class WaveformWidget(QWidget):
    """Scrolling waveform display of torque output over time."""

    def __init__(self, parent=None, buffer_size: int = 300):
        super().__init__(parent)
        self._buffer = [0.0] * buffer_size
        self._buffer_size = buffer_size
        self.setMinimumHeight(120)
        self.setMinimumWidth(300)

    def add_sample(self, value: float):
        self._buffer.append(max(-1.0, min(1.0, value)))
        if len(self._buffer) > self._buffer_size:
            self._buffer.pop(0)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        center_y = h // 2

        # Background
        painter.fillRect(0, 0, w, h, QColor(20, 20, 25))

        # Grid lines
        painter.setPen(QPen(QColor(50, 50, 60), 1))
        painter.drawLine(0, center_y, w, center_y)
        painter.drawLine(0, h // 4, w, h // 4)
        painter.drawLine(0, 3 * h // 4, w, 3 * h // 4)

        # Waveform
        if len(self._buffer) < 2:
            painter.end()
            return

        painter.setPen(QPen(QColor(0, 200, 100), 2))
        step = w / (self._buffer_size - 1)

        for i in range(1, len(self._buffer)):
            x0 = int((i - 1) * step)
            x1 = int(i * step)
            y0 = int(center_y - self._buffer[i - 1] * (center_y - 5))
            y1 = int(center_y - self._buffer[i] * (center_y - 5))
            painter.drawLine(x0, y0, x1, y1)

        painter.end()


class EffectSliderGroup(QGroupBox):
    """A group of controls for a single FFB effect."""

    def __init__(self, effect_name: str, display_name: str, engine: FFBEngine, parent=None):
        super().__init__(display_name, parent)
        self.effect_name = effect_name
        self.engine = engine

        layout = QHBoxLayout()

        # Enable checkbox
        self.enabled_cb = QCheckBox("On")
        self.enabled_cb.setChecked(True)
        self.enabled_cb.stateChanged.connect(self._on_enabled_changed)
        layout.addWidget(self.enabled_cb)

        # Gain slider
        self.gain_slider = QSlider(Qt.Horizontal)
        self.gain_slider.setRange(0, 200)
        self.gain_slider.setValue(100)
        self.gain_slider.setTickInterval(25)
        self.gain_slider.setTickPosition(QSlider.TicksBelow)
        self.gain_slider.valueChanged.connect(self._on_gain_changed)
        layout.addWidget(self.gain_slider, stretch=3)

        # Gain value label
        self.gain_label = QLabel("1.00")
        self.gain_label.setMinimumWidth(40)
        self.gain_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.gain_label)

        # Live output bar
        self.output_bar = TorqueBar()
        self.output_bar.setMaximumHeight(20)
        layout.addWidget(self.output_bar, stretch=2)

        self.setLayout(layout)

    def _on_gain_changed(self, value: int):
        gain = value / 100.0
        self.gain_label.setText(f"{gain:.2f}")
        self.engine.set_effect_gain(self.effect_name, gain)

    def _on_enabled_changed(self, state: int):
        self.engine.set_effect_enabled(self.effect_name, state == Qt.Checked)

    def set_output_value(self, value: float):
        self.output_bar.set_value(value)

    def load_config(self, config: dict):
        self.enabled_cb.setChecked(config.get("enabled", True))
        gain = config.get("gain", 1.0)
        self.gain_slider.setValue(int(gain * 100))


class MainWindow(QMainWindow):
    """Main application window."""

    def __init__(self, telemetry: IRacingTelemetry, engine: FFBEngine,
                 hardware: AASDModbusDriver):
        super().__init__()
        self.telemetry = telemetry
        self.engine = engine
        self.hardware = hardware

        self.setWindowTitle("Direct Drive FFB - AASD Servo Controller")
        self.setMinimumSize(900, 700)

        self._build_ui()
        self._setup_timers()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        # Top: Status bar with connection info
        status_frame = self._build_status_bar()
        main_layout.addWidget(status_frame)

        # Tabs for different sections
        tabs = QTabWidget()
        tabs.addTab(self._build_effects_tab(), "Effects")
        tabs.addTab(self._build_engine_tab(), "Engine Settings")
        tabs.addTab(self._build_hardware_tab(), "Hardware")
        main_layout.addWidget(tabs)

        # Bottom: Torque output visualization
        output_group = QGroupBox("Torque Output")
        output_layout = QVBoxLayout()
        self.torque_bar = TorqueBar()
        output_layout.addWidget(self.torque_bar)
        self.waveform = WaveformWidget()
        output_layout.addWidget(self.waveform)
        output_group.setLayout(output_layout)
        main_layout.addWidget(output_group)

    def _build_status_bar(self) -> QFrame:
        frame = QFrame()
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(5, 2, 5, 2)

        self.iracing_status = QLabel("iRacing: Disconnected")
        self.iracing_status.setStyleSheet("color: red; font-weight: bold;")
        layout.addWidget(self.iracing_status)

        layout.addStretch()

        self.hw_status = QLabel("Hardware: Disconnected")
        self.hw_status.setStyleSheet("color: red; font-weight: bold;")
        layout.addWidget(self.hw_status)

        layout.addStretch()

        self.loop_rate_label = QLabel("FFB Rate: 0 Hz")
        layout.addWidget(self.loop_rate_label)

        layout.addStretch()

        # Profile controls
        self.profile_combo = QComboBox()
        self._refresh_profiles()
        layout.addWidget(QLabel("Profile:"))
        layout.addWidget(self.profile_combo)

        load_btn = QPushButton("Load")
        load_btn.clicked.connect(self._on_load_profile)
        layout.addWidget(load_btn)

        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self._on_save_profile)
        layout.addWidget(save_btn)

        return frame

    def _build_effects_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        effect_names = {
            "self_aligning_torque": "Self-Aligning Torque (Primary)",
            "curb_rumble": "Curb / Road Rumble",
            "engine_vibration": "Engine Vibration",
            "tire_slip": "Tire Slip / Understeer",
            "collision_impact": "Collision Impact",
            "suspension": "Suspension Load Transfer",
        }

        self.effect_sliders: dict[str, EffectSliderGroup] = {}
        for name, display in effect_names.items():
            slider = EffectSliderGroup(name, display, self.engine)
            self.effect_sliders[name] = slider
            layout.addWidget(slider)

        layout.addStretch()
        return widget

    def _build_engine_tab(self) -> QWidget:
        widget = QWidget()
        layout = QGridLayout(widget)
        row = 0

        # Master gain
        layout.addWidget(QLabel("Master Gain:"), row, 0)
        self.master_gain_spin = QDoubleSpinBox()
        self.master_gain_spin.setRange(0.0, 2.0)
        self.master_gain_spin.setSingleStep(0.05)
        self.master_gain_spin.setValue(self.engine.config.master_gain)
        self.master_gain_spin.valueChanged.connect(
            lambda v: setattr(self.engine.config, 'master_gain', v))
        layout.addWidget(self.master_gain_spin, row, 1)
        row += 1

        # Max torque %
        layout.addWidget(QLabel("Max Torque %:"), row, 0)
        self.max_torque_spin = QDoubleSpinBox()
        self.max_torque_spin.setRange(1.0, 100.0)
        self.max_torque_spin.setSingleStep(5.0)
        self.max_torque_spin.setValue(self.engine.config.max_torque_pct)
        self.max_torque_spin.valueChanged.connect(
            lambda v: setattr(self.engine.config, 'max_torque_pct', v))
        layout.addWidget(self.max_torque_spin, row, 1)
        row += 1

        # Slew rate
        layout.addWidget(QLabel("Slew Rate Limit:"), row, 0)
        self.slew_spin = QDoubleSpinBox()
        self.slew_spin.setRange(0.01, 1.0)
        self.slew_spin.setSingleStep(0.05)
        self.slew_spin.setValue(self.engine.config.slew_rate_limit)
        self.slew_spin.valueChanged.connect(
            lambda v: setattr(self.engine.config, 'slew_rate_limit', v))
        layout.addWidget(self.slew_spin, row, 1)
        row += 1

        # Deadzone
        layout.addWidget(QLabel("Deadzone:"), row, 0)
        self.deadzone_spin = QDoubleSpinBox()
        self.deadzone_spin.setRange(0.0, 0.1)
        self.deadzone_spin.setSingleStep(0.005)
        self.deadzone_spin.setDecimals(3)
        self.deadzone_spin.setValue(self.engine.config.deadzone)
        self.deadzone_spin.valueChanged.connect(
            lambda v: setattr(self.engine.config, 'deadzone', v))
        layout.addWidget(self.deadzone_spin, row, 1)
        row += 1

        # Center spring
        layout.addWidget(QLabel("Center Spring:"), row, 0)
        self.center_spring_spin = QDoubleSpinBox()
        self.center_spring_spin.setRange(0.0, 1.0)
        self.center_spring_spin.setSingleStep(0.05)
        self.center_spring_spin.setValue(self.engine.config.center_spring_gain)
        self.center_spring_spin.valueChanged.connect(
            lambda v: setattr(self.engine.config, 'center_spring_gain', v))
        layout.addWidget(self.center_spring_spin, row, 1)
        row += 1

        # Center damping
        layout.addWidget(QLabel("Center Damping:"), row, 0)
        self.center_damp_spin = QDoubleSpinBox()
        self.center_damp_spin.setRange(0.0, 1.0)
        self.center_damp_spin.setSingleStep(0.05)
        self.center_damp_spin.setValue(self.engine.config.center_spring_damping)
        self.center_damp_spin.valueChanged.connect(
            lambda v: setattr(self.engine.config, 'center_spring_damping', v))
        layout.addWidget(self.center_damp_spin, row, 1)
        row += 1

        # Invert output
        self.invert_cb = QCheckBox("Invert Output Direction")
        self.invert_cb.setChecked(self.engine.config.invert_output)
        self.invert_cb.stateChanged.connect(
            lambda s: setattr(self.engine.config, 'invert_output', s == Qt.Checked))
        layout.addWidget(self.invert_cb, row, 0, 1, 2)
        row += 1

        layout.setRowStretch(row, 1)
        return widget

    def _build_hardware_tab(self) -> QWidget:
        widget = QWidget()
        layout = QGridLayout(widget)
        row = 0

        # COM port
        layout.addWidget(QLabel("Serial Port:"), row, 0)
        self.port_edit = QLineEdit(self.hardware.port)
        layout.addWidget(self.port_edit, row, 1)
        row += 1

        # Baud rate
        layout.addWidget(QLabel("Baud Rate:"), row, 0)
        self.baud_combo = QComboBox()
        self.baud_combo.addItems(["9600", "19200", "38400", "115200"])
        self.baud_combo.setCurrentText(str(self.hardware.baudrate))
        layout.addWidget(self.baud_combo, row, 1)
        row += 1

        # Slave address
        layout.addWidget(QLabel("Slave Address:"), row, 0)
        self.slave_spin = QSpinBox()
        self.slave_spin.setRange(1, 254)
        self.slave_spin.setValue(self.hardware.slave_address)
        layout.addWidget(self.slave_spin, row, 1)
        row += 1

        # Torque limit
        layout.addWidget(QLabel("Hardware Torque Limit %:"), row, 0)
        self.hw_torque_spin = QDoubleSpinBox()
        self.hw_torque_spin.setRange(1.0, 100.0)
        self.hw_torque_spin.setValue(self.hardware.torque_limit_pct)
        layout.addWidget(self.hw_torque_spin, row, 1)
        row += 1

        # Speed limit
        layout.addWidget(QLabel("Speed Limit (RPM):"), row, 0)
        self.speed_limit_spin = QSpinBox()
        self.speed_limit_spin.setRange(50, 2000)
        self.speed_limit_spin.setValue(self.hardware.speed_limit_rpm)
        layout.addWidget(self.speed_limit_spin, row, 1)
        row += 1

        # Connect / Enable buttons
        btn_layout = QHBoxLayout()
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self._on_connect)
        btn_layout.addWidget(self.connect_btn)

        self.enable_btn = QPushButton("Enable Servo")
        self.enable_btn.setEnabled(False)
        self.enable_btn.clicked.connect(self._on_enable)
        btn_layout.addWidget(self.enable_btn)

        self.disable_btn = QPushButton("DISABLE (E-Stop)")
        self.disable_btn.setStyleSheet("background-color: red; color: white; font-weight: bold;")
        self.disable_btn.clicked.connect(self._on_disable)
        btn_layout.addWidget(self.disable_btn)

        layout.addLayout(btn_layout, row, 0, 1, 2)
        row += 1

        # Hardware status display
        self.hw_info_label = QLabel("Not connected")
        self.hw_info_label.setStyleSheet("font-family: Consolas; font-size: 11px;")
        layout.addWidget(self.hw_info_label, row, 0, 1, 2)
        row += 1

        layout.setRowStretch(row, 1)
        return widget

    def _setup_timers(self):
        # UI update timer (~30Hz)
        self.ui_timer = QTimer()
        self.ui_timer.timeout.connect(self._update_ui)
        self.ui_timer.start(33)

        # Status update timer (~2Hz)
        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self._update_status)
        self.status_timer.start(500)

    def _update_ui(self):
        """Update torque display and effect outputs."""
        output = self.engine.output
        self.torque_bar.set_value(output)
        self.waveform.add_sample(output)

        effect_vals = self.engine.effect_outputs
        for name, slider in self.effect_sliders.items():
            slider.set_output_value(effect_vals.get(name, 0.0))

    def _update_status(self):
        """Update connection status indicators."""
        # iRacing
        if self.telemetry.connected:
            telem = self.telemetry.latest
            self.iracing_status.setText(
                f"iRacing: Connected | "
                f"Speed: {telem.speed * 3.6:.0f} km/h | "
                f"RPM: {telem.rpm:.0f}"
            )
            self.iracing_status.setStyleSheet("color: lime; font-weight: bold;")
        else:
            self.iracing_status.setText("iRacing: Waiting for connection...")
            self.iracing_status.setStyleSheet("color: orange; font-weight: bold;")

        # Hardware
        if self.hardware.is_connected:
            status = self.hardware.get_status()
            state = "Enabled" if status.enabled else "Connected (disabled)"
            self.hw_status.setText(f"Hardware: {state}")
            self.hw_status.setStyleSheet(
                "color: lime; font-weight: bold;" if status.enabled
                else "color: yellow; font-weight: bold;"
            )
            self.hw_info_label.setText(
                f"Motor RPM: {status.motor_rpm:.0f}  |  "
                f"Torque Cmd: {status.actual_torque_pct:.1f}%  |  "
                f"Encoder: {status.encoder_position}  |  "
                f"Error: {status.error_message}"
            )
        else:
            self.hw_status.setText("Hardware: Disconnected")
            self.hw_status.setStyleSheet("color: red; font-weight: bold;")

        # Loop rate
        self.loop_rate_label.setText(f"FFB Rate: {self.engine.loop_rate:.0f} Hz")

    def _on_connect(self):
        self.hardware.port = self.port_edit.text()
        self.hardware.baudrate = int(self.baud_combo.currentText())
        self.hardware.slave_address = self.slave_spin.value()
        self.hardware.torque_limit_pct = self.hw_torque_spin.value()
        self.hardware.speed_limit_rpm = self.speed_limit_spin.value()

        if self.hardware.is_connected:
            self.hardware.disconnect()
            self.connect_btn.setText("Connect")
            self.enable_btn.setEnabled(False)
        else:
            if self.hardware.connect():
                self.connect_btn.setText("Disconnect")
                self.enable_btn.setEnabled(True)
            else:
                QMessageBox.warning(self, "Connection Failed",
                                    f"Could not connect to {self.hardware.port}")

    def _on_enable(self):
        if self.hardware.enable():
            self.engine.set_hardware_callback(self.hardware.set_torque)
            self.enable_btn.setEnabled(False)
        else:
            QMessageBox.warning(self, "Enable Failed",
                                "Could not enable servo. Check hardware status.")

    def _on_disable(self):
        self.engine.set_hardware_callback(None)
        self.hardware.disable()
        self.enable_btn.setEnabled(self.hardware.is_connected)

    def _refresh_profiles(self):
        self.profile_combo.clear()
        for p in list_profiles():
            self.profile_combo.addItem(p.stem, str(p))

    def _on_load_profile(self):
        idx = self.profile_combo.currentIndex()
        if idx < 0:
            return
        path = self.profile_combo.itemData(idx)
        try:
            profile = load_profile(path)
            # Apply engine config
            eng = apply_engine_config(profile)
            self.engine.config.master_gain = eng.master_gain
            self.engine.config.max_torque_pct = eng.max_torque_pct
            self.engine.config.slew_rate_limit = eng.slew_rate_limit
            self.engine.config.deadzone = eng.deadzone
            self.engine.config.center_spring_gain = eng.center_spring_gain
            self.engine.config.center_spring_damping = eng.center_spring_damping
            self.engine.config.invert_output = eng.invert_output

            # Update engine tab spinboxes
            self.master_gain_spin.setValue(eng.master_gain)
            self.max_torque_spin.setValue(eng.max_torque_pct)
            self.slew_spin.setValue(eng.slew_rate_limit)
            self.deadzone_spin.setValue(eng.deadzone)
            self.center_spring_spin.setValue(eng.center_spring_gain)
            self.center_damp_spin.setValue(eng.center_spring_damping)
            self.invert_cb.setChecked(eng.invert_output)

            # Apply effect configs
            effects = apply_effect_configs(profile)
            for name, cfg in effects.items():
                if name in self.effect_sliders:
                    self.effect_sliders[name].load_config(cfg)
                if name in self.engine.effects:
                    self.engine.effects[name].config.gain = cfg.get("gain", 1.0)
                    self.engine.effects[name].config.enabled = cfg.get("enabled", True)
                    self.engine.effects[name].config.smoothing = cfg.get("smoothing", 0.0)
                    self.engine.effects[name].config.min_speed = cfg.get("min_speed", 0.0)

            # Apply hardware config
            hw = get_hardware_config(profile)
            self.port_edit.setText(hw.get("port", "COM3"))
            self.baud_combo.setCurrentText(str(hw.get("baudrate", 38400)))
            self.slave_spin.setValue(hw.get("slave_address", 1))
            self.hw_torque_spin.setValue(hw.get("torque_limit_pct", 50.0))
            self.speed_limit_spin.setValue(hw.get("speed_limit_rpm", 200))

        except Exception as e:
            QMessageBox.warning(self, "Load Error", str(e))

    def _on_save_profile(self):
        name, _ = QFileDialog.getSaveFileName(
            self, "Save Profile", str(Path(__file__).parent.parent.parent / "config"),
            "JSON Files (*.json)",
        )
        if not name:
            return

        hw_config = {
            "port": self.port_edit.text(),
            "baudrate": int(self.baud_combo.currentText()),
            "slave_address": self.slave_spin.value(),
            "torque_limit_pct": self.hw_torque_spin.value(),
            "speed_limit_rpm": self.speed_limit_spin.value(),
        }

        profile = build_profile_from_state(
            profile_name=Path(name).stem,
            engine_config=self.engine.config,
            hardware_config=hw_config,
            effects=self.engine.effects,
        )
        try:
            save_profile(profile, name)
            self._refresh_profiles()
        except Exception as e:
            QMessageBox.warning(self, "Save Error", str(e))

    def closeEvent(self, event):
        """Clean shutdown on window close."""
        self.engine.set_hardware_callback(None)
        self.engine.stop()
        if self.hardware.is_enabled:
            self.hardware.disable()
        if self.hardware.is_connected:
            self.hardware.disconnect()
        self.telemetry.stop()
        event.accept()
