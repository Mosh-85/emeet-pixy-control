# Install EMEET PIXY Control on Fedora

This guide covers installing [EMEET PIXY Control](https://github.com/rolfobermaier/emeet-pixy-control) on Fedora 44. It is based on a working Fedora 44 installation using kernel 7.1 and RPM Fusion.

## What will be installed

EMEET PIXY Control requires:

- Python 3.10 or newer
- `v4l2-ctl` from `v4l-utils`
- FFmpeg
- `v4l2loopback-ctl`
- the `v4l2loopback` kernel module
- systemd

The project's installer creates a private Python virtual environment, installs PySide6, adds an application-menu entry, installs a udev rule and enables a persistent virtual-camera service.

## 1. Enable RPM Fusion

The `v4l2loopback` packages are provided by RPM Fusion Free. Check whether it is already enabled:

```bash
dnf repolist --enabled | grep rpmfusion
```

The output should include at least:

```text
rpmfusion-free
rpmfusion-free-updates
```

If those repositories are missing, follow the current RPM Fusion configuration instructions for your Fedora release before continuing.

## 2. Install the required packages

Install the core dependencies and the kernel module:

```bash
sudo dnf install \
  git \
  python3 python3-pip \
  v4l-utils \
  v4l2loopback akmod-v4l2loopback \
  kernel-devel-$(uname -r)
```

Check whether FFmpeg is already installed:

```bash
ffmpeg -version | head -n 1
```

If the command is missing, install Fedora's build:

```bash
sudo dnf install ffmpeg-free
```

### Avoiding the FFmpeg package conflict

Fedora provides `ffmpeg-free`, while RPM Fusion also provides a package named `ffmpeg`. These packages conflict with each other. If `ffmpeg-free` is already installed and working, do not add `ffmpeg` to the dependency command and do not use `--allowerasing`. EMEET PIXY Control only needs the `ffmpeg` command; Fedora's `ffmpeg-free` package provides it.

If RPM Fusion's full `ffmpeg` package is already installed and `ffmpeg -version` works, keep that package instead.

## 3. Build the kernel module

Ask Fedora's akmods system to build modules for the installed kernels:

```bash
sudo akmods --force
```

A successful result resembles:

```text
Checking kmods exist for 7.x.x-200.fc44.x86_64 [  OK  ]
```

Verify the module and commands:

```bash
ffmpeg -version | head -n 1
v4l2-ctl --version
v4l2loopback-ctl --version
modinfo v4l2loopback | head
```

At this stage, `v4l2loopback-ctl --version` may print its version and then report:

```text
unable to open control device '/dev/v4l2loopback': Permission denied
```

This does not necessarily mean the module is broken. On Fedora, the control device can be restricted to root. The persistent system service will create the actual virtual camera as root.

## 4. Install EMEET PIXY Control

Clone the project and run its installer as your normal user:

```bash
git clone https://github.com/rolfobermaier/emeet-pixy-control.git
cd emeet-pixy-control
./install.sh
```

Do not run `install.sh` with `sudo`. The installer needs to create the Python environment and desktop files inside your home directory. It will request elevated privileges itself when installing the system service and udev rule.

## 5. Ensure your user belongs to the video group

Check your current groups:

```bash
groups
```

If `video` is missing, add your user explicitly:

```bash
sudo usermod -aG video "$USER"
```

Confirm that the group entry now contains your username:

```bash
getent group video
```

Log out completely and back in, or reboot. Opening a new terminal without ending the graphical session is not sufficient.

After logging in again, verify the membership:

```bash
groups
```

## 6. Verify the service and cameras

Check the persistent virtual-camera service:

```bash
systemctl status emeet-pixy-virtual-camera.service --no-pager
```

The service may show `active (exited)`. That is expected for a one-shot service that creates the virtual device and then exits.

List all video devices:

```bash
v4l2-ctl --list-devices
```

A successful setup should show three relevant entries:

```text
EMEET PIXY Virtual Camera (platform:v4l2loopback-020):
        /dev/video20

EMEET PIXY: EMEET PIXY (...):
        /dev/video0
        /dev/video1
        /dev/media0
```

Device numbers for the physical camera can differ. The project service normally creates the virtual camera as `/dev/video20`.

Check its permissions:

```bash
ls -l /dev/video20
```

The device should normally belong to the `video` group. Your logged-in user must also appear in that group.

## 7. Start the application

Open **EMEET PIXY Control** from the application menu. If advanced HID controls are unavailable, unplug and reconnect the PIXY once after installation.

When selecting a camera in another application, choose **EMEET PIXY Virtual Camera**, normally exposed as `/dev/video20`.

## Troubleshooting

### The virtual camera is missing

Restart the service and inspect its log:

```bash
sudo systemctl restart emeet-pixy-virtual-camera.service
systemctl status emeet-pixy-virtual-camera.service --no-pager
journalctl -u emeet-pixy-virtual-camera.service -b --no-pager
```

Then list the devices again:

```bash
v4l2-ctl --list-devices
```

### The kernel module cannot be loaded

Check the current kernel and matching development package:

```bash
uname -r
rpm -q kernel-devel-$(uname -r)
modinfo v4l2loopback | head
```

If the matching `kernel-devel` package is missing:

```bash
sudo dnf install kernel-devel-$(uname -r)
sudo akmods --force
```

If Secure Boot is enabled, an unsigned akmod may be blocked from loading. Check for a rejection with:

```bash
sudo journalctl -k -b | grep -Ei 'v4l2loopback|secure boot|key was rejected'
```

### `v4l2loopback-ctl` reports permission denied

First verify that the actual virtual camera exists:

```bash
v4l2-ctl --list-devices
ls -l /dev/video20
```

If `/dev/video20` exists and EMEET PIXY Control can use it, the root-only `/dev/v4l2loopback` control device is not blocking normal operation. Do not loosen its permissions merely to make the version command silent.

### The user is still not in the video group

Run:

```bash
sudo usermod -aG video "$USER"
getent group video
```

Then log out completely and log back in. Verify with `groups` before launching the application.
