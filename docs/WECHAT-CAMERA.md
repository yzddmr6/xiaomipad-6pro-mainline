# WeChat camera and switching cameras during a call

> **Status: tested on the 12 GB/512 GB unit.** WeChat 4.1.13.26 (arm64) sees the
> virtual camera described here as **Camera**, and the desktop switcher changes
> lenses during a video call without dropping the call. The picture freezes for
> about two seconds while the feed restarts.

WeChat's Linux client only enumerates V4L2 video devices. The libcamera cameras
on this tablet are media-controller subdevices, not plain `/dev/videoN` nodes,
so WeChat cannot open them directly. The fix is a small GStreamer feeder that
copies the selected sensor into a `v4l2loopback` device; WeChat then uses that
device like any USB webcam.

```text
IMX596 (front) ─┐
                ├─ libcamera software ISP ─ GStreamer ─ /dev/video20 ─ WeChat
S5KJN1 (rear) ──┘                             ("Camera")
```

## Tested behaviour

| Capability | Result |
| --- | --- |
| WeChat lists the virtual camera | ✅ as **Camera** |
| Video call with the front camera | ✅ |
| Video call with the rear camera | ✅ |
| Switching front/rear during a call | ✅ call stays up, ~2 s freeze |
| Device stays present while the feeder restarts | ✅ with `exclusive_caps=0` |
| Resolution and format offered to WeChat | 1280 x 720 YUY2 at 30 fps |

The rear feed is flipped horizontally in the feeder. Remove the `videoflip`
element if the image is mirrored in your application.

## Why `exclusive_caps=0`

With `exclusive_caps=1`, v4l2loopback removes the device node whenever no
producer is streaming. WeChat interprets that as the camera being unplugged and
stops using it. `exclusive_caps=0` keeps `/dev/video20` present across the
feeder restart, so the call keeps its capture device and only sees a short gap
in frames.

## Installing WeChat

Use the official arm64 package; the tested version is 4.1.13.26.

```sh
sudo apt install ./WeChatLinux_arm64.deb
```

Start WeChat from the application grid after logging in to the desktop. It
needs the graphical session; starting `/usr/bin/wechat` from an SSH shell
without `DISPLAY`, `WAYLAND_DISPLAY` and `XDG_RUNTIME_DIR` aborts.

## Installing v4l2loopback

The tested unit runs v4l2loopback 0.15.4 built for `6.17.0-rc1-gfe81794b5e1b`
and installed under `/lib/modules/$(uname -r)/extra/`. Either build it from
source for the running kernel or use the distribution package:

```sh
sudo apt install v4l2loopback-dkms
```

After a kernel update, rebuild the module before starting the service.

## Feeder script

Save as `/usr/local/bin/liuqin-vcam-feed` and make it executable. It waits for
the sensor to appear, picks the front or rear camera from the state file, and
feeds 1280 x 720 YUY2 into `/dev/video20`.

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

The `camera-name` strings are the libcamera paths from `cam -l`. They are
stable across reboots; the I2C client numbers in `media-ctl -p` are not.

## Systemd service

Save as `/etc/systemd/system/liuqin-vcam.service`:

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

Then:

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now liuqin-vcam.service
```

The module options can also live in `/etc/modprobe.d/v4l2loopback.conf`:

```text
options v4l2loopback video_nr=20 exclusive_caps=0 card_label="Camera"
```

Keep the `modprobe` line in the unit as well; it makes the options explicit and
loads the module before the feeder starts.

## Switching script

Save as `/usr/local/bin/liuqin-cam-switch`:

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

The script only waits for the feeder process to exist. The first frame arrives
a moment later, which is the freeze WeChat shows during a switch.

### Passwordless sudo

The switcher restarts a system service, so it needs root. Allow only this
script, not a general shell:

```sh
echo "$USER ALL=(root) NOPASSWD: /usr/local/bin/liuqin-cam-switch" |
    sudo tee /etc/sudoers.d/liuqin-cam-switch
sudo chmod 0440 /etc/sudoers.d/liuqin-cam-switch
```

Replace `$USER` with the desktop account if you run the command from another
account.

### Desktop icon

Save as `~/Desktop/switch-camera.desktop`:

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

Mark it trusted, otherwise GNOME shows it as a text file:

```sh
gio set ~/Desktop/switch-camera.desktop metadata::trusted true
chmod +x ~/Desktop/switch-camera.desktop
```

The launcher must have `Terminal=false`; the tested image has no terminal
emulator installed, and `Terminal=true` makes the click do nothing.

## Using it with WeChat

1. Confirm the virtual camera exists:

   ```sh
   v4l2-ctl --list-devices
   ```

   Expected:

   ```text
   Camera (platform:v4l2loopback-020):
       /dev/video20
   ```

2. Open WeChat, go to **Settings → Video**, and select **Camera**.
3. Start or answer a video call.
4. Click the **Switch camera** desktop icon to change lenses. The call stays
   connected; the picture freezes for about two seconds and then continues with
   the other sensor.

If the icon is missing, the same command works from a terminal in the desktop
session:

```sh
sudo -n /usr/local/bin/liuqin-cam-switch
```

## Notes and limitations

- **The feeder runs continuously.** At 1280 x 720 the software ISP keeps roughly
  one core busy (~70% in `systemctl status`), even when WeChat is closed. Stop
  the service when it is not needed:

  ```sh
  sudo systemctl stop liuqin-vcam.service
  ```

  WeChat then loses the camera until the service is started again.
- **Fixed format.** The feeder always offers 1280 x 720 YUY2 at 30 fps. WeChat
  scales it as needed; asking for another format from the feeder side requires
  editing the pipeline.
- **A switch is not seamless.** The feeder is restarted, so frames stop for
  about two seconds. WeChat keeps the call, but the remote side sees a short
  freeze.
- **Start order matters.** If an application opens `/dev/video20` while the
  feeder is between restarts, `VIDIOC_STREAMON` can return `EIO` and the
  application shows a black preview. Close and reopen the camera, or reselect
  **Camera** in the application.
- **One producer.** Only one feeder can write to `/dev/video20`; a second
  `gst-launch` fails. Multiple applications can read the device at the same
  time.
- **Kernel updates.** v4l2loopback is an out-of-tree module. After a kernel
  change, rebuild it (or the DKMS package) before the service can start.

## Troubleshooting

- **WeChat does not list Camera:** check `ls -l /dev/video20` and
  `systemctl status liuqin-vcam.service`. If the service is restarting in a
  loop, run it in the foreground:

  ```sh
  sudo systemctl stop liuqin-vcam.service
  sudo /usr/local/bin/liuqin-vcam-feed
  ```

- **Black preview in WeChat:** the feeder may have failed because another
  process holds the sensor (Snapshot, `cam`, another virtual-camera service).
  Stop it and restart `liuqin-vcam.service`.
- **The switch does nothing:** check the sudoers file and that the script is
  executable. `sudo -n /usr/local/bin/liuqin-cam-switch` prints an error instead
  of prompting when the rule is missing.
- **The image is mirrored:** remove the `videoflip` element from the rear
  branch of the feeder.

## Provenance

The scripts are board glue around v4l2loopback (GPL-2.0) and GStreamer. The
libcamera camera paths and the rear-camera flip follow the camera bring-up used
on this device. WeChat itself is not part of this project and is installed from
Xiaomi's official arm64 package.
