# Front camera bring-up and dual-camera capture

> **Status: experimental, not part of a release image.** The front camera needs
> kernel changes that are not in `linux-sm8450-liuqin` at the commit pinned by
> `kernel/source.json`, and the release images expose the rear camera only. This
> page records the bring-up that was tested on a 12 GB/512 GB unit and how to use
> both cameras once such a kernel is booted.

The Xiaomi Pad 6 Pro has two user-facing cameras: a Sony IMX596 in front and a
Samsung S5KJN1 in the rear. The project kernel supports the rear camera through
`qcom-camss` and libcamera's software ISP. Enabling the front camera is mostly a
matter of adding its driver and device-tree node, but the two sensors also need
separate CSID/VFE instances before they can stream at the same time.

## What works

Tested in October 2026 on Ubuntu 26.04 with `6.17.0-rc1-gfe81794b5e1b` plus the
local camera changes listed below.

| Capability | Front (IMX596) | Rear (S5KJN1) |
| --- | --- | --- |
| Enumerated by libcamera | ✅ as `imx596` | ✅ as `Internal back camera` |
| 1920 x 1080 still | ✅ | ✅ |
| 2592 x 1472 still | ✅ | ✅ |
| 1280 x 720 video | ✅ 29.9 fps | ✅ 29.5 fps |
| 1920 x 1080 video | ✅ | ✅ |
| Both cameras at once | ✅ 1920 x 1080 at 30 fps each | ✅ |
| Switching front/rear repeatedly | ✅ 10 of 10 captures | ✅ |
| Full resolution (5184 x 3904 / 4072 x 3060) | ❌ times out | ❌ blank frames |

## Hardware

The wiring comes from the vendor runtime device tree and the sensor-module
containers in the stock ROM.

| | Front | Rear |
| --- | --- | --- |
| Sensor | Sony IMX596, 20 MP, 5184 x 3904 | Samsung S5KJN1, 50 MP, 4080 x 3060 active |
| CCI bus | CCI0, bus 0, address `0x10` | CCI0, bus 1, address `0x10` |
| CSIPHY | 2 | 3 |
| MCLK | `CAM_CC_MCLK5_CLK`, GPIO 105 | `CAM_CC_MCLK2_CLK`, GPIO 102 |
| Reset | GPIO 127, active low | GPIO 126, active low |
| Rails | 1.1 V on GPIO 74, 2.9 V on GPIO 119, `pm8350c_l1` | 1.1 V on GPIO 85, 2.8 V on GPIO 52, `pm8350c_l1` |
| Lanes | 4, 678.4 MHz link frequency | 4, 700 MHz link frequency |
| Focus | fixed focus | GT9764 VCM, not wired up in mainline |

The depth camera (SmartSens SC202CS, CCI1, address `0x36`) is present in the
same vendor graph but is not enabled.

## Why the front camera is not enabled by default

1. **No mainline driver.** There is no IMX596 driver in upstream Linux or in the
   project kernel. The tested build uses a V4L2 subdev driver generated from the
   mode tables in the vendor's `com.qti.sensormodule.liuqin_aac_imx596_front_i`
   container; the register sequences, PLL settings and lane mapping come from
   that blob.
2. **No device-tree node.** The front module needs its CCI node, MCLK/reset
   pinctrl, rails and a CSIPHY endpoint. The release device tree describes the
   rear module only.
3. **One CSID is not enough for two cameras.** The pinned kernel instantiates a
   single CSID and a single VFE. A CSID has one sink pad, so the second sensor
   cannot attach while the first one holds the link; even sequential use is
   fragile once both sensors are bound. The tested build instantiates three
   CSID/VFE pairs and links CSIPHY *i* to CSID *i* mod 3 and CSID *i* to VFE
   *i*.

## Kernel prerequisites

The tested build differs from the pinned kernel in these areas. None of them is
required for rear-camera-only use.

- `qcom-camss`: three CSID and three VFE instances for the resource table behind
  `qcom,sm8475-camss`, plus a one-to-one CSIPHY/CSID/VFE link function. The front
  sensor uses CSIPHY2 -> CSID2 -> VFE2 and the rear CSIPHY3 -> CSID0 -> VFE0.
- `qcom-camss`: `csid_link_setup()` must accept enabling the same link again;
  libcamera re-enables it on every configure. A different sensor on the same
  CSID must still be rejected with `-EBUSY`.
- `qcom-camss`: `vfe_pm_domain_on()` must resume the IFE power domain before
  `device_link_add(..., DL_FLAG_RPM_ACTIVE)` and keep that reference. Without
  it the second camera session fails with `Failed to power up pipeline: -22`
  after the domain has been powered down once.
- Device tree: front module node, the additional CSID/VFE register and interrupt
  ranges, the CSIPHY endpoints for both sensors, and the front rails and pinctrl
  states.

Because the front node is in the device tree, enabling it requires rebuilding
the DTB and boot image as described in the [build guide](BUILD.md); the module
alone is not enough.

## Checking the bring-up

```sh
cam -l
```

Expected output:

```text
Available cameras:
1: 'imx596' (/base/soc@0/cci@ac15000/i2c-bus@0/camera@10)
2: Internal back camera (/base/soc@0/cci@ac15000/i2c-bus@1/camera@10)
```

The front camera is reported as `imx596` rather than "Internal front camera"
because the driver does not provide a camera location; applications that rely on
that property may not label it correctly.

Check the media graph:

```sh
media-ctl -p -d /dev/media0
```

