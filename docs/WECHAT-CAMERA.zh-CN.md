# 微信调用摄像头与通话中切换前后摄

> **状态：已在 12 GB/512 GB 机器上验证。** 微信 4.1.13.26（arm64）能把本文的虚拟摄像头识别为 **Camera**，通话中点击桌面图标即可切换前后摄，通话不会断开；画面会冻结约两秒。

微信 Linux 客户端只枚举 V4L2 视频设备。平板上的 libcamera 摄像头是 media controller 的 subdev，不是普通的 `/dev/videoN` 节点，所以微信无法直接打开。解决办法是用一个小型 GStreamer feeder 把当前传感器画面送进 `v4l2loopback` 设备，微信再把它当普通 USB 摄像头使用。

```text
IMX596（前摄）─┐
                ├─ libcamera 软件 ISP ─ GStreamer ─ /dev/video20 ─ 微信
S5KJN1（后摄）─┘                             （"Camera"）
```

## 验证结果

| 能力 | 结果 |
| --- | --- |
| 微信能看到虚拟摄像头 | ✅ 显示为 **Camera** |
| 前摄视频通话 | ✅ |
| 后摄视频通话 | ✅ |
| 通话中切换前后摄 | ✅ 通话保持，冻结约 2 秒 |
| feeder 重启期间设备节点仍在 | ✅ 依赖 `exclusive_caps=0` |
| 提供给微信的分辨率/格式 | 1280 x 720 YUY2 @30 fps |

feeder 对后摄做了水平翻转；如果应用里画面是镜像的，删掉 `videoflip` 元素即可。

## 为什么必须 `exclusive_caps=0`

`exclusive_caps=1` 时，没有生产者推流 v4l2loopback 就会删除设备节点。微信会把这种情况当成摄像头被拔出，停止使用。`exclusive_caps=0` 让 `/dev/video20` 在 feeder 重启期间一直存在，通话端只看到短暂的帧中断。

## 安装微信

使用官方 arm64 包，验证版本为 4.1.13.26。

```sh
sudo apt install ./WeChatLinux_arm64.deb
```

登录桌面后从应用列表启动微信。它需要图形会话；在没有 `DISPLAY`、`WAYLAND_DISPLAY`、`XDG_RUNTIME_DIR` 的 SSH shell 里直接跑 `/usr/bin/wechat` 会 abort。

## 安装 v4l2loopback

验证机上是按 `6.17.0-rc1-gfe81794b5e1b` 编译的 v4l2loopback 0.15.4，模块放在 `/lib/modules/$(uname -r)/extra/`。可以按当前内核从源码编译，也可以直接用发行版包：

```sh
sudo apt install v4l2loopback-dkms
```

内核升级后要先重新编译模块，再启动服务。

## Feeder 脚本

保存为 `/usr/local/bin/liuqin-vcam-feed` 并赋予执行权限。脚本会等待传感器出现，根据状态文件选择前摄或后摄，把 1280 x 720 YUY2 送进 `/dev/video20`。

```sh
#!/bin/sh
# Feed the selected sensor into /dev/video20.
export LC_ALL=C

# The tested unit keeps a local libcamera build here; drop these two lines if
# libcamera is installed system-wide.
export LD_LIBRARY_PATH=/opt/libcamera-local/lib/aarch64-linux-gnu
export LIBCAMERA_IPA_MODULE_PATH=/opt/libcamera-local/lib/aarch64-linux-gnu/libcamera/ipa

STATE=/etc/liuqin-vcam-source
[ -f "$STATE" ] || echo front > "$STATE"
which=$(cat "$STATE" 2>/dev/null)
[ "$which" = "back" ] || which=front

if [ "$which" = "back" ]; then
    NAME="/base/soc@0/cci@ac15000/i2c-bus@1/camera@10"
    FLIP="! videoflip method=horizontal-flip"
    SENSOR=s5kjn1
else
    NAME="/base/soc@0/cci@ac15000/i2c-bus@0/camera@10"
    FLIP=""
    SENSOR=imx596
fi

# Wait for the sensor to bind; libcamera would otherwise fail to open it.
i=0
while [ $i -lt 100 ]; do
    for n in /sys/bus/i2c/devices/*/name; do
        if [ "$(cat "$n" 2>/dev/null)" = "$SENSOR" ]; then
            i=999
            break
        fi
    done
    [ $i -eq 999 ] && break
    i=$((i + 1))
    sleep 0.1
done

exec /usr/bin/gst-launch-1.0 -e -q libcamerasrc camera-name="$NAME" \
    ! video/x-raw,width=1280,height=720,framerate=30/1 \
    ! videoconvert ${FLIP} \
    ! video/x-raw,format=YUY2 \
    ! v4l2sink device=/dev/video20
```

`camera-name` 是 `cam -l` 输出的 libcamera 路径，重启后保持不变；`media-ctl -p` 里的 I2C client 编号则会变。

## systemd 服务

保存为 `/etc/systemd/system/liuqin-vcam.service`：

```ini
[Unit]
Description=Liuqin virtual camera (/dev/video20) fed from the selected sensor
After=graphical.target

[Service]
Type=simple
ExecStartPre=/sbin/modprobe v4l2loopback video_nr=20 exclusive_caps=0 card_label="Camera"
ExecStart=/usr/local/bin/liuqin-vcam-feed
Restart=always
RestartSec=2
TimeoutStopSec=5

[Install]
WantedBy=graphical.target
```

