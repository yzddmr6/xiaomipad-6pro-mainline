# liuqin 双摄预览版

[English](CAMERA-PREVIEW.en.md) | [已发布的相机支持](CAMERA.md)

预览版组合了 [IMX596 前摄内核 Draft][kernel-pr]、现有 S5KJN1 后摄、
前摄 libcamera 配置和 [PipeWire GPU 软件 ISP 配置][gpu-pr]。预览版使用
内核里的静态板级设备树。此前的真机结果来自本地运行时 overlay，静态设备树
方案仍待启动和采集验证。

## 构建匹配的预览安装包

在 Ubuntu 24.04 构建机上，按[构建说明](BUILD.md)安装依赖并准备输入文件。
在本集成仓库的预览分支目录下，获取配置锁指定的公开内核提交。装配步骤使用构建说明中生成的
`out/image-inputs.local.json`：

```sh
kernel=../linux-sm8450-liuqin-camera
sha=$(python3 -c 'import json; print(json.load(open("kernel/source.json"))["commit"])')
git init "$kernel"
git -C "$kernel" fetch https://github.com/beiwater/linux-sm8450-liuqin.git "$sha"
git -C "$kernel" checkout --detach FETCH_HEAD

python3 tools/build-liuqin-kernel.py --source "$kernel" --out out/camera-kernel --configure-only
grep -Fx CONFIG_VIDEO_IMX596=m out/camera-kernel/.config
python3 tools/build-liuqin-kernel.py --source "$kernel" --out out/camera-kernel
find out/camera-kernel/modules -name 'imx596.ko*'

sudo python3 tools/build-liuqin-image.py --inputs out/image-inputs.local.json \
  --kernel-source "$kernel" --kernel-out out/camera-kernel --out out/camera-preview
python3 tools/install-liuqin.py --bundle out/camera-preview/bundle --check
```

在测试机上使用同一套构建产物，并按项目的[安装步骤](INSTALL-TESTING.md)操作。
如果旧系统装过相机 bring-up 服务，请用干净的预览版系统测试，避免旧服务重新
加载运行时 overlay。保留项目原有的相机启动入口。

## 真机复现

1. 启动后关闭 Snapshot，执行 `cam -l`，确认 IMX596 前摄和 S5KJN1 后摄都
   能枚举。分别用 `LIBCAMERA_SOFTISP_MODE=gpu LIBCAMERA_LOG_LEVELS='*:INFO,Debayer:DEBUG' cam -c 编号 --capture=30`
   抓帧，核对实际收到的帧和 EGL 去马赛克日志。环境变量或 EGL 初始化本身
   不能证明图像帧经过了 GPU 后端。
2. 打开 GNOME Snapshot，分别切换、预览、拍照和录像回放。使用发行版自带的
   编码器，记录实际分辨率、方向和色彩。用
   `systemctl --user show pipewire.service -p Environment` 检查 PipeWire 配置。
3. 对照[英文页的固定 720p 命令](CAMERA-PREVIEW.en.md#verify-capture-and-recording)，
   用发行版 x264 分别录制前后摄的同一运动场景，并切换 CPU/GPU ISP 各测一遍。
   用 `ffprobe` 对比实际解码帧数、时长和帧时间戳间隔。

此前本地配置的 720p 样本记录为前后摄各 5 秒、150 帧。本预览包配合发行版
编码器的录像表现仍待验证。

[kernel-pr]: https://github.com/yzddmr6/linux-sm8450-liuqin/pull/15
[gpu-pr]: https://github.com/yzddmr6/xiaomipad-6pro-mainline/pull/33
