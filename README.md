# Ubuntu for Xiaomi Pad 6 Pro

在 Xiaomi Pad 6 Pro（代号 `liuqin`，骁龙 8+ Gen 1）上原生运行 Ubuntu 26.04 GNOME 桌面，可与 Android 双系统共存，内核基于上游 Linux 适配。[English](README.en.md)

**最新版本见 [Releases](https://github.com/yzddmr6/xiaomipad-6pro-mainline/releases/latest)。官方发布只在本仓库 Releases，免费提供。** 支持状态见[硬件支持](#硬件支持)。

## 📮 关注与交流

最新进展动态、使用技巧和 AI 讨论会先在公众号和小红书更新；刷机遇到问题也欢迎反馈。

| 公众号：熵减矩阵 | 小红书：yzddmr6 |
|:---:|:---:|
| <img src="docs/assets/gzh-qr.png" width="200" alt="公众号：熵减矩阵"> | <img src="docs/assets/xhs-qr.jpg" width="200" alt="小红书：yzddmr6"> |
| 进展动态 · 使用技巧 · AI 讨论 | 使用分享 · 问题反馈 |

## ⚠️ 安装前请阅读

本项目仅适用于 **Xiaomi Pad 6 Pro（liuqin，SM8475）**。其他小米平板型号不适用。

- **这是实验性项目。** 安装会解锁、重分区、清空数据；操作不当可能导致设备无法启动。请按安装指南操作，并保留备份。
- **数据清除**：解锁 Bootloader 和首次安装会清除用户数据，请提前备份。
- **只在 256 GB 机型上验证过。** 128 GB 与 512 GB 机型的出厂分区结构相同，安装器同样接受，但未经验证；改过分区的设备不支持安装。安装器会重新划分磁盘尾部，不要绕过校验。
- **双系统**：双系统布局保留原厂 Android，两个系统都从 A 槽启动，切换时把另一系统的 boot 镜像写入 `boot_a`。不要使用 KernelSU 的“安装到未使用的槽位”，不要再次运行原厂 `flash_all` 脚本，系统更新须冻结；槽位规则见[安装步骤](docs/INSTALL-TESTING.md#双系统切换)。
- **恢复准备**：安装前请准备对应的原厂固件，并阅读[数据与恢复说明](docs/FLASHING.md#数据与恢复)。
- **功能限制**：部分硬件功能尚不完整，请先查看下方硬件支持表。

出厂校准与设备地址必须来自本机，不可跨设备复制。

## 🚀 获取与安装

请从 [GitHub Releases](https://github.com/yzddmr6/xiaomipad-6pro-mainline/releases/latest) 下载同一版本的完整安装包，不要混用不同版本的文件。

| 我想要 | 入口 |
|---|---|
| 安装 Ubuntu | [安装指南](docs/FLASHING.md) |
| 升级已安装的系统 | [升级已安装的系统](docs/INSTALL-TESTING.md#升级已安装的系统) |
| 自行编译内核和设备组件 | [构建指南](docs/BUILD.md) |
| 恢复 Android | [数据与恢复说明](docs/FLASHING.md#数据与恢复) |

预编译安装包包括匹配的启动镜像、根文件系统、安装工具与校验文件。

## 硬件支持

以下为最新发布版本的支持情况。✅ 可用 · 🟡 部分支持 · ❌ 不支持。
器件信息来自本项目已确认的板级资料和设备树。

功能速览，具体范围见下表：

- ✅ CPU · ✅ GPU 硬件加速 · ✅ 内置存储 · ✅ 120 Hz 显示 · ✅ 触摸 · ✅ 手动亮度
- ✅ 磁吸键盘与触摸板 · ✅ 原生指纹登录 · ✅ 自动旋转 · ✅ 电源键与音量键
- ✅ Wi-Fi · ✅ 蓝牙 · ✅ USB 网络与开发者模式 · ✅ USB OTG · ✅ DP 外屏
- ✅ 四扬声器 · ✅ 单麦克风录音 · ✅ H.264 硬解 · 🟡 后摄 · ✅ 电量显示与充电 · 🟡 待机

### 平台、显示与输入

| 功能 | 器件 / 实现 | 状态 | 说明 |
|---|---|---|---|
| SoC / CPU | Qualcomm Snapdragon 8+ Gen 1（SM8475），ARM64 | ✅ 可用 | 主线内核与 Ubuntu 桌面运行 |
| GPU / 桌面合成 | Adreno 730 / Freedreno / Turnip / Mesa | ✅ 可用 | 桌面合成与应用的 OpenGL ES、Vulkan 硬件加速，使用发行版 Mesa |
| 内置存储 | UFS / ext4 | ✅ 可用 | 独立的系统分区与 `/home` 分区 |
| 内置显示 | Novatek NT36532 / 双 DSI / DSC | ✅ 可用 | 2880×1800，120 Hz |
| 手动亮度 | Kinetic KTZ8866 背光控制 | ✅ 可用 | 背光与手动亮度调节 |
| 触控 | Novatek NT36532 / SPI（CSOT 或 TM 面板） | ✅ 可用 | 按面板模块自动选择固件；点击、滑动与触控手势 |
| 磁吸键盘 | Nanosic WN8030 | ✅ 可用 | 普通按键、音量键与触摸板；拆下后重新吸附即可继续输入 |
| 指纹 | FPC1264 / libfprint TOD / fprintd | ✅ 可用 | 在「设置 → 用户」录入，用于锁屏解锁和开机登录；目前支持第一个账户（UID 1000）的一枚指纹，见[指纹说明](docs/FINGERPRINT.md) |
| 保护套开合 | GPIO / SW_LID | ✅ 可用 | 合上保护套时按系统电源设置挂起 |

暂不支持：手写笔。

### 无线与 USB

| 功能 | 器件 / 实现 | 状态 | 说明 |
|---|---|---|---|
| Wi-Fi | Qualcomm QCA6490 / ath11k | ✅ 可用 | 2.4 GHz 与 5 GHz 客户端连接 |
| 蓝牙 | QCA6490 / hci_qca / BlueZ | ✅ 可用 | 配对与日常使用 |
| USB 设备模式 | Synopsys DWC3 / USB 网络（NCM） | ✅ 可用 | 开发者模式下经 USB 线 SSH 登录，默认关闭 |
| USB OTG / 主机模式 | USB-C 数据角色切换 / UCSI | ✅ 可用 | 自动切换为主机模式，可接有线鼠标等 USB 输入设备 |
| USB-C 外接显示 | DisplayPort Alt Mode / FSA4480 | ✅ 可用 | 外接显示器输出画面，拔出后恢复内屏，接回电脑后恢复 USB |

暂不支持：Wi-Fi 热点。

### 音视频与传感器

| 功能 | 器件 / 实现 | 状态 | 说明 |
|---|---|---|---|
| 四扬声器 | 4 × Cirrus Logic CS35L41 / AudioReach | ✅ 可用 | 立体声播放与音量控制，使用本机出厂校准 |
| 内置麦克风 | WCD938x / SoundWire / LPASS / ADSP | ✅ 可用 | 单声道 48 kHz 录音 |
| H.264 视频硬解 | Qualcomm Iris2 / V4L2 | ✅ 可用 | 通过 V4L2 接口的硬件解码 |
| 后置摄像头 | S5KJN1 / Qualcomm CAMSS / libcamera 软件图像处理 | 🟡 部分支持 | 「相机」应用可预览、拍照、录像；软件图像处理，无自动对焦，录像不流畅，见[相机](docs/CAMERA.md) |
| 加速度计 / 自动旋转 | SLPI / SSC / iio-sensor-proxy | ✅ 可用 | 登录界面与桌面随方向自动旋转，装着磁吸键盘时保持横屏 |

暂不支持：前置摄像头、陀螺仪与磁力计、环境光传感器与自动亮度、其他格式的视频硬解、浏览器视频硬解、硬件视频编码。

### 电源与时间

| 功能 | 器件 / 实现 | 状态 | 说明 |
|---|---|---|---|
| 电源键 / 音量键 | Qualcomm PMIC / GPIO 输入 | ✅ 可用 | 灭屏、亮屏、电源菜单与音量调节 |
| 电池状态 / 充电 | qcom_battmgr / UPower | ✅ 可用 | 电量与充电状态显示，墙充充电 |
| 快充 | MiPPS / USB PD PPS | ✅ 可用 | 通过小米认证的充电器和第三方 USB PD PPS 充电器默认走高压快充通路。普通 PD 使用固定电压档位。认证失败会自动重试。见[快充](docs/FASTCHARGE.md) |
| 电脑 USB 供电 | USB 供电路径 | 🟡 部分支持 | 供电功率有限，高负载时电量可能下降 |
| 关机充电 | 启动阶段充电模式 | 🟡 部分支持 | 显示电量和充电趋势；低电量时留在充电界面。电量至少 40%、电压至少 3.7 V 时，可短按电源键开机，长按 2 秒进入 Fastboot |
| 待机 | suspend-to-idle | 🟡 部分支持 | 用电池时空闲 15 分钟自动挂起；挂起后保持睡眠，定时唤醒后正常恢复；待机功耗还未优化 |

暂不支持：休眠到磁盘、硬件时钟写入（启动后联网校时；离线开机时从上次关机时的时间继续）。

## 使用与维护

每天匿名检查一次 GitHub 上的最新版本，有新版时提示；可在通知设置中关闭。

Ubuntu 软件包通过 APT 管理。项目内核与设备组件随安装包整体更新：升级时用新版安装包执行保留 `/home` 的重装，见[升级已安装的系统](docs/INSTALL-TESTING.md#升级已安装的系统)。不要混用不同版本的启动镜像和系统组件。

**开发者模式。** `sudo liuqin-devmode on` 开启经 USB 线的 root SSH 登录，默认关闭，只接受密钥登录；安装时加 `--enable-rescue` 会写入主机的公钥。主机上用 `python3 tools/liuqin-devmode-ssh.py root` 连接，用完执行 `sudo liuqin-devmode off`。

如需在 Ubuntu 中运行 Android 应用，可使用 Waydroid；所需内核配置已内置，
见 [Waydroid 支持](docs/WAYDROID.md)。

充电功率由厂商充电器认证路径协商，见 [快充](docs/FASTCHARGE.md)。

遇到问题时，请提供设备型号、系统版本、复现步骤和相关日志，并通过 GitHub Issues 反馈。
提交日志前，请移除密码、网络凭据和个人信息。使用交流也可通过顶部的公众号与小红书进行。

## 开发与贡献

| 仓库 | 内容 |
|---|---|
| xiaomipad-6pro-mainline | 设备配置、用户态适配、构建与安装工具、文档 |
| [linux-sm8450-liuqin](https://github.com/yzddmr6/linux-sm8450-liuqin) | 完整 Linux 内核源码与设备适配提交 |

构建使用的内核提交记录在 [kernel/source.json](kernel/source.json)。
内核分支为 `liuqin-6.17`。欢迎改进驱动、构建工具与文档，提交方式见
[参与贡献](CONTRIBUTING.md)。

## 致谢与许可证

本项目基于 [sm8450-mainline](https://github.com/sm8450-mainline/linux) 的内核工作，
并使用 Ubuntu、GNOME、Freedreno 和 Linux Qualcomm 社区的成果。

除文件另有声明外，项目原创代码采用 MIT 许可证。Linux 内核及第三方组件保留各自许可证；
固件适用其权利人的授权条款。详见 [LICENSE](LICENSE) 与 [NOTICE](NOTICE)。