然后：

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now liuqin-vcam.service
```

模块参数也可以放到 `/etc/modprobe.d/v4l2loopback.conf`：

```text
options v4l2loopback video_nr=20 exclusive_caps=0 card_label="Camera"
```

服务里的 `modprobe` 行建议保留：参数更直观，也保证 feeder 启动前模块已加载。

## 切换脚本

保存为 `/usr/local/bin/liuqin-cam-switch`：

```sh
#!/bin/sh
# Switch the virtual camera between the front and rear sensor.
export LC_ALL=C
STATE=/etc/liuqin-vcam-source

cur=$(cat "$STATE" 2>/dev/null)
case "$cur" in
    front) next=back ;;
    back)  next=front ;;
    *)     next=front ;;
esac

echo "$next" > "$STATE"
systemctl restart liuqin-vcam.service

# Wait for the new feeder process (max ~4 s).
i=0
while [ $i -lt 40 ]; do
    pid=$(systemctl show liuqin-vcam.service -p MainPID --value)
    if [ -n "$pid" ] && [ "$pid" != "0" ] && [ -d "/proc/$pid" ]; then
        break
    fi
    i=$((i + 1))
    sleep 0.1
done

if [ "$next" = "front" ]; then
    msg="已切换到：前摄"
else
    msg="已切换到：后摄"
fi
echo "$msg"
command -v notify-send >/dev/null 2>&1 && notify-send -t 1500 "虚拟摄像头" "$msg" 2>/dev/null
exit 0
```

脚本只等 feeder 进程出现；第一帧会稍晚一点，这就是通话中切换时看到的冻结。

### 免密 sudo

切换脚本要重启系统服务，因此需要 root 权限。只放行这一个脚本，不要放行通用 shell：

```sh
echo "$USER ALL=(root) NOPASSWD: /usr/local/bin/liuqin-cam-switch" |
    sudo tee /etc/sudoers.d/liuqin-cam-switch
sudo chmod 0440 /etc/sudoers.d/liuqin-cam-switch
```

如果命令不是在桌面账号下执行，把 `$USER` 换成实际的桌面用户名。

### 桌面图标

保存为 `~/Desktop/switch-camera.desktop`：

```ini
[Desktop Entry]
Type=Application
Name=Switch camera
Comment=Switch the virtual camera during a WeChat call
Exec=sudo -n /usr/local/bin/liuqin-cam-switch
Terminal=false
Icon=camera-switch
Categories=Utility;AudioVideo;
StartupNotify=false
```

标记为可信，否则 GNOME 会把它当成文本文件：

```sh
gio set ~/Desktop/switch-camera.desktop metadata::trusted true
chmod +x ~/Desktop/switch-camera.desktop
```

启动器必须写 `Terminal=false`；验证机上没有装终端程序，`Terminal=true` 会导致点击无反应。

## 在微信里使用

1. 确认虚拟摄像头存在：

   ```sh
   v4l2-ctl --list-devices
   ```

   期望输出：

   ```text
   Camera (platform:v4l2loopback-020):
       /dev/video20
   ```

2. 打开微信，进入 **设置 → 视频**，摄像头选择 **Camera**。
3. 发起或接听视频通话。
4. 点击桌面上的 **Switch camera** 图标切换镜头。通话保持连接；画面冻结约两秒，然后切换到另一颗摄像头。

如果桌面图标不可用，也可以在桌面会话的终端里执行：

```sh
sudo -n /usr/local/bin/liuqin-cam-switch
```

## 说明与限制

- **feeder 常驻运行。** 1280 x 720 下软件 ISP 大约占满一个核（`systemctl status` 里约 70%），即使微信没开也一样。不用时可以停掉：

  ```sh
  sudo systemctl stop liuqin-vcam.service
  ```

  停掉后微信就看不到摄像头，重新用 `systemctl start` 启动。

- **格式固定。** feeder 只提供 1280 x 720 YUY2 @30 fps，微信会自行缩放；要换格式得改 pipeline。
- **切换不是无缝的。** feeder 会重启，画面中断约两秒。微信通话不断，但对方会看到短暂卡顿。
- **启动顺序有影响。** 如果应用在 feeder 重启间隙打开 `/dev/video20`，`VIDIOC_STREAMON` 可能返回 `EIO`，预览黑屏。关掉摄像头再打开，或在应用里重新选择 **Camera**。
- **只有一个生产者。** 同时只能有一个 feeder 写 `/dev/video20`，第二个 `gst-launch` 会失败；多个应用可以同时读。
- **内核升级。** v4l2loopback 是树外模块；换内核后要重新编译（或重装 DKMS 包），否则服务起不来。

## 排障

- **微信里没有 Camera：** 检查 `ls -l /dev/video20` 和 `systemctl status liuqin-vcam.service`。如果服务在反复重启，先停掉再前台运行：

  ```sh
  sudo systemctl stop liuqin-vcam.service
  sudo /usr/local/bin/liuqin-vcam-feed
  ```

- **微信预览黑屏：** feeder 可能因为传感器被占用而失败（「相机」、`cam`、其他虚拟摄像头服务）。停掉占用者，再重启 `liuqin-vcam.service`。
- **切换没反应：** 检查 sudoers 文件和脚本执行权限。规则缺失时 `sudo -n /usr/local/bin/liuqin-cam-switch` 会直接报错，而不是提示输密码。
- **画面是镜像的：** 删掉 feeder 后摄分支里的 `videoflip` 元素。

## 来源

这些脚本是围绕 v4l2loopback（GPL-2.0）和 GStreamer 的板级胶水。libcamera 路径和后摄翻转沿用本机摄像头调试的结果。微信本身不属于本项目，使用小米官方 arm64 包安装。
