# Ubuntu for Xiaomi Pad 6 Pro

Run Ubuntu 26.04 on the Xiaomi Pad 6 Pro, with the GNOME desktop and a device-adapted kernel based on upstream Linux. [中文](README.zh-CN.md)

## 📮 Follow & Discuss

Progress updates, usage tips and AI discussions (primarily in Chinese) are published here first — issue feedback is welcome.

| WeChat: 熵减矩阵 | Xiaohongshu: yzddmr6 |
|:---:|:---:|
| <img src="docs/assets/gzh-qr.png" width="200" alt="WeChat: 熵减矩阵"> | <img src="docs/assets/xhs-qr.jpg" width="200" alt="Xiaohongshu: yzddmr6"> |
| Updates · Tips · AI talks | Sharing · Feedback |

## ⚠️ Read Before Installing

This project is only for the **Xiaomi Pad 6 Pro (liuqin, SM8475)**. Other Xiaomi Pad models are not compatible.

- **Data loss**: unlocking the bootloader and performing an initial installation erase user data. Back up your files first.
- **Storage and layout**: installation has been tested on one known **256 GB partition layout**, not every 256 GB device. Other capacities and modified partition layouts are unverified and unsupported. The installer repartitions the tail of the disk. In the dual-boot layout both systems boot from slot A; the Linux-only layout places Ubuntu in slot B. Do not bypass the checks.
- **Validation scope**: initial installation, first boot, rotation, touch, the magnetic keyboard (including its touchpad), audio and USB OTG host mode (wired mouse) have been tested. Android recovery still requires separate device validation.
- **Installation layout**: the installer offers a Linux-only layout and a dual-boot layout that keeps a stock Android. In the dual-boot layout both systems boot from slot A, and switching writes the other system's boot image into `boot_a`. An earlier revision of the dual-boot layout, which booted Ubuntu from slot B, has been installed on the 256 GB unit; the current slot-A arrangement, switching between the systems, Android's first boot and KernelSU are not yet validated on hardware, and the Linux-only layout has not been installed on hardware. See the [installation steps](docs/INSTALL-TESTING.md).
- **Recovery preparation**: obtain the matching stock firmware and read the [data and recovery instructions](docs/FLASHING.md#data-and-recovery) before installing.
- **Hardware limitations**: some features are incomplete. Review the hardware support table below.

Flashing an incorrect boot image or partition can prevent the tablet from starting. Follow the installation guide and keep your backups.
Factory calibration and device addresses must come from the same tablet; never copy them between devices.

## 🚀 Getting Started

Use a complete installation bundle from [GitHub Releases](https://github.com/yzddmr6/xiaomipad-6pro-mainline/releases). Do not mix files from different versions.

| I want to | Read |
|---|---|
| Install Ubuntu | [Installation guide](docs/FLASHING.md) |
| Build the kernel and device components | [Build guide](docs/BUILD.md) |
| Restore Android | [Data and recovery instructions](docs/FLASHING.md#data-and-recovery) |

Prebuilt installation bundles include matching boot and root filesystem images, installation tools and checksums.

## Hardware Support

This describes hardware support and known limitations for Xiaomi Pad 6 Pro (liuqin).
Component identities come from confirmed board information
and device trees; other batches, capacities and accessory combinations are not implied tested.

✅ Working · 🟡 Partial · ❌ Unsupported · 🧪 Unverified

### Platform, Display and Input

| Feature | Component / Implementation | Status | Scope and Limitations |
|---|---|---|---|
| SoC / CPU | Qualcomm Snapdragon 8+ Gen 1 (SM8475), ARM64 | ✅ Working | Kernel boot and Ubuntu desktop; not all power states are validated |
| GPU / compositing | Adreno 730 / Freedreno / Mesa | ✅ Working | Desktop acceleration; some applications need rendering workarounds |
| Internal storage | UFS / ext4 | ✅ Working | Persistent system and packages on `linux_root` and `linux_home`; installer targets the known 256 GB layout only |
| Display | Novatek NT36532 / dual DSI / DSC | ✅ Working | 2880 x 1800 at 120 Hz; other refresh rates are not individually tested |
| Manual brightness | Kinetic KTZ8866 backlight | ✅ Working | Backlight and manual brightness adjustment |
| Touchscreen | Novatek NT36532 / SPI (CSOT or TM panel) | ✅ Working | Driver selects the firmware by panel module; touch input, swipes and gestures |
| Magnetic keyboard | Nanosic WN8030 | ✅ Working | Character and volume keys, touchpad, reattachment; suspend recovery not fully covered |
| Stylus | NVTCapacitivePen input interface | 🧪 Unverified | Coordinates, pressure, buttons and input after wake not tested |
| Hall switches | GPIO / SW_LID / SW_TABLET_MODE | 🟡 Partial | Switch states are readable; cover-close and open-to-wake policies not fully validated |
| Fingerprint | FPC1264 / OEM TA / libfprint TOD | 🟡 Partial | Single-finger enrolment, persistent matching and GNOME unlock verified on the recorded temporary support boot; placement sensitivity and stutter remain. Optional integration: [Fingerprint](docs/FINGERPRINT.md) |

### Wireless and USB

| Feature | Component / Implementation | Status | Scope and Limitations |
|---|---|---|---|
| Wi-Fi 2.4 GHz | Qualcomm QCA6490 / ath11k | ✅ Working | Wireless connection and everyday networking |
| Wi-Fi 5 GHz | QCA6490, supported as WCN6855 family | ✅ Working | 5 GHz connections verified; no peak-throughput claim |
| Wi-Fi hotspot / AP | NetworkManager / ath11k | 🧪 Unverified | Confirmed networking scope is client mode |
| Bluetooth | QCA6490 / hci_qca / BlueZ | ✅ Working | Everyday Bluetooth functionality is usable |
| USB 2.0 device mode | Synopsys DWC3 / NXP eUSB2 repeater | ✅ Working | USB NCM networking and transfer; High-Speed device mode |
| USB reconnect after charging | USB-C / USB gadget | 🟡 Partial | Since v0.1.1 the UCSI typec controller negotiates automatically; a dedicated retest is pending |
| USB 3.x SuperSpeed | USB controller / PHY | 🧪 Unverified | Since v0.1.1 the SM8475 PHY tables and controller are in place; SuperSpeed peripheral enumeration untested |
| USB OTG / host mode | USB-C data-role switching | ✅ Working | Since v0.1.1 UCSI negotiates the role automatically; wired mouse validated, USB drives and docks pending |
| USB-C external display | Video output / docks | 🧪 Unverified | External-monitor output has not been tested |

### Audio, Video and Sensors

| Feature | Component / Implementation | Status | Scope and Limitations |
|---|---|---|---|
| Four speakers | 4 × Cirrus Logic CS35L41 / AudioReach | ✅ Working | Stereo playback and volume control with per-device calibration; tuning continues |
| Internal microphone | WCD938x AMIC1 over SoundWire → LPASS TX macro → ADSP codec DMA | ✅ Working | Mono 48 kHz capture through the UCM "Mic" device; validated on hardware from a RAM-booted build of this branch. The other three mics and any noise suppression are not wired yet |
| H.264 hardware decoding | Qualcomm Iris2 / V4L2 | ✅ Working | Userspace decoding verified; not evidence of browser integration |
| Other decoding formats | Iris / V4L2 | 🧪 Unverified | HEVC, VP9 and other formats not individually validated |
| Browser hardware decoding | Browser / V4L2 integration | 🧪 Unverified | Video playback alone does not prove hardware decoding |
| Hardware encoding | Qualcomm video engine | 🧪 Unverified | Hardware encoding workflows not tested |
| Rear camera | S5KJN1 / Qualcomm CAMSS / libcamera software ISP | 🟡 Partial | Preview, photos and video in the Camera app; software image processing, no autofocus, video stutters. See [Camera](docs/CAMERA.md) |
| Front camera | Camera sensor / CAMSS | ❌ Unsupported | Not enabled or validated |
| Accelerometer / auto-rotation | SLPI / SSC / iio-sensor-proxy | ✅ Working | First boot, login screen, desktop rotation and magnetic-keyboard landscape use |
| Gyroscope / magnetometer | SSC sensor path | 🧪 Unverified | Application-usable measurements not confirmed by accelerometer support |
| Ambient light sensor | SSC light-sensor path | 🧪 Unverified | Real light measurements not fully validated |
| Automatic brightness | Desktop brightness policy | ❌ Unsupported | Automatic brightness control not integrated |

### Power and Time

| Feature | Component / Implementation | Status | Scope and Limitations |
|---|---|---|---|
| Power / volume keys | Qualcomm PMIC / GPIO input | ✅ Working | Screen on/off, power menu and volume; password-lock authentication not separately tested |
| Battery / basic charging | qcom_battmgr / UPower | ✅ Working | Capacity reporting, charging state and basic wall charging |
| Computer USB power | USB power path | 🟡 Partial | Limited supply power; heavy workloads may still discharge the battery |
| Xiaomi proprietary fast charging | Vendor charging protocol | 🟡 Partial | Charger authentication integrated; battery authentication verified and PD adapters reach 9 V / 2 A. MiPPS and standard PPS fast charging await adapter validation |
| Charging while powered off | Boot-stage charging hold | 🟡 Partial | No complete charging display; use a long power-key press to boot while charging |
| Suspend / resume | Linux power management | 🟡 Partial | Basic resume verified; peripheral recovery and deep-sleep power need further testing |
| RTC / offline time retention | Qualcomm PMK8350 RTC | 🧪 Unverified | Network time synchronization works; offline writes and power-loss retention are not guaranteed |

## Usage and Maintenance

Ubuntu packages are managed through APT. Update instructions for the project kernel and device components will accompany each installation release.
Do not mix boot images and system components from different releases.

To run Android applications inside Ubuntu, Waydroid is supported; the required kernel configuration is built in. See [Waydroid support](docs/WAYDROID.md).

Charging power is negotiated through the vendor charger authentication path. See [Fast charging](docs/FASTCHARGE.md).

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