The front and rear links should be enabled on separate CSIDs:

```text
msm_csiphy2 -> msm_csid2 [ENABLED] -> msm_vfe2
msm_csiphy3 -> msm_csid0 [ENABLED] -> msm_vfe0
```

Video node numbers are not fixed. Use `media-ctl -p` to resolve the node behind
`msm_vfe2_video0` and `msm_vfe0_video0` rather than hard-coding `/dev/videoN`.

If a process is already streaming from one camera (for example a virtual-camera
service), stop it before enumerating. libcamera then fails to enable the busy
link and the front camera disappears from the list.

## Capturing stills

GStreamer is the reliable path for stills; see the note about `cam --file` under
[Limitations](#limitations).

```sh
FRONT=/base/soc@0/cci@ac15000/i2c-bus@0/camera@10
REAR=/base/soc@0/cci@ac15000/i2c-bus@1/camera@10

timeout -s INT 6 gst-launch-1.0 -e libcamerasrc camera-name="$FRONT" \
  ! video/x-raw,width=2592,height=1472 ! videoconvert ! jpegenc \
  ! multifilesink location=/tmp/front-%02d.jpg max-files=1
```

The pipeline keeps running; `timeout` or Ctrl-C stops it. `max-files=1` keeps a
single complete JPEG, the most recent frame. Use `$REAR` for the rear camera.

`cam` can also be used for a quick capture test when no file is written
(`-c1` and `-c2` follow the enumeration order above):

```sh
cam -c1 --stream width=1920,height=1080 --capture=3   # front
cam -c2 --stream width=1920,height=1080 --capture=3   # rear
```

## Recording video

```sh
timeout -s INT 10 gst-launch-1.0 -e -q libcamerasrc camera-name="$FRONT" \
  ! video/x-raw,width=1280,height=720,framerate=30/1 ! videoconvert \
  ! x264enc speed-preset=ultrafast bitrate=4000 tune=zerolatency \
  ! mp4mux ! filesink location=/tmp/front.mp4
```

`-e` and a clean interrupt are needed for `mp4mux` to write the index; without
them the file is unplayable.

## Running both cameras at once

Both cameras must be opened **in one process**. Two separate processes (two
`cam` invocations, for example) fight over the media links and the second one
fails with `Failed to setup link ... Device or resource busy`.

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

Measured with an 8.6 s run: 253 front frames and 259 rear frames, about 30 fps
each. 1280 x 720 leaves more CPU headroom and is the safer choice on a busy
system.

## Using the cameras in applications

Applications that enumerate libcamera cameras, such as `cam -l`, see both.
Applications that only speak V4L2 need a loopback device; one camera at a time
is the useful configuration there:

```sh
sudo modprobe v4l2loopback video_nr=20 exclusive_caps=0 card_label=Camera
gst-launch-1.0 libcamerasrc camera-name="$FRONT" \
  ! video/x-raw,width=1280,height=720,framerate=30/1 \
  ! videoconvert ! video/x-raw,format=YUY2 ! v4l2sink device=/dev/video20
```

`exclusive_caps=0` keeps the device node present while the feeder is restarted,
so applications do not treat the camera as unplugged. To switch cameras, stop
the feeder and start it again with the other `camera-name`.

## Limitations

- **Full resolution does not work.** A 5184 x 3904 frame is about 80 MB; long
  captures time out and the rear sensor's 4072 x 3060 mode returns blank frames.
  2592 x 1472 is the practical upper bound.
- **30 fps ceiling.** libcamera's software ISP processes frames on the CPU.
  1080p30 single and dual streams work, but 2592 x 1472 video drops to about
  10 fps and the CPU load is high. There is no usable hardware H.264 encoder.
- **No autofocus.** The front module is fixed-focus. The rear GT9764 VCM is not
  connected to a mainline driver.
- **No orientation metadata.** The front driver does not expose a rotation
  property, so previews may be rotated. Use `cam -o rot180` or a `videoflip`
  element in GStreamer.
- **`cam --file` can write white frames.** On this platform the CPU can read a
  DMA-BUF before the ISP's writes are visible, and the PPM file comes out as
  `0xff`. GStreamer copies the frame and is not affected.
- **The IFE power domain stays on.** The `vfe_pm_domain_on()` workaround keeps a
  runtime-PM reference, which costs a few tens of milliwatts while the tablet is
  idle.

## Troubleshooting

- **The front camera is missing from `cam -l`:** check that the driver and the
  device-tree node are present (`lsmod | grep qcom_liuqin_sensors`,
  `dmesg | grep -i imx596`), and stop anything already streaming.
- **`Failed to setup link ... Device or resource busy`:** another process owns
  the camera or the link. Stop it; do not try to run two independent camera
  processes for dual capture.
- **`Failed to power up pipeline: -22`:** the VFE power-domain workaround is
  missing or the domain was left in a bad state. A reboot restores it.
- **`i2c-qcom-cci ... timeout`:** the sensor did not power up. Check the rails
  and reset GPIO, and reboot before suspecting the driver.
- **White stills:** see the DMA-BUF note above; use GStreamer or the Camera
  application instead of `cam --file`.

## Provenance

The IMX596 mode tables were reconstructed from the vendor sensor-module
container shipped in the stock ROM; no vendor binaries are distributed. The
driver and the board adaptation were written for this bring-up and are not
upstream. Parts of the work were developed with AI assistance. The rear S5KJN1
driver is the upstream-derived driver described in the [camera page](CAMERA.md).
