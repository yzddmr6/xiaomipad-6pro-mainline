# 相机

小米平板 6 Pro 的后置摄像头（三星 S5KJN1，5000 万像素）以实验功能提供：可以在发行版自带的「相机」应用（GNOME Snapshot）里预览、拍照和录像。图像处理由 libcamera 的软件 ISP 在 CPU 上完成，没有校准过的调校参数，没有自动对焦，也不使用硬件编码。前置摄像头未在发布镜像中启用；包含前后双摄同时取流的实验性调试见[前置摄像头调试与双摄同时取流](CAMERA-FRONT.zh-CN.md)。

| 功能 | 状态 |
| --- | --- |
| 后摄预览与拍照 | 🟡 可用，画面比原厂系统偏软 |
| 后摄录像与回放 | 🟡 可用，运动画面有卡顿 |
| 自动对焦、校准调校、其他传感器模式 | ❌ 未实现 |
| 前置摄像头 | ❌ 发布镜像未启用；实验性调试见[前置摄像头](CAMERA-FRONT.zh-CN.md) |
| 硬件视频编码 | ❌ 未使用 |

## 验证状态

贡献者在 liuqin 平板上（Ubuntu 26.04，用 `fastboot boot` 临时启动相机内核）完成了以下测试：

- 10 次采集启停循环，每次结束后 TITAN_TOP 与 IFE0 电源域都回到关闭状态；
- 连续 300 帧 4080 x 3060 RAW 采集，平均帧间隔 33.3 ms；
- 在 Snapshot 里拍摄真实场景的 1920 x 1080 JPEG 照片、录制 10 秒 MP4 并在应用内回放，多次关闭重开均正常；
- 预览方向正确。

最终合入的内核在测试版本之上加了审查清理：相机时钟控制器的注释修正与死代码删除、改用 `qcom,sm8475-camss` 兼容串、删除用不到的设备树节点。这些清理不改变已测试的驱动行为。项目自己构建的完整镜像尚未带相机实际启动验证，欢迎在 issue 里反馈结果。

测试使用的是产品启动参数，其中包含 `clk_ignore_unused` 和 `pd_ignore_unused`。去掉这两个参数后相机所需的时钟和电源域是否完整，目前还没有验证。

## 组件

| 部分 | 作用 |
| --- | --- |
| 内核：`camcc-sm8450`（`qcom,sm8475-camcc`） | 相机时钟与电源域；SM8475 上保留 GDSC 状态 |
| 内核：`i2c-qcom-cci` | 相机控制总线（CCI0） |
| 内核：`qcom-camss`（`qcom,sm8475-camss`） | CSIPHY3、CSID0、IFE0，即后摄用到的子集 |
| 内核：`s5kjn1` | 传感器驱动，19.2 MHz 时钟，4080 x 3060 RAW10 30 fps |
| `device/configs/liuqin-camera.config` | 启用以上模块和系统 DMA-BUF heap，在模块裁剪之后应用 |
| `/etc/modprobe.d/liuqin-camera.conf` | 先加载时钟控制器，再加载 CAMSS |
| `/etc/udev/rules.d/70-liuqin-camera-heap.rules` | 允许 video 组和当前登录用户访问 `/dev/dma_heap/system`，软件 ISP 从这里分配缓冲区 |
| `/usr/local/bin/liuqin-camera` | 启动 Snapshot，并为它重新打开 DMA-BUF 导入（见下文） |
| `/usr/local/share/applications/org.gnome.Snapshot.desktop`、`/usr/local/share/dbus-1/services/org.gnome.Snapshot.service` | 让「相机」图标和 D-Bus 激活都经过 `liuqin-camera` 启动 |

镜像还预装了 `gnome-snapshot`、`libcamera-ipa`、`gstreamer1.0-libcamera`、`gstreamer1.0-plugins-bad`（`h264parse`）、`gstreamer1.0-plugins-ugly`（录像用的 H.264 编码器）、`gstreamer1.0-libav`（回放用的 H.264 解码器），以及[验证](#验证)一节用到的诊断工具 `libcamera-tools`（`cam`）和 `v4l-utils`（`media-ctl`、`v4l2-ctl`）。它们只是镜像内容，不是 `liuqin-device-support` 的依赖，卸载它们不会影响设备支持包。

## Snapshot 的 DMA-BUF 导入

在这台设备上，GTK 直接导入 DMA-BUF 作为纹理时可能读到旧数据：CPU 写入的内容还没对 GPU 可见，GPU 就开始读，画面显示为噪点。因此 `/etc/environment.d/50-liuqin-dmabuf.conf` 对所有应用设置了 `GDK_DISABLE=dmabuf`，让 GTK 改为复制一份图像再上传。

Snapshot 的相机预览需要 DMA-BUF 导入，所以 `liuqin-camera` 只为 Snapshot 清除了 `GDK_DISABLE`。相机预览正好是全局规避所针对的模式（软件 ISP 在 CPU 上写帧，GPU 负责显示），因此预览有可能出现花屏，不过测试中没有观察到。保存的照片和视频由 CPU 直接从帧数据编码，不受影响。等内核的缓存一致性问题修复后，全局设置和这个例外会一起移除。

## 使用

从应用列表打开「相机」即可。录像是在关闭 Snapshot 硬件编码的情况下测试的，如果录像失败，请关闭硬件编码：

```sh
gsettings set org.gnome.Snapshot enable-hardware-encoding false
```

## 验证

关闭「相机」后执行：

```sh
cam -l                          # 来自 libcamera-tools，应列出一个 internal back 摄像头
cam -c1 --capture=3             # 采集 3 帧，每帧输出一行
sudo cat /sys/kernel/debug/pm_genpd/pm_genpd_summary | grep -Ei 'titan|ife'
                                # 相机相关电源域应为 off
```

Snapshot 正在预览时不要运行 `cam -l` 或其他相机枚举：libcamera 枚举时会重置传感器的翻转控制，导致当前预览上下颠倒，直到重新打开「相机」。视频节点编号不固定，直接采集测试请用 `media-ctl -p`（来自 v4l-utils）查找 `msm_vfe0_video0`。

## 排障

- **找不到摄像头：** 检查模块是否加载（`lsmod | grep -E 's5kjn1|qcom_camss|camcc'`），并查看 `journalctl -k | grep -Ei 'camss|s5kjn1|cci'`。
- **Snapshot 打开后没有画面：** 确认是从「相机」图标启动的，而不是直接运行 `/usr/bin/snapshot`；并确认用户目录下没有 `~/.local/share/applications/org.gnome.Snapshot.desktop` 或 D-Bus 服务覆盖绕过了 `liuqin-camera`。
- **录下的视频回放黑屏：** 缺少 `gstreamer1.0-libav`。

## 来源

相机支持由 reisa 贡献。S5KJN1 驱动改编自 Vladimir Zapolskiy（Linaro）的上游驱动，提交 [e38fd0933c75](https://github.com/torvalds/linux/commit/e38fd0933c759bfd51aafbee07f11e8d69da4366)，保留了原版权声明和模块作者。liuqin 的 19.2 MHz 模式数据根据设备厂商的相机模组配置重建，不分发任何厂商二进制文件。CSIPHY v2.1.3 通道参数参照高通 GPL-2.0 下游相机驱动，保留了其版权声明。贡献者说明板级适配部分借助了 AI 辅助开发。
