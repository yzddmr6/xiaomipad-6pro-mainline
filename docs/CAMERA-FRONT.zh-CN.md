# 前置摄像头调试与双摄同时取流

> **状态：实验性，未进入发布镜像。** 前置摄像头需要的内核改动不在 `kernel/source.json` 所固定的 `linux-sm8450-liuqin` 提交里，发布镜像也只提供后置摄像头。本文记录在 12 GB/512 GB 机器上验证过的调试过程，以及启动这类内核后前后双摄的用法。

小米平板 6 Pro 有两颗用户可见的摄像头：前置索尼 IMX596、后置三星 S5KJN1。项目内核通过 `qcom-camss` 和 libcamera 的软件 ISP 支持后摄。让前摄出图主要是补驱动和设备树节点；而要让两颗传感器**同时**取流，还需要各自独立的 CSID/VFE 实例。

## 可用能力

2026 年 10 月在 Ubuntu 26.04、`6.17.0-rc1-gfe81794b5e1b` 加下述本地改动的环境中实测：

| 能力 | 前摄（IMX596） | 后摄（S5KJN1） |
| --- | --- | --- |
| libcamera 枚举 | ✅ 显示为 `imx596` | ✅ 显示为 `Internal back camera` |
| 1920 x 1080 拍照 | ✅ | ✅ |
| 2592 x 1472 拍照 | ✅ | ✅ |
| 1280 x 720 录像 | ✅ 29.9 fps | ✅ 29.5 fps |
| 1920 x 1080 录像 | ✅ | ✅ |
| 双摄同时取流 | ✅ 各 1920 x 1080 @30 fps | ✅ |
| 前后反复切换 | ✅ 10 次全部成功 | ✅ |
| 全分辨率（5184 x 3904 / 4072 x 3060） | ❌ 超时 | ❌ 空白帧 |

## 硬件

以下信息来自原厂运行时设备树和原厂 ROM 里的 sensor module 容器。

| | 前摄 | 后摄 |
| --- | --- | --- |
| 传感器 | 索尼 IMX596，2000 万像素，5184 x 3904 | 三星 S5KJN1，5000 万像素，有效 4080 x 3060 |
| CCI 总线 | CCI0，bus 0，地址 `0x10` | CCI0，bus 1，地址 `0x10` |
| CSIPHY | 2 | 3 |
| MCLK | `CAM_CC_MCLK5_CLK`，GPIO 105 | `CAM_CC_MCLK2_CLK`，GPIO 102 |
| 复位 | GPIO 127，低有效 | GPIO 126，低有效 |
| 供电 | 1.1 V（GPIO 74）、2.9 V（GPIO 119）、`pm8350c_l1` | 1.1 V（GPIO 85）、2.8 V（GPIO 52）、`pm8350c_l1` |
| Lane | 4 条，678.4 MHz link 频率 | 4 条，700 MHz link 频率 |
| 对焦 | 定焦 | GT9764 VCM，主线未接入 |

景深摄像头（SmartSens SC202CS，CCI1，地址 `0x36`）也在原厂图里，但未启用。

## 为什么默认没有前摄

1. **主线没有驱动。** 上游 Linux 和项目内核都没有 IMX596 驱动。验证用的 V4L2 subdev 驱动是从原厂 `com.qti.sensormodule.liuqin_aac_imx596_front_i` 容器里的 mode table 生成的，寄存器序列、PLL 和 lane 映射都来自该二进制。
2. **设备树没有节点。** 前摄需要自己的 CCI 节点、MCLK/复位 pinctrl、供电和 CSIPHY endpoint；发布镜像的设备树只描述了后摄。
3. **一个 CSID 不够两颗摄像头用。** 固定提交的内核只实例化 1 个 CSID 和 1 个 VFE。CSID 只有一个 sink pad，第一颗传感器占住链路后第二颗就无法接入；两颗传感器都绑定后，连顺序使用也不稳定。验证用的内核实例化了 3 组 CSID/VFE，并把 CSIPHY *i* 接到 CSID *i* % 3、CSID *i* 接到 VFE *i*。

