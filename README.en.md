# Ubuntu for Xiaomi Pad 6 Pro

Run the Ubuntu 26.04 GNOME desktop natively on the Xiaomi Pad 6 Pro (codename `liuqin`, Snapdragon 8+ Gen 1), optionally alongside Android, with a kernel adapted from upstream Linux. [中文](README.md)

**The latest version is on [Releases](https://github.com/yzddmr6/xiaomipad-6pro-mainline/releases/latest). Official builds are published only in this repository's Releases, free of charge.** See [Hardware Support](#hardware-support) for the support status.

## 📮 Follow & Discuss

Progress updates, usage tips and AI discussions (primarily in Chinese) are published here first — issue feedback is welcome.

| WeChat: 熵减矩阵 | Xiaohongshu: yzddmr6 |
|:---:|:---:|
| <img src="docs/assets/gzh-qr.png" width="200" alt="WeChat: 熵减矩阵"> | <img src="docs/assets/xhs-qr.jpg" width="200" alt="Xiaohongshu: yzddmr6"> |
| Updates · Tips · AI talks | Sharing · Feedback |

## ⚠️ Read Before Installing

This project is only for the **Xiaomi Pad 6 Pro (liuqin, SM8475)**. Other Xiaomi Pad models are not compatible.

- **This is an experimental project.** Installation unlocks, repartitions and erases the tablet; a mistake can leave it unable to start. Follow the installation guide and keep your backups.
- **Data loss**: unlocking the bootloader and performing an initial installation erase user data. Back up your files first.
- **Validated only on the 256 GB model.** The 128 GB and 512 GB models share the factory partition structure and are accepted by the installer, but they are not validated; modified partition layouts are not supported. The installer repartitions the tail of the disk; do not bypass its checks.
- **Dual boot**: the dual-boot layout keeps stock Android. Both systems boot from slot A, and switching writes the other system's boot image into `boot_a`. Never use KernelSU's "install to inactive slot", never run the stock `flash_all` script again, and keep system updates frozen; see the slot rules in the [installation steps](docs/INSTALL-TESTING.en.md#switching-between-the-systems).
- **Recovery preparation**: obtain the matching stock firmware and read the [data and recovery instructions](docs/FLASHING.en.md#data-and-recovery) before installing.
- **Hardware limitations**: some features are incomplete. Review the hardware support table below.

Factory calibration and device addresses must come from the same tablet; never copy them between devices.

## 🚀 Getting Started

Download the complete installation bundle of one version from [GitHub Releases](https://github.com/yzddmr6/xiaomipad-6pro-mainline/releases/latest). Do not mix files from different versions.

| I want to | Read |
|---|---|
| Install Ubuntu | [Installation guide](docs/FLASHING.en.md) |
| Upgrade an installed system | [Upgrading an installed system](docs/INSTALL-TESTING.en.md#upgrading-an-installed-system) |
| Build the kernel and device components | [Build guide](docs/BUILD.en.md) |
| Restore Android | [Data and recovery instructions](docs/FLASHING.en.md#data-and-recovery) |

Prebuilt installation bundles include matching boot and root filesystem images, installation tools and checksums.

## Hardware Support

Support in the latest release. ✅ Working · 🟡 Partial · ❌ Unsupported.
Component identities come from confirmed board information and device trees.

At a glance; see the tables for the scope of each feature:

- ✅ CPU · ✅ GPU acceleration · ✅ Internal storage · ✅ 120 Hz display · ✅ Touch · ✅ Manual brightness
- ✅ Magnetic keyboard and touchpad · ✅ Native fingerprint login · ✅ Auto-rotation · ✅ Power and volume keys
- ✅ Wi-Fi · ✅ Bluetooth · ✅ USB networking and developer mode · ✅ USB OTG · ✅ DP output
- ✅ Four speakers · ❌ Recording currently unavailable · ✅ H.264 hardware decoding · 🟡 Rear camera · ✅ Battery reporting and charging · 🟡 Standby

### Platform, Display and Input

| Feature | Component / Implementation | Status | Details |
|---|---|---|---|
| SoC / CPU | Qualcomm Snapdragon 8+ Gen 1 (SM8475), ARM64 | ✅ Working | Mainline kernel and Ubuntu desktop |
| GPU / compositing | Adreno 730 / Freedreno / Turnip / Mesa | ✅ Working | Hardware-accelerated compositing and OpenGL ES and Vulkan for applications, using the distribution's Mesa |
| Internal storage | UFS / ext4 | ✅ Working | Separate system and `/home` partitions |
| Display | Novatek NT36532 / dual DSI / DSC | ✅ Working | 2880×1800 at 120 Hz |
| Manual brightness | Kinetic KTZ8866 backlight | ✅ Working | Backlight and manual brightness adjustment |
| Touchscreen | Novatek NT36532 / SPI (CSOT or TM panel) | ✅ Working | Firmware selected automatically by panel module; taps, swipes and gestures |
| Magnetic keyboard | Nanosic WN8030 | ✅ Working | Character and volume keys and touchpad; typing resumes after reattaching |
| Fingerprint | FPC1264 / libfprint TOD / fprintd | ✅ Working | Enrol in Settings → Users, then unlock the screen or log in at boot; currently the first account (UID 1000) with one finger, see the [fingerprint guide](docs/FINGERPRINT.en.md) |
| Cover open/close | GPIO / SW_LID | ✅ Working | Closing the cover suspends the tablet according to the power settings |

Not yet supported: stylus.

### Wireless and USB

| Feature | Component / Implementation | Status | Details |
|---|---|---|---|
| Wi-Fi | Qualcomm QCA6490 / ath11k | ✅ Working | 2.4 GHz and 5 GHz client connections |
| Bluetooth | QCA6490 / hci_qca / BlueZ | ✅ Working | Pairing and everyday use |
| USB device mode | Synopsys DWC3 / USB networking (NCM) | ✅ Working | SSH login over the USB cable in developer mode, off by default |
| USB OTG / host mode | USB-C data-role switching / UCSI | ✅ Working | Switches to host mode automatically; USB input devices such as a wired mouse work |
| USB-C external display | DisplayPort Alt Mode / FSA4480 | ✅ Working | Picture on an external monitor, internal display restored after unplugging, USB restored when reconnected to a computer |

Not yet supported: Wi-Fi hotspot.

### Audio, Video and Sensors

| Feature | Component / Implementation | Status | Details |
|---|---|---|---|
| Four speakers | 4 × Cirrus Logic CS35L41 / AudioReach | ✅ Working | Stereo playback and volume control with this tablet's factory calibration |
| Internal microphone | WCD938x / SoundWire / LPASS / ADSP | ❌ Unsupported | Recording is currently unavailable |
| H.264 hardware decoding | Qualcomm Iris2 / V4L2 | ✅ Working | Hardware decoding through the V4L2 interface |
| Rear camera | S5KJN1 / Qualcomm CAMSS / libcamera software ISP | 🟡 Partial | Preview, photos and video in the Camera app; software image processing, no autofocus, video is not smooth. See [Camera](docs/CAMERA.en.md) |
| Accelerometer / auto-rotation | SLPI / SSC / iio-sensor-proxy | ✅ Working | The login screen and desktop rotate with the tablet; landscape is kept while the magnetic keyboard is attached |

Not yet supported: front camera, gyroscope and magnetometer, ambient light sensor and automatic brightness, hardware decoding of other formats, browser hardware decoding, hardware video encoding.

### Power and Time

| Feature | Component / Implementation | Status | Details |
|---|---|---|---|
| Power / volume keys | Qualcomm PMIC / GPIO input | ✅ Working | Screen on/off, power menu and volume |
| Battery / charging | qcom_battmgr / UPower | ✅ Working | Charge level and charging state, wall charging |
| Fast charging | Xiaomi charger authentication | 🟡 Partial | Fast charging for chargers that pass Xiaomi authentication; other USB PD chargers charge at standard PD levels (9 V / 2 A). See [Fast charging](docs/FASTCHARGE.en.md) |
| Computer USB power | USB power path | 🟡 Partial | Limited supply power; the charge level can drop under heavy load |
| Charging while powered off | Boot-stage charging hold | 🟡 Partial | Keeps charging while off and plugged in, without a charging animation; press and hold the power key to boot |
| Standby | suspend-to-idle | 🟡 Partial | Stays asleep after suspending and resumes normally on a timer wake; standby power consumption is not optimised yet |

Not yet supported: hibernation to disk, writing the hardware clock (the clock is set from the network after boot; when starting offline it continues from the last shutdown).

## Usage and Maintenance

Ubuntu packages are managed through APT. The project kernel and device components are updated together with the installation bundle: upgrade with a reinstall that keeps `/home`, see [Upgrading an installed system](docs/INSTALL-TESTING.en.md#upgrading-an-installed-system). Do not mix boot images and system components from different releases.

**Developer mode.** `sudo liuqin-devmode on` enables root SSH login over the USB cable; it is off by default and accepts keys only. Installing with `--enable-rescue` provisions the host's public key. Connect from the host with `python3 tools/liuqin-devmode-ssh.py root`, and run `sudo liuqin-devmode off` when done.

To run Android applications inside Ubuntu, Waydroid is supported; the required kernel configuration is built in. See [Waydroid support](docs/WAYDROID.en.md).

Charging power is negotiated through the vendor charger authentication path. See [Fast charging](docs/FASTCHARGE.en.md).

Report problems through GitHub Issues with the device model, system version, reproduction steps and relevant logs.
Remove passwords, network credentials and personal information before sharing logs.
The accounts listed above are also available for usage discussions.

## Development and Contributions

| Repository | Contents |
|---|---|
| xiaomipad-6pro-mainline | Device configuration, userspace integration, build and installation tools, documentation |
| [linux-sm8450-liuqin](https://github.com/yzddmr6/linux-sm8450-liuqin) | Complete Linux kernel source and device adaptation commits |

[kernel/source.json](kernel/source.json) records the kernel revision used by the build.
The device branch is `liuqin-6.17`. Contributions to drivers, tools and documentation are welcome;
see [CONTRIBUTING.md](CONTRIBUTING.md).

## Acknowledgments and Licensing

This project builds on the kernel work of [sm8450-mainline](https://github.com/sm8450-mainline/linux),
along with Ubuntu, GNOME, Freedreno and the Linux Qualcomm community.

Original project code is MIT-licensed unless a file states otherwise. Linux and third-party components retain their own licenses;
firmware is subject to its respective owners' terms. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
