"""
Entry point for the Dirt FFB consumer wheel plugin.

Wires together:
  - AppSettings (persisted user prefs)
  - IRacingTelemetry (shared memory reader)
  - DirectInputFFBDriver (consumer wheel output)
  - FFBEngine (telemetry → torque pipeline)
  - PluginMainWindow (PyQt5 GUI)

A global exception hook ensures crashes are shown as a dialog rather than
silently swallowing errors in the windowed exe.
"""

import sys
import traceback

from PyQt5.QtWidgets import QApplication, QDialog, QMessageBox

from src.settings import AppSettings
from src.gui.plugin_window import PluginMainWindow, SetupWizard
from src.telemetry.iracing_reader import IRacingTelemetry
from src.ffb.engine import FFBEngine
from src.hardware.directinput_driver import DirectInputFFBDriver
from src.wheel_detect import enumerate_ffb_wheels
from src.config import load_profile, apply_engine_config


def excepthook(exc_type, exc_value, exc_tb):
    """Show crash dialog instead of silently dying (important for windowed exe)."""
    tb = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    msg = QMessageBox()
    msg.setIcon(QMessageBox.Critical)
    msg.setWindowTitle("Dirt FFB Plugin — Error")
    msg.setText("An unexpected error occurred:")
    msg.setDetailedText(tb)
    msg.exec_()
    sys.exit(1)


sys.excepthook = excepthook


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Dirt FFB Plugin")
    app.setApplicationVersion("1.0.0")
    app.setQuitOnLastWindowClosed(False)  # keep alive in tray

    # Apply Fusion dark theme (same as main.py)
    app.setStyle("Fusion")
    from PyQt5.QtGui import QPalette, QColor
    palette = QPalette()
    palette.setColor(QPalette.Window,          QColor(45, 45, 45))
    palette.setColor(QPalette.WindowText,      QColor(220, 220, 220))
    palette.setColor(QPalette.Base,            QColor(30, 30, 30))
    palette.setColor(QPalette.AlternateBase,   QColor(40, 40, 40))
    palette.setColor(QPalette.ToolTipBase,     QColor(255, 255, 220))
    palette.setColor(QPalette.ToolTipText,     QColor(0, 0, 0))
    palette.setColor(QPalette.Text,            QColor(220, 220, 220))
    palette.setColor(QPalette.Button,          QColor(60, 60, 60))
    palette.setColor(QPalette.ButtonText,      QColor(220, 220, 220))
    palette.setColor(QPalette.BrightText,      QColor(255, 100, 100))
    palette.setColor(QPalette.Link,            QColor(42, 130, 218))
    palette.setColor(QPalette.Highlight,       QColor(42, 130, 218))
    palette.setColor(QPalette.HighlightedText, QColor(0, 0, 0))
    app.setPalette(palette)

    from PyQt5.QtGui import QFont
    app.setFont(QFont("Segoe UI", 10))

    settings = AppSettings()

    # First-run wizard
    if not settings.wizard_complete:
        wizard = SetupWizard(settings)
        if wizard.exec_() != QDialog.Accepted:
            sys.exit(0)

    # Validate that a wheel was selected
    if not settings.device_guid:
        wizard = SetupWizard(settings)
        if wizard.exec_() != QDialog.Accepted:
            sys.exit(0)

    # Build core components
    telemetry = IRacingTelemetry()
    hardware  = DirectInputFFBDriver(
        device_guid=settings.device_guid,
        max_force_pct=75.0,
    )
    engine = FFBEngine(telemetry)

    # Load last profile if available
    try:
        from src.resource_path import resource_path
        profile_path = resource_path(f"config/{settings.last_profile}.json")
        if profile_path.exists():
            profile = load_profile(profile_path)
            eng_cfg = apply_engine_config(profile)
            engine.config.master_gain          = eng_cfg.master_gain
            engine.config.max_torque_pct       = eng_cfg.max_torque_pct
            engine.config.slew_rate_limit      = eng_cfg.slew_rate_limit
            engine.config.deadzone             = eng_cfg.deadzone
            engine.config.center_spring_gain   = eng_cfg.center_spring_gain
            engine.config.center_spring_damping = eng_cfg.center_spring_damping
    except Exception:
        pass

    # Start telemetry and FFB engine
    telemetry.start()

    # Connect hardware and wire engine → hardware callback
    if hardware.connect():
        if hardware.enable():
            engine.set_hardware_callback(hardware.set_torque)
        else:
            # Non-fatal — user can retry from GUI
            pass

    engine.start()

    window = PluginMainWindow(telemetry, engine, hardware, settings)
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