## 内核前提

验证用的内核与固定提交的差异如下。只用后摄时都不需要这些改动。

- `qcom-camss`：`qcom,sm8475-camss` 使用的资源表实例化 3 个 CSID 和 3 个 VFE，并增加一对一的 CSIPHY/CSID/VFE 建链函数。前摄走 CSIPHY2 -> CSID2 -> VFE2，后摄走 CSIPHY3 -> CSID0 -> VFE0。
- `qcom-camss`：`csid_link_setup()` 要允许重复使能同一条链路——libcamera 每次 configure 都会重新使能；但另一个传感器抢同一个 CSID 仍必须返回 `-EBUSY`。
- `qcom-camss`：`vfe_pm_domain_on()` 要先 resume IFE 电源域，再调用 `device_link_add(..., DL_FLAG_RPM_ACTIVE)`，并保留这个引用。否则电源域关闭过一次之后，下一次开流会失败并报 `Failed to power up pipeline: -22`。
- 设备树：前摄节点、额外的 CSID/VFE 寄存器与中断范围、两颗传感器的 CSIPHY endpoint，以及前摄供电和 pinctrl 状态。

前摄节点在设备树里，所以只换模块不够，需要按[构建指南](BUILD.zh-CN.md)重新生成 DTB 和 boot 镜像。

## 确认调试已生效

```sh
cam -l
```

期望输出：

```text
Available cameras:
1: 'imx596' (/base/soc@0/cci@ac15000/i2c-bus@0/camera@10)
2: Internal back camera (/base/soc@0/cci@ac15000/i2c-bus@1/camera@10)
```

前摄显示为 `imx596` 而不是「Internal front camera」，是因为驱动没有提供 camera location 属性；依赖该属性的应用可能不会把它标成前摄。

查看媒体图：

```sh
media-ctl -p -d /dev/media0
```

前后摄应各占一个 CSID：

```text
msm_csiphy2 -> msm_csid2 [ENABLED] -> msm_vfe2
msm_csiphy3 -> msm_csid0 [ENABLED] -> msm_vfe0
```

video 节点编号不固定，用 `media-ctl -p` 找到 `msm_vfe2_video0` 和 `msm_vfe0_video0` 对应的节点，不要写死 `/dev/videoN`。

如果已有进程在占用某颗摄像头（例如虚拟摄像头服务），先停掉再枚举。否则 libcamera 使能链路会失败，前摄会从列表里消失。

## 拍照

