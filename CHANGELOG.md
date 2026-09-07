# Changelog

## Unreleased - 2026-09-07

- Added background service startup and foreground preview-test modes to separate quiet autostart from visible testing
- Virtual Camera now starts automatically in foreground and background launches
- Privacy is applied before preview startup and re-applied after FFmpeg opens the camera
- Added tray-based controls, duplicate-instance protection, and clearer status handling for background operation
- Refined the UI with compact status/header layout, improved styling, wheel-safe settings controls, and clearer camera/tracking controls
- Improved keyboard/desktop privacy shortcuts and install setup for persistent virtual-camera and background app launch
- Added independent tracking and privacy controls with Waybar support
- Privacy mode now remembers and restores the latest pan, tilt, zoom, and tracking state
- Removed the obsolete Startup Defaults panel; camera controls now use the latest saved settings
- Improved virtual-camera ownership detection and restored the in-app preview after stopping the pipeline
- Made tracking/privacy indicators static because the proprietary HID protocol does not expose readable state
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
