# Camera

The Xiaomi Pad 6 Pro rear camera (Samsung S5KJN1, 50 MP) works as an
experimental feature: preview, still photos and video recording run in the
distribution Camera application (GNOME Snapshot). Image processing runs on the
CPU through libcamera's software ISP; there is no calibrated tuning, no
autofocus and no hardware encoding. The front camera is not enabled in release
images; an experimental bring-up that also covers simultaneous front/rear
capture is documented in [Front camera bring-up and dual-camera
capture](CAMERA-FRONT.md).

| Function | Status |
| --- | --- |
| Rear preview and still photos | 🟡 works, image softer than the stock system |
| Rear video recording and playback | 🟡 works, motion stutters |
| Autofocus, calibrated tuning, other sensor modes | ❌ not implemented |
| Front camera | ❌ not in release images; experimental bring-up in [Front camera](CAMERA-FRONT.md) |
| Hardware video encoding | ❌ not used |

## Validation status

Contributor testing on a liuqin tablet (Ubuntu 26.04, a temporary `fastboot boot`
of the camera kernel) covered:

- ten capture start/stop cycles, with the TITAN_TOP and IFE0 power domains
  returning to off after each one;
- a continuous 300-frame 4080 x 3060 RAW capture at a mean frame interval of
  33.3 ms;
- real-scene 1920 x 1080 JPEG photos and ten-second MP4 recordings in Snapshot,
  with in-app playback, across repeated close/reopen cycles;
- correct preview orientation.

The kernel that landed adds review cleanups on top of the tested revision:
comment and dead-code removal in the camera clock controller, the
`qcom,sm8475-camss` compatible, and removal of unused device-tree nodes. The
cleanups do not change the tested driver behaviour. A complete image built by
the project has not yet been booted with the camera; report results in an issue.

Testing ran with the product boot arguments, which include `clk_ignore_unused`
and `pd_ignore_unused`. Whether the camera clock and power-domain set is
complete without them is not established.

## Components

| Part | Role |
| --- | --- |
| kernel: `camcc-sm8450` (`qcom,sm8475-camcc`) | camera clocks and power domains; SM8475 retains GDSC state |
| kernel: `i2c-qcom-cci` | camera control bus (CCI0) |
| kernel: `qcom-camss` (`qcom,sm8475-camss`) | CSIPHY3, CSID0 and IFE0, the subset the rear camera uses |
| kernel: `s5kjn1` | sensor driver, 19.2 MHz clock, 4080 x 3060 RAW10 at 30 fps |
| `device/configs/liuqin-camera.config` | enables the above and the system DMA-BUF heap, applied after the module trim |
| `/etc/modprobe.d/liuqin-camera.conf` | loads the clock controller before CAMSS |
| `/etc/udev/rules.d/70-liuqin-camera-heap.rules` | gives the video group and the active seat access to `/dev/dma_heap/system`, which the software ISP allocates from |
| `/usr/local/bin/liuqin-camera` | starts Snapshot with DMA-BUF import re-enabled (see below) |
| `/usr/local/share/applications/org.gnome.Snapshot.desktop`, `/usr/local/share/dbus-1/services/org.gnome.Snapshot.service` | route the Camera launcher and D-Bus activation through `liuqin-camera` |

The image also installs `gnome-snapshot`, `libcamera-ipa`,
`gstreamer1.0-libcamera`, `gstreamer1.0-plugins-bad` (`h264parse`),
`gstreamer1.0-plugins-ugly` (the H.264 encoder used for recording),
`gstreamer1.0-libav` (the H.264 decoder used for playback), and the diagnostic
tools `libcamera-tools` (`cam`) and `v4l-utils` (`media-ctl`, `v4l2-ctl`) used
in [Verifying](#verifying). They are image content, not dependencies of
`liuqin-device-support`, so they can be removed without affecting the device
support.

## DMA-BUF import in Snapshot

On this device, GTK's direct DMA-BUF texture import can show stale data: the GPU
reads a buffer before the CPU's writes to it are visible, and the image renders
as noise. `/etc/environment.d/50-liuqin-dmabuf.conf` therefore sets
`GDK_DISABLE=dmabuf` for every application, and GTK uploads images by copying
them instead.

Snapshot needs DMA-BUF import for camera preview, so `liuqin-camera` clears
`GDK_DISABLE` for Snapshot only. Camera preview is the same pattern the global
workaround guards against (the software ISP writes the frame on the CPU and the
GPU displays it), so preview corruption is possible, although it has not been
observed in testing. Saved photos and videos are encoded from the frames on the
CPU and are not affected. Both the global setting and this exception will go
away once the kernel coherency problem is fixed.

## Using the camera

Open **Camera** from the application grid. Recording was tested with Snapshot's
hardware encoding turned off; if recording fails, turn it off:

```sh
gsettings set org.gnome.Snapshot enable-hardware-encoding false
```

## Verifying

With Camera closed:

```sh
cam -l                          # from libcamera-tools; expect an internal back camera
cam -c1 --capture=3             # capture three frames; each output line reports one frame
sudo cat /sys/kernel/debug/pm_genpd/pm_genpd_summary | grep -Ei 'titan|ife'
                                # the camera domains should be off
```

Do not run `cam -l` or other camera enumeration while Snapshot is previewing:
libcamera resets the sensor's flip controls during enumeration, which turns the
active preview upside down until Camera is reopened. Video node numbers are not
fixed; use `media-ctl -p` (from v4l-utils) to resolve `msm_vfe0_video0` for direct capture tests.

## Troubleshooting

- **No camera found:** check that the modules loaded
  (`lsmod | grep -E 's5kjn1|qcom_camss|camcc'`) and read
  `journalctl -k | grep -Ei 'camss|s5kjn1|cci'`.
- **Snapshot opens but shows no image:** confirm it was started through the
  Camera launcher, not `/usr/bin/snapshot` directly, and that no user-level
  `~/.local/share/applications/org.gnome.Snapshot.desktop` or D-Bus service
  override bypasses `liuqin-camera`.
- **Recorded video plays black:** `gstreamer1.0-libav` is missing.

## Provenance

The camera support was contributed by reisa. The S5KJN1 driver is adapted from
Vladimir Zapolskiy's (Linaro) upstream driver, commit
[e38fd0933c75](https://github.com/torvalds/linux/commit/e38fd0933c759bfd51aafbee07f11e8d69da4366),
with its copyright and module author retained. The liuqin 19.2 MHz mode data was
reconstructed from the device's vendor camera module configuration; no vendor
binaries are distributed. The CSIPHY v2.1.3 lane settings follow Qualcomm's
GPL-2.0 downstream camera driver, with its copyright notices retained. The
contributor states that parts of the board adaptation were developed with AI
assistance.
