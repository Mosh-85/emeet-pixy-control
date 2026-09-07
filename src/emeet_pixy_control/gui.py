#!/usr/bin/env python3

import argparse
import os
import sys
import subprocess
import shutil
from pathlib import Path

from emeet_pixy_control import __version__

from PySide6.QtCore import QEvent, QLockFile, Qt, QSettings, QTimer, QProcess
from PySide6.QtGui import QAction, QIcon
from PySide6.QtMultimedia import QCamera, QMediaCaptureSession, QMediaDevices
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QApplication, QComboBox, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QMainWindow, QPushButton, QSlider, QVBoxLayout, QWidget,
    QScrollArea, QSplitter, QFrame, QMenu, QMessageBox, QSystemTrayIcon
)

PACKAGE_DIR = Path(__file__).resolve().parent
BACKEND = PACKAGE_DIR / "backend.py"
ICON = PACKAGE_DIR / "assets" / "emeet-pixy-control.svg"
FFMPEG = shutil.which("ffmpeg")
V4L2_CTL = shutil.which("v4l2-ctl")

APP_STYLESHEET = """
QWidget {
    background: #111820;
    color: #e8f0ef;
    font-size: 10pt;
}

QMainWindow {
    background: #111820;
}

QFrame#statusPanel {
    background: #17252c;
    border: 1px solid #2c535b;
    border-radius: 6px;
    padding: 3px 8px;
}

QGroupBox {
    background: #1a2730;
    border: 1px solid #334954;
    border-radius: 8px;
    margin-top: 14px;
    padding: 18px 10px 10px;
    font-weight: 600;
}

QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 5px;
    color: #8ce7dd;
}

QLabel#appTitle {
    color: #f4fbfa;
    letter-spacing: 1px;
}

QLabel#message {
    background: #17252c;
    border: 1px solid #2c535b;
    border-radius: 6px;
    color: #9debe3;
    padding: 8px;
}

QLabel#modeLabel {
    color: #f2b66d;
}

QLabel#statusLabel, QLabel#previewStatus, QLabel#virtualStatus {
    color: #a9babd;
}

QPushButton {
    background: #263842;
    border: 1px solid #405761;
    border-radius: 6px;
    padding: 7px 12px;
    min-height: 18px;
}

QPushButton:hover {
    background: #304b54;
    border-color: #62d8ce;
}

QPushButton:pressed {
    background: #1b676d;
}

QPushButton:disabled {
    background: #1a252b;
    border-color: #2b373d;
    color: #68777a;
}

QPushButton#primaryButton {
    background: #167c7b;
    border-color: #63dfd4;
    color: #f4fffd;
    font-weight: 600;
}

QPushButton#dangerButton {
    background: #62373b;
    border-color: #a95d61;
}

QPushButton#privacyButton {
    background: #263842;
    border-color: #405761;
}

QPushButton#privacyButton:checked {
    background: #5b3e2c;
    border-color: #d19a5b;
}

QComboBox, QSlider {
    background: #202f38;
}

QComboBox {
    border: 1px solid #405761;
    border-radius: 5px;
    padding: 6px 8px;
    min-height: 20px;
}

QComboBox:hover {
    border-color: #62d8ce;
}

QComboBox QAbstractItemView {
    background: #1a2730;
    border: 1px solid #405761;
    selection-background-color: #167c7b;
    selection-color: #f4fffd;
}

QSlider::groove:horizontal {
    height: 5px;
    background: #344852;
    border-radius: 2px;
}

QSlider::handle:horizontal {
    width: 16px;
    margin: -6px 0;
    background: #62d8ce;
    border: 2px solid #b6fff8;
    border-radius: 8px;
}

QScrollArea, QScrollArea > QWidget > QWidget {
    background: #111820;
}

QScrollBar:vertical {
    background: #111820;
    width: 10px;
    margin: 2px;
}

QScrollBar::handle:vertical {
    background: #405761;
    border-radius: 5px;
    min-height: 24px;
}

QVideoWidget {
    background: #080d10;
    border: 1px solid #334954;
    border-radius: 6px;
}
"""


def normalize_choice(value, valid_values, default):
    if value in valid_values:
        return value

    if isinstance(value, str):
        normalized = value.strip().lower()
        for choice in valid_values:
            if str(choice).lower() == normalized:
                return choice

    return default


def resolve_startup_tracking(saved_tracking, default_tracking, privacy_default):
    if privacy_default:
        return "privacy"

    tracking = normalize_choice(
        saved_tracking,
        ("idle", "track", "privacy"),
        "idle",
    )

    if tracking == "privacy":
        return normalize_choice(
            default_tracking,
            ("idle", "track"),
            "idle",
        )

    return tracking


def camera_toggle_tracking_mode(
    enabled,
    saved_tracking="idle",
    default_tracking="idle",
):
    if not enabled:
        return "privacy"

    tracking = normalize_choice(
        saved_tracking,
        ("idle", "track", "privacy"),
        "idle",
    )

    if tracking == "privacy":
        return normalize_choice(
            default_tracking,
            ("idle", "track"),
            "idle",
        )

    return tracking


def should_autostart_virtual_camera(background_mode, configured):
    return configured