静态照片建议走 GStreamer；`cam --file` 的问题见[已知限制](#已知限制)。

```sh
FRONT=/base/soc@0/cci@ac15000/i2c-bus@0/camera@10
REAR=/base/soc@0/cci@ac15000/i2c-bus@1/camera@10

timeout -s INT 6 gst-launch-1.0 -e libcamerasrc camera-name="$FRONT" \
  ! video/x-raw,width=2592,height=1472 ! videoconvert ! jpegenc \
  ! multifilesink location=/tmp/front-%02d.jpg max-files=1
```

pipeline 会一直运行，由 `timeout` 或 Ctrl-C 结束；`max-files=1` 只保留一张完整的 JPEG，即最新的一帧。后摄把 `$FRONT` 换成 `$REAR`。

只做采集测试、不落文件时也可以用 `cam`（`-c1`、`-c2` 按上面的枚举顺序）：

```sh
cam -c1 --stream width=1920,height=1080 --capture=3   # 前摄
cam -c2 --stream width=1920,height=1080 --capture=3   # 后摄
```

## 录像

```sh
timeout -s INT 10 gst-launch-1.0 -e -q libcamerasrc camera-name="$FRONT" \
  ! video/x-raw,width=1280,height=720,framerate=30/1 ! videoconvert \
  ! x264enc speed-preset=ultrafast bitrate=4000 tune=zerolatency \
  ! mp4mux ! filesink location=/tmp/front.mp4
```

必须加 `-e` 并正常中断，`mp4mux` 才会写入索引；否则文件无法播放。

## 双摄同时取流

两颗摄像头必须**在同一个进程里**打开。两个独立进程（例如两次 `cam`）会互相抢媒体链路，第二个报 `Failed to setup link ... Device or resource busy`。

```sh
gst-launch-1.0 -e \
  libcamerasrc camera-name="$FRONT" \
    ! video/x-raw,width=1920,height=1080,framerate=30/1 ! videoconvert \
    ! x264enc speed-preset=ultrafast bitrate=4000 tune=zerolatency \
    ! mp4mux ! filesink location=/tmp/dual-front.mp4 \
  libcamerasrc camera-name="$REAR" \
    ! video/x-raw,width=1920,height=1080,framerate=30/1 ! videoconvert \
    ! x264enc speed-preset=ultrafast bitrate=4000 tune=zerolatency \
    ! mp4mux ! filesink location=/tmp/dual-rear.mp4
```

实测 8.6 秒：前摄 253 帧、后摄 259 帧，各约 30 fps。1280 x 720 的 CPU 余量更大，系统繁忙时更稳。

## 在应用里使用

枚举 libcamera 摄像头的应用（如 `cam -l`）能看到两颗。只认 V4L2 的应用需要一个回环设备，这种场景通常一次只喂一颗：

```sh
sudo modprobe v4l2loopback video_nr=20 exclusive_caps=0 card_label=Camera
gst-launch-1.0 libcamerasrc camera-name="$FRONT" \
  ! video/x-raw,width=1280,height=720,framerate=30/1 \
  ! videoconvert ! video/x-raw,format=YUY2 ! v4l2sink device=/dev/video20
```

`exclusive_caps=0` 让设备节点在重启 feeder 时仍然存在，应用不会把摄像头判成拔出。切换前后摄时停掉 feeder，用另一个 `camera-name` 重新启动即可。

## 已知限制

- **全分辨率不可用。** 5184 x 3904 一帧约 80 MB，长流会超时；后摄的 4072 x 3060 模式返回空白帧。实用上限是 2592 x 1472。
- **30 fps 封顶。** libcamera 的软件 ISP 在 CPU 上处理，1080p30 单流和双流都可以，但 2592 x 1472 录像会掉到约 10 fps，CPU 占用很高。没有可用的硬件 H.264 编码器。
- **没有自动对焦。** 前摄定焦；后摄的 GT9764 VCM 没有接主线驱动。
- **没有方向元数据。** 前摄驱动没有提供 rotation 属性，预览方向可能需要手动纠正：`cam -o rot180`，或在 GStreamer 里加 `videoflip`。
- **`cam --file` 可能写出全白帧。** 这块平台上 CPU 可能在 ISP 写入可见之前读到 DMA-BUF，PPM 文件全是 `0xff`。GStreamer 会拷贝帧，不受影响。
- **IFE 电源域常开。** `vfe_pm_domain_on()` 的绕法会保留一个运行时 PM 引用，空闲时多消耗几十毫瓦。

## 排障

- **`cam -l` 里没有前摄：** 确认驱动和设备树节点都在（`lsmod | grep qcom_liuqin_sensors`、`dmesg | grep -i imx596`），并停掉正在取流的进程。
- **`Failed to setup link ... Device or resource busy`：** 摄像头或链路被别的进程占用。先停掉它；双摄不要用两个独立进程。
- **`Failed to power up pipeline: -22`：** 缺 VFE 电源域的绕法，或电源域已经处于坏状态。重启可恢复。
- **`i2c-qcom-cci ... timeout`：** 传感器没有上电。检查供电和复位 GPIO，先重启再怀疑驱动。
- **照片全白：** 见上面的 DMA-BUF 说明，改用 GStreamer 或「相机」应用，不要用 `cam --file`。

## 来源

IMX596 的 mode table 从原厂 ROM 的 sensor module 容器重建，不分发任何原厂二进制。驱动和板级适配是为这次调试编写的，未进入上游；部分工作借助了 AI 辅助开发。后摄 S5KJN1 驱动沿用[相机页面](CAMERA.zh-CN.md)所述的上游衍生驱动。
