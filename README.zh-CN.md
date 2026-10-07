# Ubuntu for Xiaomi Pad 6 Pro

在 Xiaomi Pad 6 Pro 上运行 Ubuntu 26.04 桌面，采用 GNOME 桌面环境与基于上游 Linux 的设备适配内核。[English](README.md)

## 📮 关注与交流

最新进展动态、使用技巧和 AI 讨论会先在公众号和小红书更新；刷机遇到问题也欢迎反馈。

| 公众号：熵减矩阵 | 小红书：yzddmr6 |
|:---:|:---:|
| <img src="docs/assets/gzh-qr.png" width="200" alt="公众号：熵减矩阵"> | <img src="docs/assets/xhs-qr.jpg" width="200" alt="小红书：yzddmr6"> |
| 进展动态 · 使用技巧 · AI 讨论 | 使用分享 · 问题反馈 |

## ⚠️ 安装前请阅读

本项目仅适用于 **Xiaomi Pad 6 Pro（liuqin，SM8475）**。其他小米平板型号不适用。

- **数据清除**：解锁 Bootloader 和首次安装会清除用户数据，请提前备份。
- **容量与布局**：安装器已在已知的 **256 GB 分区布局**完成真机验证，并不代表所有 256 GB 设备均兼容。其他容量、改过分区的设备未验证，不支持安装。安装器会重新划分磁盘尾部：双系统布局下两个系统都从 A 槽启动，单系统布局把 Ubuntu 安装到 B 槽；不要绕过校验。
- **验证范围**：首次安装、首次启动、旋转、触摸、磁吸键盘（含触摸板）、声音和 USB OTG 主机模式（有线鼠标）已验证；Android 恢复流程尚未完成独立真机验证。
- **安装方式**：安装器提供 Ubuntu 单系统布局与保留原厂 Android 的双系统布局。双系统都从 A 槽启动，切换时把另一系统的 boot 镜像写入 `boot_a`。已知 256 GB 开发机已验证 A 槽 Ubuntu、修复原厂固件后的 Android 首次启动，以及调试 Android 下 KernelSU 模块脚本返回 Ubuntu；管理器 WebUI 按钮、普通 Android 启动下的 KernelSU 自动加载和单系统布局仍未验收。详见[安装步骤](docs/INSTALL-TESTING.zh-CN.md)。
- **恢复准备**：安装前请准备对应的原厂固件，并阅读[数据与恢复说明](docs/FLASHING.zh-CN.md#数据与恢复)。
- **功能限制**：部分硬件功能尚不完整，请先查看下方硬件支持表。

误刷启动镜像或分区可能导致设备无法启动。请按安装指南操作，并保留备份。
出厂校准与设备地址必须来自本机，不可跨设备复制。

## 🚀 获取与安装

请从 [GitHub Releases](https://github.com/yzddmr6/xiaomipad-6pro-mainline/releases) 下载完整安装包，不要混用不同版本的文件。

| 我想要 | 入口 |
|---|---|
| 安装 Ubuntu | [安装指南](docs/FLASHING.zh-CN.md) |
| 自行编译内核和设备组件 | [构建指南](docs/BUILD.zh-CN.md) |
| 恢复 Android | [数据与恢复说明](docs/FLASHING.zh-CN.md#数据与恢复) |

预编译安装包包括匹配的启动镜像、根文件系统、安装工具与校验文件。

## 硬件支持

以下为 Xiaomi Pad 6 Pro（liuqin）的当前硬件支持与已知限制。
器件信息来自本项目已确认的板级资料和设备树；不代表其他批次、容量或配件组合均已验证。

✅ 可用 · 🟡 部分支持 · ❌ 不支持 · 🧪 未验证

v0.6.0 已将 DP 与原生指纹整合到完整安装包，已在已知 256 GB 开发机完成安装、首次设置与功能确认。

### 平台、显示与输入

| 功能 | 器件 / 实现 | 状态 | 范围与限制 |
|---|---|---|---|
| SoC / CPU | Qualcomm Snapdragon 8+ Gen 1（SM8475），ARM64 | ✅ 可用 | 主线内核启动与 Ubuntu 桌面运行；不代表全部节能状态已验证 |
| GPU / 桌面合成 | Adreno 730 / Freedreno / Mesa | ✅ 可用 | 桌面硬件加速；部分应用仍需渲染兼容设置 |
| 内置存储 | UFS / ext4 | ✅ 可用 | 持久系统与软件包安装；安装器仅面向已知 256 GB 布局 |
| 内置显示 | Novatek NT36532 / 双 DSI / DSC | ✅ 可用 | 2880 x 1800，120 Hz；其他刷新率未逐项验证 |
| 手动亮度 | Kinetic KTZ8866 背光控制 | ✅ 可用 | 背光与手动亮度调节 |
| 触控 | Novatek NT36532 / SPI（CSOT 或 TM 面板） | ✅ 可用 | 驱动按面板模块选择固件；点击、滑动与触控手势 |
| 磁吸键盘 | Nanosic WN8030 | ✅ 可用 | 普通按键、音量键、触摸板与重新吸附输入；挂起后恢复未完整覆盖 |
| 指纹 | FPC1264 / libfprint TOD / fprintd | 🟡 已验证 | 原生设置录入、同指通过/异指拒绝、锁屏和正常重启后指纹登录；限一个账号、一枚指纹，见[指纹说明](docs/FINGERPRINT.zh-CN.md) |
| 手写笔 | NVTCapacitivePen 输入接口 | 🧪 未验证 | 坐标、压感、按键及唤醒后的笔输入未实测 |
| 霍尔开关 | GPIO / SW_LID / SW_TABLET_MODE | 🟡 部分支持 | 开关状态可读取；保护套合盖、打开唤醒的整机策略未完整验收 |

### 无线与 USB

| 功能 | 器件 / 实现 | 状态 | 范围与限制 |
|---|---|---|---|
| Wi-Fi 2.4 GHz | Qualcomm QCA6490 / ath11k | ✅ 可用 | 无线连接与日常联网 |
| Wi-Fi 5 GHz | QCA6490，按 WCN6855 系列适配 | ✅ 可用 | 5 GHz 连接已验证；不承诺特定峰值速率 |
| Wi-Fi 热点 / AP | NetworkManager / ath11k | 🧪 未验证 | 当前已确认的联网场景为客户端模式 |
| 蓝牙 | QCA6490 / hci_qca / BlueZ | ✅ 可用 | 日常蓝牙功能可用 |
| USB 2.0 设备模式 | Synopsys DWC3 / NXP eUSB2 repeater | ✅ 可用 | USB NCM 网络与数据传输；当前为 High-Speed 设备模式 |
| USB 充电器切换后重连 | USB-C / USB gadget | 🟡 部分支持 | v0.1.1 起由 UCSI typec 自动协商，可靠性预期改善；专项复测待做 |
| USB 3.x SuperSpeed | USB 控制器 / PHY | 🧪 未验证 | v0.1.1 起 SM8475 PHY 表与控制器已就绪；SuperSpeed 外设枚举未实测 |
| USB OTG / 主机模式 | USB-C 数据角色切换 | ✅ 可用 | v0.1.1 起 UCSI 自动角色协商；有线鼠标实测可用，U 盘/扩展坞待测 |
| USB-C 外接显示 | DisplayPort Alt Mode / FSA4480 | 🟡 已验证 | 真实外屏、拔出恢复内屏、接回电脑恢复 USB 已验证；其他扩展坞、DP 音频和高刷未逐项验收 |

### 音视频与传感器

| 功能 | 器件 / 实现 | 状态 | 范围与限制 |
|---|---|---|---|
| 四扬声器 | 4 × Cirrus Logic CS35L41 / AudioReach | ✅ 可用 | 立体声播放与音量控制，读取本机校准；音质仍在调校 |
| 内置麦克风 | WCD938x AMIC1 经 SoundWire → LPASS TX macro → ADSP codec DMA | ✅ 可用 | UCM "Mic" 设备单声道 48 kHz 录音；本分支 RAM 启动真机验证通过。其余三颗麦克风与降噪尚未接入 |
| H.264 视频硬解 | Qualcomm Iris2 / V4L2 | ✅ 可用 | 已验证用户态硬件解码，不等于浏览器已经接入 |
| 其他视频解码格式 | Iris / V4L2 | 🧪 未验证 | HEVC、VP9 等未完成逐项验证 |
| 浏览器视频硬解 | 浏览器 / V4L2 接入 | 🧪 未验证 | 浏览器能播放视频不等于使用硬件解码 |
| 硬件视频编码 | Qualcomm 视频引擎 | 🧪 未验证 | 尚未验证硬件编码工作流 |
| 后置摄像头 | S5KJN1 / Qualcomm CAMSS / libcamera 软件图像处理 | 🟡 部分支持 | 「相机」应用可预览、拍照、录像；软件图像处理，无自动对焦，录像有卡顿，见[相机](docs/CAMERA.zh-CN.md) |
| 前置摄像头 | 相机传感器 / CAMSS | ❌ 不支持 | 尚未启用或验证 |
| 加速度计 / 自动旋转 | SLPI / SSC / iio-sensor-proxy | ✅ 可用 | 首次启动、登录界面、桌面横竖旋转与磁吸键盘横屏 |
| 陀螺仪 / 磁力计 | SSC 传感器链路 | 🧪 未验证 | 未确认应用可用的测量链路，不随自动旋转标为可用 |
| 环境光传感器 | SSC 光感路径 | 🧪 未验证 | 尚未完成真实光照测量验收 |
| 自动亮度 | 桌面亮度策略 | ❌ 不支持 | 尚未接通自动亮度控制 |

### 电源与时间

| 功能 | 器件 / 实现 | 状态 | 范围与限制 |
|---|---|---|---|
| 电源键 / 音量键 | Qualcomm PMIC / GPIO 输入 | ✅ 可用 | 灭屏、亮屏、电源菜单与音量调整；已验证原生锁屏认证 |
| 电池状态 / 基础充电 | qcom_battmgr / UPower | ✅ 可用 | 电量、充电状态与基础墙充 |
| 电脑 USB 供电 | USB 供电路径 | 🟡 部分支持 | 供电功率有限，高负载时可能仍净放电 |
| 小米私有快充 | 厂商充电协议 | 🟡 部分支持 | 已集成充电器认证；电池认证已验证，PD 适配器可达 9 V / 2 A。MiPPS 与标准 PPS 快充尚待适配器验收 |
| 关机充电 | 启动阶段充电保持 | 🟡 部分支持 | 无完整充电显示界面；关机充电时开机需长按电源键 |
| 挂起 / 恢复 | Linux 电源管理 | 🟡 部分支持 | 基础恢复已验证，外设恢复和深休眠功耗仍需进一步测试 |
| RTC / 离线时间保持 | Qualcomm PMK8350 RTC | 🧪 未验证 | 联网校时可用；不保证离线写入及断电后的时间保持 |

## 使用与维护

Ubuntu 软件包通过 APT 管理。项目内核与设备组件的更新方式将随安装版本说明，
不要混用不同版本的启动镜像和系统组件。

如需在 Ubuntu 中运行 Android 应用，可使用 Waydroid；所需内核配置已内置，
见 [Waydroid 支持](docs/WAYDROID.zh-CN.md)。

充电功率由厂商充电器认证路径协商，见 [快充](docs/FASTCHARGE.zh-CN.md)。

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