def background_service_is_active():
    result = subprocess.run(
        ["systemctl", "--user", "is-active", "--quiet", "emeet-pixy-control.service"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


class PixyGUI(QMainWindow):
    def __init__(self, start_minimized=False):
        super().__init__()
        self.start_minimized = start_minimized
        self.quitting = False
        self.tray = None
        self.panel_toggle_action = None
        self.privacy_toggle_action = None
        self.tracking_on_action = None
        self.tracking_off_action = None

        self.settings = QSettings("emeet-pixy", "control")
        self.camera = None
        self.capture_session = None
        self.camera_enabled = True
        self.restoring = False
        self.preview_released = False

        self.virtual_process = None
        self.virtual_external_pid = None
        self.virtual_active = False
        self.virtual_stopping = False
        self.virtual_starting = False
        self.virtual_lock = QLockFile(
            str(Path("/tmp/emeet-pixy-control-virtual-camera.lock"))
        )
        self.virtual_lock_held = False
        self.preview_before_virtual = True
        self.camera_before_virtual = True
        self.camera_override_tracking = None
        self.preview_compact = False
        self.full_window_size = None

        # Resolution selector data:
        # { "3840x2160": QCameraFormat, ... }
        self.camera_formats = {}

        self.launch_mode = (
            "Background autostart"
            if start_minimized
            else "Preview test"
        )

        self.setWindowTitle(f"EMEET PIXY Control v{__version__}")
        if ICON.exists():
            self.setWindowIcon(QIcon(str(ICON)))
        self.resize(1100, 700)

        screen = QApplication.primaryScreen()
        available = screen.availableGeometry() if screen else None

        saved_geometry = self.settings.value("window/geometry")
        if saved_geometry is not None:
            self.restoreGeometry(saved_geometry)

        if available is not None:
            self.resize(
                min(self.width(), available.width() - 40),
                min(self.height(), available.height() - 40),
            )

        root = QWidget()
        self.setCentralWidget(root)
        main = QVBoxLayout(root)

        # Header
        header = QHBoxLayout()

        title = QLabel("EMEET PIXY")
        title.setObjectName("appTitle")
        font = title.font()
        font.setPointSize(18)
        font.setBold(True)
        title.setFont(font)

        self.device_label = QLabel("Detecting camera...")
        self.device_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.version_label = QLabel(f"v{__version__}")
        self.version_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.version_label.setToolTip("Application version")

        self.mode_label = QLabel(f"Mode: {self.launch_mode}")
        self.mode_label.setObjectName("modeLabel")
        self.mode_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.mode_label.setStyleSheet("QLabel { font-weight: 600; }")

        self.preview_restore_button = QPushButton("Resume Preview")
        self.preview_restore_button.setObjectName("primaryButton")
        self.preview_restore_button.clicked.connect(self.toggle_preview)
        self.preview_restore_button.hide()

        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.preview_restore_button)
        main.addLayout(header)

        status_panel = QFrame()
        status_panel.setObjectName("statusPanel")
        status_layout = QHBoxLayout(status_panel)
        status_layout.setContentsMargins(8, 3, 8, 3)
        status_layout.addWidget(self.mode_label)
        status_layout.addStretch()
        status_layout.addWidget(self.device_label)
        status_layout.addSpacing(12)
        status_layout.addWidget(self.version_label)
        main.addWidget(status_panel)

        self.message = QLabel(
            f"Ready — {self.launch_mode} mode"
        )
        self.message.setObjectName("message")
        self.message.setAlignment(Qt.AlignCenter)
        main.addWidget(self.message)

        self.body_splitter = QSplitter(Qt.Horizontal)
        self.body_splitter.setChildrenCollapsible(False)

        # Live preview
        preview_box = QGroupBox("Live Preview")
        self.preview_box = preview_box
        preview_layout = QVBoxLayout(preview_box)

        self.video_widget = QVideoWidget()
        self.video_widget.setMinimumSize(320, 240)
        self.video_widget.setAspectRatioMode(Qt.KeepAspectRatio)
        preview_layout.addWidget(self.video_widget)

        self.released_panel = QLabel(
            "<b>CAMERA PREVIEW RELEASED</b><br><br>"
            "EMEET PIXY is available to Google Meet, OBS, Teams, "
            "and other applications.<br><br>"
            "Camera controls remain active."
        )
        self.released_panel.setAlignment(Qt.AlignCenter)
        self.released_panel.setWordWrap(True)
        self.released_panel.setMinimumSize(320, 240)
        self.released_panel.setStyleSheet(
            "QLabel {"
            "  border: 1px solid palette(mid);"
            "  padding: 40px;"
            "  font-size: 14px;"
            "}"
        )
        self.released_panel.hide()

        preview_layout.addWidget(self.released_panel)

        self.preview_status = QLabel("Starting camera...")
        self.preview_status.setObjectName("previewStatus")
        self.preview_status.setAlignment(Qt.AlignCenter)
        preview_layout.addWidget(self.preview_status)

        self.preview_button = QPushButton("Release Preview")
        self.preview_button.clicked.connect(self.toggle_preview)
        preview_layout.addWidget(self.preview_button)

        self.body_splitter.addWidget(preview_box)
        self.body_splitter.setStretchFactor(0, 3)

        # Controls
        controls_widget = QWidget()
        controls = QVBoxLayout(controls_widget)
        controls.setContentsMargins(0, 0, 0, 0)

        # Pan / Tilt
        ptz_box = QGroupBox("Pan / Tilt")
        ptz = QGridLayout(ptz_box)

        up = self.make_move_button("▲", "up")
        down = self.make_move_button("▼", "down")
        left = self.make_move_button("◀", "left")
        right = self.make_move_button("▶", "right")

        center = QPushButton("● Center")
        center.setMinimumHeight(55)
        center.clicked.connect(self.center_camera)

        ptz.addWidget(up, 0, 1)
        ptz.addWidget(left, 1, 0)
        ptz.addWidget(center, 1, 1)
        ptz.addWidget(right, 1, 2)
        ptz.addWidget(down, 2, 1)

        controls.addWidget(ptz_box)

        # Resolution
        resolution_box = QGroupBox("Resolution")
        resolution_layout = QVBoxLayout(resolution_box)

        self.resolution = QComboBox()
        self.resolution.currentIndexChanged.connect(
            self.resolution_changed
        )

        self.resolution_info = QLabel("")
        self.resolution_info.setAlignment(Qt.AlignCenter)

        resolution_layout.addWidget(self.resolution)
        resolution_layout.addWidget(self.resolution_info)

        controls.addWidget(resolution_box)

        # Zoom
        zoom_box = QGroupBox("Zoom")
        zoom_layout = QVBoxLayout(zoom_box)

        self.zoom_label = QLabel("100")
        self.zoom_label.setAlignment(Qt.AlignCenter)

        self.zoom = QSlider(Qt.Horizontal)
        self.zoom.setRange(100, 150)
        self.zoom.setValue(100)
        self.zoom.setTickInterval(5)
        self.zoom.setTickPosition(QSlider.TicksBelow)

        self.zoom.valueChanged.connect(
            lambda value: self.zoom_label.setText(str(value))
        )
        self.zoom.sliderReleased.connect(self.apply_zoom)

        zoom_layout.addWidget(self.zoom_label)
        zoom_layout.addWidget(self.zoom)
        controls.addWidget(zoom_box)

        # Tracking / Privacy
        tracking_box = QGroupBox("Face Tracking")
        tracking = QGridLayout(tracking_box)

        tracking_on = QPushButton("Tracking ON")
        tracking_off = QPushButton("Tracking OFF")
        self.tracking_on_button = tracking_on
        self.tracking_off_button = tracking_off
        privacy = QPushButton("Camera: Off")
        privacy.setObjectName("privacyButton")
        privacy.setCheckable(True)
        self.privacy_button = privacy

        tracking_on.clicked.connect(lambda: self.set_tracking_mode("track"))
        tracking_off.clicked.connect(lambda: self.set_tracking_mode("idle"))
        privacy.clicked.connect(self.toggle_privacy)

        tracking.addWidget(tracking_on, 0, 0)
        tracking.addWidget(tracking_off, 0, 1)
        tracking.addWidget(privacy, 1, 0, 1, 2)

        controls.addWidget(tracking_box)

        # Gesture
        gesture_box = QGroupBox("Gesture Control")
        gesture = QHBoxLayout(gesture_box)

        gesture_on = QPushButton("ON")
        gesture_off = QPushButton("OFF")

        gesture_on.clicked.connect(lambda: self.set_gesture(True))
        gesture_off.clicked.connect(lambda: self.set_gesture(False))

        gesture.addWidget(gesture_on)
        gesture.addWidget(gesture_off)

        controls.addWidget(gesture_box)

        # Audio
        audio_box = QGroupBox("Audio Mode")
        audio_layout = QHBoxLayout(audio_box)

        self.audio = QComboBox()
        self.audio.addItem("Noise Cancel", "nc")
        self.audio.addItem("Live", "live")
        self.audio.addItem("Original", "org")

        audio_apply = QPushButton("Apply")
        audio_apply.clicked.connect(self.apply_audio)

        audio_layout.addWidget(self.audio)
        audio_layout.addWidget(audio_apply)
        controls.addWidget(audio_box)

        # Anti-flicker
        flicker_box = QGroupBox("Anti-flicker")
        flicker_layout = QHBoxLayout(flicker_box)

        self.flicker = QComboBox()
        self.flicker.addItem("60 Hz", "60")
        self.flicker.addItem("50 Hz", "50")
        self.flicker.addItem("Off", "off")

        flicker_apply = QPushButton("Apply")
        flicker_apply.clicked.connect(self.apply_flicker)

        flicker_layout.addWidget(self.flicker)
        flicker_layout.addWidget(flicker_apply)

        controls.addWidget(flicker_box)

        # Virtual Camera
        virtual_box = QGroupBox("Virtual Camera")
        virtual_layout = QGridLayout(virtual_box)

        self.virtual_device_status = QLabel("Device: checking...")
        self.virtual_device_status.setAlignment(Qt.AlignCenter)

        self.virtual_status = QLabel("Pipeline: Stopped")
        self.virtual_status.setObjectName("virtualStatus")
        self.virtual_status.setAlignment(Qt.AlignCenter)

        self.virtual_start = QPushButton("Start")
        self.virtual_stop = QPushButton("Stop")
        self.virtual_start.setObjectName("primaryButton")
        self.virtual_stop.setObjectName("dangerButton")

        self.virtual_start.clicked.connect(
            self.start_virtual_camera
        )

        self.virtual_stop.clicked.connect(
            lambda: self.stop_virtual_camera(True)
        )

        self.virtual_stop.setEnabled(False)

        virtual_layout.addWidget(
            self.virtual_device_status, 0, 0, 1, 2
        )
        virtual_layout.addWidget(
            self.virtual_status, 1, 0, 1, 2
        )
        virtual_layout.addWidget(
            self.virtual_start, 2, 0
        )
        virtual_layout.addWidget(
            self.virtual_stop, 2, 1
        )

        controls.addWidget(virtual_box)

        # Status
        status_box = QGroupBox("Camera Status")
        status_layout = QVBoxLayout(status_box)

        self.status = QLabel("Checking...")
        self.status.setObjectName("statusLabel")
        self.status.setAlignment(Qt.AlignCenter)

        refresh = QPushButton("Refresh Status")
        refresh.clicked.connect(self.refresh_status)

        status_layout.addWidget(self.status)
        status_layout.addWidget(refresh)

        controls.addWidget(status_box)
        controls.addStretch()

        controls_scroll = QScrollArea()
        controls_scroll.setWidgetResizable(True)
        controls_scroll.setFrameShape(QFrame.NoFrame)
        controls_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarAlwaysOff
        )
        controls_scroll.setWidget(controls_widget)

        self.body_splitter.addWidget(controls_scroll)
        self.body_splitter.setStretchFactor(1, 2)
        self.body_splitter.setSizes([700, 430])

        main.addWidget(self.body_splitter, 1)

        self.protect_setting_controls_from_wheel()
        self.setup_tray()

        self.load_saved_ui_state()
        device = self.find_pixy_camera()
        if device is not None and not self.camera_formats:
            self.populate_resolutions(device)

        if self.start_minimized:
            self.camera_enabled = False

        privacy_startup = self.bool_setting(
            "camera/privacy_enabled",
            True,
        )

        if privacy_startup:
            self.backend("privacy")
            self.video_widget.hide()
            self.released_panel.setText(
                "<b>PRIVACY MODE ACTIVE</b><br><br>"
                "The camera stream is hidden until privacy is turned off."
            )
            self.released_panel.show()
            self.preview_button.setEnabled(False)
            self.preview_status.setText("Privacy active — preview hidden")
        elif self.camera_enabled:
            self.start_camera()
        else:
            self.set_camera_enabled(False)
        self.refresh_status(save=False)

        self.refresh_virtual_device_status()

        self.virtual_device_timer = QTimer(self)
        self.virtual_device_timer.timeout.connect(
            self.refresh_virtual_device_status
        )
        self.virtual_device_timer.start(3000)

        if should_autostart_virtual_camera(
            self.start_minimized,
            self.bool_setting("virtual/autostart", True),
        ):
            QTimer.singleShot(
                1500,
                self.start_background_virtual_camera,
            )

        # Allow USB/video startup to settle before restoring saved settings.
        QTimer.singleShot(800, self.restore_camera_state)

        if self.start_minimized:
            self.showMinimized()

    # --------------------------------------------------
    # CAMERA PREVIEW
    # --------------------------------------------------

    def protect_setting_controls_from_wheel(self):
        for control in self.findChildren(QSlider):
            control.installEventFilter(self)
        for control in self.findChildren(QComboBox):
            control.installEventFilter(self)

    def eventFilter(self, watched, event):
        if (
            event.type() == QEvent.Type.Wheel
            and isinstance(watched, (QSlider, QComboBox))
        ):
            return True
        return super().eventFilter(watched, event)

    def setup_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return

        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(QIcon(str(ICON)))
        self.tray.setToolTip("EMEET PIXY Control")

        menu = QMenu()

        self.panel_toggle_action = QAction("Show Control Panel", self)
        self.panel_toggle_action.triggered.connect(
            self.toggle_control_panel
        )
        menu.addAction(self.panel_toggle_action)
        menu.addSeparator()

        privacy_enabled = self.bool_setting("camera/privacy_enabled", False)
        self.privacy_toggle_action = QAction(self)
        self.privacy_toggle_action.setCheckable(True)
        self.privacy_toggle_action.setChecked(privacy_enabled)
        self.update_privacy_toggle_action(privacy_enabled)
        self.privacy_toggle_action.triggered.connect(
            self.toggle_privacy
        )
        menu.addAction(self.privacy_toggle_action)

        self.tracking_on_action = QAction("Tracking On", self)
        self.tracking_on_action.triggered.connect(
            lambda: self.set_tracking_mode("track")
        )
        menu.addAction(self.tracking_on_action)

        self.tracking_off_action = QAction("Tracking Off", self)
        self.tracking_off_action.triggered.connect(
            lambda: self.set_tracking_mode("idle")
        )
        menu.addAction(self.tracking_off_action)

        quit_action = QAction("Quit EMEET PIXY Control", self)
        quit_action.triggered.connect(self.quit_application)
        menu.addAction(quit_action)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.tray_activated)
        self.tray.show()

    def show_control_panel(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()
        if self.panel_toggle_action is not None:
            self.panel_toggle_action.setText("Hide Control Panel")

    def toggle_control_panel(self):
        if self.isVisible():
            self.hide()
            if self.panel_toggle_action is not None:
                self.panel_toggle_action.setText("Show Control Panel")
        else:
            self.show_control_panel()

    def tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_privacy(
                not self.bool_setting("camera/privacy_enabled", False)
            )

    def quit_application(self):
        self.quitting = True
        self.close()

    def set_camera_enabled(self, enabled):
        if enabled:
            mode = camera_toggle_tracking_mode(
                True,
                self.settings.value("camera/tracking", "idle"),
                self.settings.value("camera/tracking", "idle"),
            )
            if self.backend(mode) is None:
                return

            self.camera_enabled = True
            self.camera_override_tracking = mode
            self.video_widget.show()
            self.released_panel.hide()
            self.preview_button.setEnabled(True)
            self.preview_status.setText("Starting live preview...")
            self.start_camera()
            QTimer.singleShot(500, self.restore_camera_state)
        else:
            if self.virtual_active or self.virtual_process is not None:
                self.message.setText(
                    "Stop the virtual camera before disabling preview"
                )
                return

            self.camera_enabled = False
            self.camera_override_tracking = camera_toggle_tracking_mode(False)
            self.backend(self.camera_override_tracking)
            self.stop_camera()
            self.video_widget.hide()
            self.released_panel.setText(
                "<b>CAMERA PREVIEW DISABLED</b><br><br>"
                "Turn the camera on to view the live stream."
            )
            self.released_panel.show()
            self.preview_button.setEnabled(False)
            self.preview_status.setText("Preview disabled")
            self.message.setText("Camera preview disabled")

        self.settings.setValue("camera/enabled", self.camera_enabled)
        self.settings.sync()

    def find_pixy_camera(self):
        for device in QMediaDevices.videoInputs():
            name = device.description().upper()
            if "EMEET" in name and "PIXY" in name:
                return device
        return None

    def populate_resolutions(self, device):
        """
        Populate one entry per physical resolution.

        The PIXY exposes MJPEG plus low-resolution YUYV duplicates.
        For duplicate resolutions we prefer the format with the larger
        frame-rate capability, which on this camera selects MJPEG.
        """

        formats = {}

        for fmt in device.videoFormats():
            size = fmt.resolution()
            key = f"{size.width()}x{size.height()}"

            existing = formats.get(key)

            if (
                existing is None
                or fmt.maxFrameRate() > existing.maxFrameRate()
            ):
                formats[key] = fmt

        ordered = sorted(
            formats.items(),
            key=lambda item: (
                item[1].resolution().width()
                * item[1].resolution().height()
            ),
            reverse=True,
        )

        self.camera_formats = dict(ordered)

        saved = str(
            self.settings.value(
                "camera/resolution",
                "3840x2160"
            )
        )

        self.resolution.blockSignals(True)
        self.resolution.clear()

        saved_index = 0

        for index, (key, fmt) in enumerate(ordered):
            size = fmt.resolution()

            label = (
                f"{size.width()} × {size.height()}"
            )

            self.resolution.addItem(label, key)

            if key == saved:
                saved_index = index

        self.resolution.setCurrentIndex(saved_index)
        self.resolution.blockSignals(False)

        self.update_resolution_info()

    def selected_camera_format(self):
        key = self.resolution.currentData()

        if key is None:
            return None

        return self.camera_formats.get(str(key))

    def update_resolution_info(self):
        fmt = self.selected_camera_format()

        if fmt is None:
            self.resolution_info.setText("")
            return

        minimum = round(fmt.minFrameRate())
        maximum = round(fmt.maxFrameRate())

        if minimum == maximum:
            fps_text = f"{maximum} fps"
        else:
            fps_text = f"{minimum}-{maximum} fps"

        self.resolution_info.setText(fps_text)

        key = str(self.resolution.currentData())

        # PIXY native zoom is unavailable at 4K.
        # Qt's 1080p format is 30-60 fps and Qt attempts the
        # highest supported frame rate, effectively 1080p60.
        try:
            width = int(key.split("x", 1)[0])
        except (ValueError, IndexError):
            width = 9999

        zoom_available = width < 1920

        self.zoom.setEnabled(zoom_available)

        if zoom_available:
            self.zoom.setToolTip(
                "EMEET PIXY hardware zoom"
            )
        else:
            self.zoom.setToolTip(
                "Zoom unavailable in this video mode"
            )

    def resolution_changed(self):
        if not self.camera_formats:
            return

        key = self.resolution.currentData()

        if key is None:
            return

        self.settings.setValue(
            "camera/resolution",
            str(key)
        )

        self.settings.sync()

        self.update_resolution_info()

        if self.virtual_active:
            self.stop_virtual_camera(False)
            self.start_virtual_camera()
            return

        # Re-open the camera with the newly selected format,
        # unless another application currently owns the stream.
        if not self.preview_released and self.camera_enabled:
            self.stop_camera()
            self.start_camera()

            QTimer.singleShot(
                500,
                self.restore_camera_state
            )

    def start_camera(self):
        if self.virtual_active or self.virtual_process is not None:
            return

        device = self.find_pixy_camera()

        if device is None:
            self.preview_status.setText("EMEET PIXY not found")
            self.device_label.setText("Camera unavailable")
            return

        self.device_label.setText(device.description())

        if not self.camera_formats:
            self.populate_resolutions(device)

        self.capture_session = QMediaCaptureSession(self)
        self.camera = QCamera(device, self)

        selected_format = self.selected_camera_format()

        if selected_format is not None:
            self.camera.setCameraFormat(selected_format)

        self.capture_session.setCamera(self.camera)
        self.capture_session.setVideoOutput(self.video_widget)

        self.camera.errorOccurred.connect(self.camera_error)
        self.camera.activeChanged.connect(self.camera_active_changed)

        self.preview_status.setText("Starting live preview...")
        self.camera.start()

    def stop_camera(self):
        if self.camera is not None:
            self.camera.stop()
            self.camera.deleteLater()
            self.camera = None

        if self.capture_session is not None:
            self.capture_session.deleteLater()
            self.capture_session = None

    def toggle_preview(self):
        if not self.preview_released:
            # Release the physical video stream for Meet/OBS/etc.
            self.stop_camera()
            self.preview_released = True

            self.video_widget.hide()

            self.released_panel.setText(
                "<b>CAMERA PREVIEW RELEASED</b><br><br>"
                "EMEET PIXY is available to Google Meet, OBS, Teams, "
                "and other applications.<br><br>"
                "Camera controls remain active."
            )

            self.released_panel.show()

            self.preview_status.setText(
                "Controls active — video stream released"
            )

            self.preview_button.setText("Resume Preview")

            # External application now owns the video mode.
            self.resolution.setEnabled(False)
            self.zoom.setEnabled(False)

            self.resolution.setToolTip(
                "Video mode is controlled by the application using the camera"
            )

            self.zoom.setToolTip(
                "Zoom availability depends on the external application's "
                "selected video mode"
            )

            self.message.setText(
                "Camera available to other applications"
            )

            self.compact_preview_layout()

        else:
            self.preview_released = False

            self.restore_preview_layout()

            self.released_panel.hide()
            self.video_widget.show()

            self.preview_button.setText("Release Preview")

            self.resolution.setEnabled(True)
            self.resolution.setToolTip("")

            self.preview_status.setText(
                "Starting live preview..."
            )

            self.start_camera()
            self.update_resolution_info()

            QTimer.singleShot(
                500,
                self.restore_camera_state
            )

            self.message.setText(
                "Preview resumed"
            )

    def compact_preview_layout(self):
        if self.preview_compact:
            return

        self.preview_compact = True
        self.full_window_size = self.size()
        self.preview_box.hide()
        self.preview_restore_button.show()
        self.resize(min(self.width(), 650), self.height())

    def restore_preview_layout(self):
        if not self.preview_compact:
            return

        self.preview_compact = False
        self.preview_box.show()
        self.preview_restore_button.hide()
        self.body_splitter.setSizes([700, 430])

        if self.full_window_size is not None:
            self.resize(self.full_window_size)
            self.full_window_size = None

    def restart_camera(self):
        self.preview_status.setText("Restarting preview...")
        self.stop_camera()
        self.start_camera()
        QTimer.singleShot(500, self.restore_camera_state)

    def camera_active_changed(self, active):
        self.preview_status.setText(
            "Live" if active else "Preview stopped"
        )

    def camera_error(self, error, error_string):
        self.preview_status.setText(
            f"Camera error: {error_string}"
        )

    # --------------------------------------------------
    # VIRTUAL CAMERA
    # --------------------------------------------------

    def start_background_virtual_camera(self):
        if not should_autostart_virtual_camera(
            self.start_minimized,
            self.bool_setting("virtual/autostart", True),
        ):
            return

        if self.virtual_active or self.virtual_process is not None:
            return

        self.start_virtual_camera()

        if not self.virtual_active:
            QTimer.singleShot(
                3000,
                self.start_background_virtual_camera,
            )

    def find_virtual_camera_device(self):
        """
        Locate the EMEET PIXY virtual camera by its V4L2 device name
        instead of assuming a fixed /dev/video number.
        """

        root = Path("/sys/class/video4linux")

        if not root.exists():
            return None

        devices = sorted(
            root.glob("video*"),
            key=lambda x: int(x.name.replace("video", ""))
        )

        for dev_dir in devices:
            try:
                name = (
                    dev_dir / "name"
                ).read_text().strip()
            except Exception:
                continue

            if name == "EMEET PIXY Virtual Camera":
                return f"/dev/{dev_dir.name}"

        return None


    def refresh_virtual_device_status(self):
        device = self.find_virtual_camera_device()

        if device:
            self.virtual_device_status.setText(
                f"Device: Ready — {device}"
            )
            external_pid = self.find_virtual_pipeline_process(device)
            if external_pid and self.virtual_process is None:
                if not self.virtual_active:
                    self.preview_before_virtual = not self.preview_released
                    self.camera_before_virtual = self.camera_enabled
                    self.stop_camera()
                    self.compact_preview_layout()
                    self.preview_restore_button.setEnabled(False)
                    self.preview_button.setEnabled(False)
                    self.preview_status.setText(
                        "Virtual camera active — preview unavailable"
                    )
                    self.virtual_stop.setEnabled(True)
                self.virtual_external_pid = external_pid
                self.virtual_active = True
                self.virtual_status.setText(
                    "Pipeline: Running — existing FFmpeg process"
                )
            elif (
                self.virtual_process is None
                and self.virtual_external_pid is not None
            ):
                self.virtual_external_pid = None
                self.virtual_active = False
                self.virtual_status.setText("Pipeline: Stopped")
                self.restore_after_virtual_camera()
            self.virtual_start.setEnabled(
                not self.virtual_active and self.virtual_process is None
            )
        else:
            self.virtual_device_status.setText(
                "Device: Missing"
            )
            self.virtual_start.setEnabled(False)

        self.update_resolution_control_state()

    def find_virtual_pipeline_process(self, virtual_device):
        physical_device = self.find_pixy_video_device()
        if physical_device is None:
            return None

        result = subprocess.run(
            ["pgrep", "-af", "ffmpeg"],
            capture_output=True,
            text=True,
            check=False,
        )
        for line in result.stdout.splitlines():
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            if physical_device in parts[1] and virtual_device in parts[1]:
                try:
                    return int(parts[0])
                except ValueError:
                    continue
        return None

    def update_resolution_control_state(self):
        if self.virtual_active:
            self.resolution.setEnabled(True)
            self.resolution.setToolTip(
                "Changing resolution restarts the virtual-camera pipeline"
            )
            return

        if self.virtual_starting or self.preview_released:
            self.resolution.setEnabled(False)
            return

        self.resolution.setEnabled(True)
        self.resolution.setToolTip("")


    def find_pixy_video_device(self):
        """
        Find the PIXY capture node dynamically instead of assuming
        that it will always be /dev/video1.
        """

        root = Path("/sys/class/video4linux")

        if not root.exists():
            return None

        devices = sorted(
            root.glob("video*"),
            key=lambda x: int(x.name.replace("video", ""))
        )

        for dev_dir in devices:
            try:
                name = (
                    dev_dir / "name"
                ).read_text().strip().upper()
            except Exception:
                continue

            if "EMEET" not in name or "PIXY" not in name:
                continue

            device = f"/dev/{dev_dir.name}"

            # PIXY exposes more than one video node.
            # Select the node that actually exposes MJPEG capture.
            try:
                result = subprocess.run(
                    [
                        V4L2_CTL or "v4l2-ctl",
                        "-d",
                        device,
                        "--list-formats-ext",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=2,
                )

                if "MJPG" in result.stdout:
                    return device

            except Exception:
                continue

        return None


    def start_virtual_camera(self):
        if self.virtual_active or self.virtual_starting:
            return

        self.virtual_starting = True
        self.resolution.setEnabled(True)
        self.resolution.setToolTip(
            "Changing resolution restarts the virtual-camera pipeline"
        )

        if not self.virtual_lock.tryLock(0):
            self.virtual_starting = False
            self.message.setText(
                "Virtual camera is already running in another app window"
            )
            self.virtual_status.setText(
                "Pipeline: Already running"
            )
            return

        self.virtual_lock_held = True

        virtual_device = self.find_virtual_camera_device()

        if virtual_device is None:
            self.virtual_starting = False
            self.release_virtual_lock()
            self.message.setText(
                "EMEET PIXY Virtual Camera is not available"
            )
            self.virtual_status.setText(
                "Pipeline: Device missing"
            )
            self.refresh_virtual_device_status()
            return

        physical_device = self.find_pixy_video_device()

        if physical_device is None:
            self.virtual_starting = False
            self.release_virtual_lock()
            self.message.setText(
                "EMEET PIXY capture device not found"
            )
            self.virtual_status.setText(
                "PIXY unavailable"
            )
            return

        resolution = str(
            self.resolution.currentData()
            or "1280x720"
        )

        try:
            width, height = [
                int(x)
                for x in resolution.split("x", 1)
            ]
        except Exception:
            width, height = 1280, 720
            resolution = "1280x720"

        self.preview_before_virtual = (
            not self.preview_released
        )
        self.camera_before_virtual = self.camera_enabled

        # Qt must release the physical stream before FFmpeg opens it.
        self.stop_camera()

        self.compact_preview_layout()
        self.preview_restore_button.setEnabled(False)

        self.preview_status.setText(
            "Starting virtual camera..."
        )

        self.preview_button.setEnabled(False)

        self.virtual_start.setEnabled(False)
        self.virtual_stop.setEnabled(True)

        self.virtual_status.setText(
            f"Pipeline: Starting — {resolution} @ 30 fps"
        )

        self.message.setText(
            "Starting virtual camera"
        )

        # Give Qt a moment to fully release /dev/videoX.
        QTimer.singleShot(
            350,
            lambda: self.launch_virtual_camera(
                physical_device,
                virtual_device,
                resolution,
                width,
                height,
            )
        )


    def launch_virtual_camera(
        self,
        physical_device,
        virtual_device,
        resolution,
        width,
        height,
    ):
        if self.virtual_process is not None:
            self.virtual_starting = False
            self.release_virtual_lock()
            return

        process = QProcess(self)

        if not FFMPEG:
            self.virtual_starting = False
            self.release_virtual_lock()
            self.virtual_status.setText("Pipeline: FFmpeg missing")
            self.message.setText("FFmpeg is required for Virtual Camera mode")
            self.restore_after_virtual_camera()
            return

        process.setProgram(FFMPEG)

        process.setArguments(
            [
                "-hide_banner",
                "-loglevel",
                "warning",

                "-f",
                "v4l2",

                "-input_format",
                "mjpeg",

                "-video_size",
                resolution,

                "-framerate",
                "30",

                "-i",
                physical_device,

                "-vf",
                "format=yuv420p",

                "-f",
                "v4l2",

                virtual_device,
            ]
        )

        process.setProcessChannelMode(
            QProcess.ProcessChannelMode.MergedChannels
        )

        process.finished.connect(
            self.virtual_process_finished
        )

        self.virtual_process = process

        process.start()

        if not process.waitForStarted(3000):
            error = process.errorString()

            self.virtual_process = None
            self.virtual_starting = False
            self.release_virtual_lock()

            self.virtual_status.setText(
                "Pipeline: Failed to start"
            )

            self.message.setText(
                f"Virtual camera error: {error}"
            )

            self.restore_after_virtual_camera()

            return

        self.virtual_active = True
        self.virtual_starting = False

        self.virtual_status.setText(
            f"Pipeline: Running — {width} × {height} @ 30 fps"
        )

        self.update_resolution_control_state()

        self.preview_status.setText(
            "Virtual output active"
        )

        self.message.setText(
            "EMEET PIXY Virtual Camera running"
        )

        if self.bool_setting("camera/privacy_enabled", False):
            # Opening the physical stream can wake the camera out of privacy.
            QTimer.singleShot(
                500,
                lambda: self.backend("privacy"),
            )

        # We know the exact resolution selected by FFmpeg,
        # so native zoom availability remains predictable.
        self.update_resolution_info()


    def stop_virtual_camera(
        self,
        restore_preview=True,
    ):
        self.virtual_stopping = True

        process = self.virtual_process

        if process is None and self.virtual_external_pid is not None:
            subprocess.run(
                ["kill", str(self.virtual_external_pid)],
                check=False,
            )
            self.virtual_external_pid = None

        if process is not None:
            if (
                process.state()
                != QProcess.ProcessState.NotRunning
            ):
                process.terminate()

                if not process.waitForFinished(2000):
                    process.kill()
                    process.waitForFinished(1000)

            process.deleteLater()

        self.virtual_process = None
        self.virtual_external_pid = None
        self.virtual_active = False
        self.virtual_stopping = False
        self.virtual_starting = False
        self.release_virtual_lock()

        self.virtual_status.setText("Pipeline: Stopped")

        self.virtual_start.setEnabled(True)
        self.virtual_stop.setEnabled(False)

        if restore_preview:
            self.restore_after_virtual_camera()


    def virtual_process_finished(
        self,
        exit_code,
        exit_status,
    ):
        if self.virtual_stopping:
            return

        if not self.virtual_active:
            return

        self.virtual_process = None
        self.virtual_active = False
        self.virtual_starting = False
        self.release_virtual_lock()

        self.virtual_status.setText(
            f"Pipeline: Stopped unexpectedly ({exit_code})"
        )

        self.virtual_start.setEnabled(True)
        self.virtual_stop.setEnabled(False)

        self.message.setText(
            "Virtual camera process stopped"
        )

        self.restore_after_virtual_camera()


    def restore_after_virtual_camera(self):
        self.preview_button.setEnabled(True)

        if self.preview_before_virtual:
            self.camera_enabled = True
            self.settings.setValue("camera/enabled", True)
            self.restore_preview_layout()
            self.preview_released = False

            self.released_panel.hide()
            self.video_widget.show()

            self.preview_button.setText(
                "Release Preview"
            )

            self.resolution.setEnabled(True)

            self.preview_status.setText(
                "Starting live preview..."
            )

            QTimer.singleShot(
                300,
                self.start_camera
            )

            QTimer.singleShot(
                800,
                self.restore_camera_state
            )

        else:
            # Return to Direct/Release mode.
            self.preview_released = True
            self.preview_restore_button.setEnabled(True)

            self.video_widget.hide()

            self.released_panel.setText(
                "<b>CAMERA PREVIEW RELEASED</b><br><br>"
                "EMEET PIXY is available to Google Meet, OBS, "
                "Teams, and other applications.<br><br>"
                "Camera controls remain active."
            )

            self.released_panel.show()

            self.preview_button.setText(
                "Resume Preview"
            )

            self.preview_status.setText(
                "Controls active — video stream released"
            )

            self.resolution.setEnabled(False)
            self.zoom.setEnabled(False)

    def release_virtual_lock(self):
        if not self.virtual_lock_held:
            return

        self.virtual_lock.unlock()
        self.virtual_lock_held = False


    # --------------------------------------------------
    # BACKEND
    # --------------------------------------------------

    def backend(self, *args):
        if not BACKEND.exists():
            self.message.setText(f"Backend missing: {BACKEND}")
            return None

        result = subprocess.run(
            [sys.executable, str(BACKEND), *map(str, args)],
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            error = result.stderr.strip() or result.stdout.strip()
            self.message.setText("ERROR: " + error)
            return None

        output = result.stdout.strip()
        self.message.setText(output if output else "OK")

        return output

    # --------------------------------------------------
    # PTZ
    # --------------------------------------------------

    def make_move_button(self, text, command):
        button = QPushButton(text)
        button.setMinimumSize(80, 55)

        button.setAutoRepeat(True)
        button.setAutoRepeatDelay(250)
        button.setAutoRepeatInterval(100)

        button.clicked.connect(
            lambda checked=False: self.backend(command)
        )

        button.released.connect(self.refresh_status)

        return button

    def center_camera(self):
        self.backend("center")
        self.zoom.setValue(100)
        self.refresh_status()

    def apply_zoom(self):
        self.backend("zoom", self.zoom.value())
        self.settings.setValue("camera/zoom", self.zoom.value())
        self.refresh_status()

    # --------------------------------------------------
    # FEATURE STATE
    # --------------------------------------------------

    def privacy_state_path(self):
        state_home = os.environ.get(
            "XDG_STATE_HOME",
            str(Path.home() / ".local" / "state"),
        )
        return Path(state_home) / "emeet-pixy-control" / "privacy-state"

    def write_privacy_state(self, enabled):
        path = self.privacy_state_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("on\n" if enabled else "off\n")
        except OSError:
            pass

    def tracking_state_path(self):
        return self.privacy_state_path().with_name("tracking-state")

    def write_tracking_state(self, enabled):
        path = self.tracking_state_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("on\n" if enabled else "off\n")
        except OSError:
            pass

    def set_tracking_mode(self, mode):
        if mode not in ("idle", "track"):
            return

        self.settings.setValue("camera/tracking", mode)
        self.write_tracking_state(mode == "track")

        if self.bool_setting("camera/privacy_enabled", False):
            self.update_tracking_toggle_action(mode == "track")
            return

        if self.backend(mode) is not None:
            self.update_tracking_toggle_action(mode == "track")

    def set_privacy_mode(self, enabled):
        if enabled:
            status = self.backend("status")
            if status:
                privacy_position = {}
                for line in status.splitlines():
                    line = line.strip()
                    for axis in ("pan", "tilt", "zoom"):
                        if not line.startswith(f"{axis}:"):
                            continue
                        try:
                            privacy_position[axis] = int(
                                line.split(":", 1)[1]
                                .strip()
                                .split()[0]
                            )
                        except (ValueError, IndexError):
                            pass
                for axis, position in privacy_position.items():
                    self.settings.setValue(
                        f"camera/privacy_{axis}",
                        position,
                    )
            if self.backend("privacy") is not None:
                self.settings.setValue("camera/privacy_enabled", True)
                self.write_privacy_state(True)
        else:
            tracking = normalize_choice(
                self.settings.value("camera/tracking", "idle"),
                ("idle", "track"),
                "idle",
            )
            if self.backend(tracking) is None:
                return
            for axis in ("pan", "tilt", "zoom"):
                saved_position = self.settings.value(
                    f"camera/privacy_{axis}"
                )
                try:
                    saved_position = int(saved_position)
                except (TypeError, ValueError):
                    saved_position = None
                if saved_position is not None:
                    self.backend(axis, saved_position)
                    self.settings.setValue(
                        f"camera/{axis}",
                        saved_position,
                    )
            self.write_privacy_state(False)
            self.settings.setValue("camera/privacy_enabled", False)
            if not self.virtual_active and not self.preview_released:
                self.camera_enabled = True
                self.video_widget.show()
                self.released_panel.hide()
                self.preview_button.setEnabled(True)
                self.preview_status.setText("Starting live preview...")
                self.start_camera()
                QTimer.singleShot(800, self.restore_camera_state)
        self.update_privacy_toggle_action(enabled)

    def update_privacy_toggle_action(self, enabled):
        label = "Camera: On" if enabled else "Camera: Off"
        if self.privacy_toggle_action is not None:
            self.privacy_toggle_action.setChecked(enabled)
            self.privacy_toggle_action.setText(label)
        if self.privacy_button is not None:
            self.privacy_button.setChecked(enabled)
            self.privacy_button.setText(label)

    def update_tracking_toggle_action(self, enabled):
        if self.tracking_on_button is not None:
            self.tracking_on_button.setEnabled(not enabled)
        if self.tracking_off_button is not None:
            self.tracking_off_button.setEnabled(enabled)
        if self.tracking_on_action is not None:
            self.tracking_on_action.setEnabled(not enabled)
        if self.tracking_off_action is not None:
            self.tracking_off_action.setEnabled(enabled)

    def toggle_privacy(self, enabled):
        self.set_privacy_mode(enabled)

    def set_gesture(self, enabled):
        command = "gesture-on" if enabled else "gesture-off"

        if self.backend(command) is not None:
            self.settings.setValue("camera/gesture", bool(enabled))

    def apply_audio(self):
        mode = self.audio.currentData()

        if self.backend("audio", mode) is not None:
            self.settings.setValue("camera/audio", mode)

    def apply_flicker(self):
        mode = self.flicker.currentData()

        if self.backend("flicker", mode) is not None:
            self.settings.setValue("camera/flicker", mode)

    # --------------------------------------------------
    # PERSISTENCE
    # --------------------------------------------------

    def bool_setting(self, key, default=False):
        value = self.settings.value(key, default)

        if isinstance(value, bool):
            return value

        return str(value).lower() in (
            "1", "true", "yes", "on"
        )

    def load_saved_ui_state(self):
        zoom = int(
            self.settings.value("camera/zoom", 100)
        )
        zoom = max(100, min(150, zoom))

        self.zoom.setValue(zoom)
        self.zoom_label.setText(str(zoom))

        audio = str(
            self.settings.value("camera/audio", "nc")
        )
        index = self.audio.findData(audio)

        if index >= 0:
            self.audio.setCurrentIndex(index)

        flicker = str(
            self.settings.value("camera/flicker", "60")
        )
        index = self.flicker.findData(flicker)

        if index >= 0:
            self.flicker.setCurrentIndex(index)

        self.camera_enabled = self.bool_setting(
            "camera/enabled",
            True,
        )
        self.settings.setValue("camera/privacy_enabled", True)
        self.write_privacy_state(True)
        self.update_privacy_toggle_action(True)
        self.update_tracking_toggle_action(
            self.settings.value("camera/tracking", "idle") == "track"
        )

    def restore_camera_state(self):
        if self.restoring:
            return

        self.restoring = True

        try:
            pan = int(
                self.settings.value("camera/pan", 0)
            )
            tilt = int(
                self.settings.value("camera/tilt", 0)
            )
            zoom = int(
                self.settings.value("camera/zoom", 100)
            )

            tracking = self.camera_override_tracking
            if tracking is None:
                if self.bool_setting("camera/privacy_enabled", False):
                    tracking = "privacy"
                else:
                    tracking = normalize_choice(
                        self.settings.value("camera/tracking", "idle"),
                        ("idle", "track"),
                        "idle",
                    )

            gesture = self.bool_setting(
                "camera/gesture",
                False,
            )

            audio = normalize_choice(
                self.settings.value("camera/audio", "nc"),
                ("nc", "live", "org"),
                "nc",
            )

            flicker = normalize_choice(
                self.settings.value("camera/flicker", "60"),
                ("off", "50", "60"),
                "60",
            )

            pan = max(-150, min(150, pan))
            tilt = max(-90, min(90, tilt))
            zoom = max(100, min(150, zoom))

            self.backend("pan", pan)
            self.backend("tilt", tilt)

            if self.zoom.isEnabled():
                self.backend("zoom", zoom)

            self.backend(
                "gesture-on" if gesture else "gesture-off"
            )

            if audio in ("nc", "live", "org"):
                self.backend("audio", audio)

            if flicker in ("off", "50", "60"):
                self.backend("flicker", flicker)

            # Apply privacy last: PIXY HID settings can wake the video path.
            if tracking in ("idle", "track", "privacy"):
                self.backend(tracking)
                if self.camera_override_tracking == tracking:
                    self.camera_override_tracking = None

            self.zoom.setValue(zoom)
            self.zoom_label.setText(str(zoom))

            self.refresh_status(save=True)

            self.message.setText(
                "Saved camera settings restored"
            )

        finally:
            self.restoring = False

    # --------------------------------------------------
    # STATUS
    # --------------------------------------------------

    def refresh_status(self, save=True):
        output = self.backend("status")

        if not output:
            self.status.setText("Camera unavailable")
            return

        self.status.setText(output)

        parsed = {}

        for line in output.splitlines():
            line = line.strip()

            if line.startswith("pan:"):
                try:
                    parsed["pan"] = int(
                        line.split(":", 1)[1]
                        .strip()
                        .split()[0]
                    )
                except (ValueError, IndexError):
                    pass

            elif line.startswith("tilt:"):
                try:
                    parsed["tilt"] = int(
                        line.split(":", 1)[1]
                        .strip()
                        .split()[0]
                    )
                except (ValueError, IndexError):
                    pass

            elif line.startswith("zoom:"):
                try:
                    parsed["zoom"] = int(
                        line.split(":", 1)[1].strip()
                    )
                except ValueError:
                    pass

        if "zoom" in parsed:
            self.zoom.blockSignals(True)
            self.zoom.setValue(parsed["zoom"])
            self.zoom.blockSignals(False)
            self.zoom_label.setText(str(parsed["zoom"]))

        if save:
            for key in ("pan", "tilt", "zoom"):
                if key in parsed:
                    self.settings.setValue(
                        f"camera/{key}",
                        parsed[key]
                    )

    # --------------------------------------------------
    # EXIT
    # --------------------------------------------------

    def closeEvent(self, event):
        if not self.quitting and self.tray is not None:
            self.hide()
            if self.panel_toggle_action is not None:
                self.panel_toggle_action.setText("Show Control Panel")
            event.ignore()
            return

        self.refresh_status(save=True)

        self.settings.setValue(
            "window/geometry",
            self.saveGeometry()
        )

        self.settings.sync()

        if self.virtual_active or self.virtual_process is not None:
            self.stop_virtual_camera(False)

        self.stop_camera()

        event.accept()


def build_parser():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--background",
        action="store_true",
        help="Start quietly for boot/login autostart.",
    )
    parser.add_argument(
        "--foreground",
        action="store_true",
        help="Start with the visible preview window for testing and manual use.",
    )
    return parser


def main(argv=None):
    parser = build_parser()
    args, remaining = parser.parse_known_args(argv)

    app = QApplication([sys.argv[0], *remaining])
    app.setStyleSheet(APP_STYLESHEET)

    if not args.background and background_service_is_active():
        QMessageBox.warning(
            None,
            "EMEET PIXY Control is already running",
            "The background control app is already active.\n\n"
            "Use its system-tray icon to open the control panel, or quit "
            "the existing instance before starting another one.",
        )
        return 1

    background_mode = args.background and not args.foreground
    window = PixyGUI(start_minimized=background_mode)

    if background_mode:
        window.hide()
    else:
        window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
