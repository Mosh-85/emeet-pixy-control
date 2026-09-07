# Changelog

## Unreleased - 2026-09-07

- Added background service startup and foreground preview-test modes to separate quiet autostart from visible testing
- Improved startup defaults for camera/privacy state so the app can launch with the last saved behavior
- Added tray-based controls, duplicate-instance protection, and clearer status handling for background operation
- Refined the UI with compact status/header layout, improved styling, and better visibility of the camera toggle in release-preview mode
- Improved keyboard/desktop privacy shortcuts and install setup for persistent virtual-camera and background app launch
- Fixed startup sync issues where the privacy toggle could appear out of sync with the saved configuration
- Added installation support for user-level autostart and quick privacy toggles for Linux desktop environments

## 0.1.0 - 2026-08-24

Initial public preview.

- Native PySide6 preview/control GUI
- Dynamic PIXY video/HID discovery
- PTZ, center, zoom, tracking, privacy, gesture, audio and anti-flicker controls
- Resolution selection and saved settings
- Release Preview mode for direct use by Meet/OBS/Teams
- FFmpeg + v4l2loopback Virtual Camera mode
- Persistent virtual-camera systemd service
- Targeted udev permissions for EMEET PIXY HID access
- Responsive split layout with scrollable controls
- Desktop launcher and project icon
