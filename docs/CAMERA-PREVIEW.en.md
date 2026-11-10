# Dual-camera preview for liuqin

[中文](CAMERA-PREVIEW.md) | [Released camera support](CAMERA.en.md)

This preview combines the [IMX596 kernel draft][kernel-pr], the existing
S5KJN1 rear camera, the front sensor's libcamera settings, and the [PipeWire
GPU software-ISP profile][gpu-pr]. It uses the kernel's static board device
tree. The earlier hardware results used a runtime overlay on a local build.
Boot and capture with this static board configuration still need validation.

## Build a matching preview bundle

Use an Ubuntu 24.04 build host and the prerequisites and prepared inputs in
[Building](BUILD.en.md). From this integration checkout, fetch the exact
public kernel revision named by this branch:

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
```

Assemble a matching kernel, device tree, modules and device-support package
using `out/image-inputs.local.json` from the input-preparation step in the build guide:

```sh
sudo python3 tools/build-liuqin-image.py --inputs out/image-inputs.local.json \
  --kernel-source "$kernel" --kernel-out out/camera-kernel --out out/camera-preview
python3 tools/install-liuqin.py --bundle out/camera-preview/bundle --check
```

Use the matching bundle and the project's [installation
procedure](INSTALL-TESTING.en.md) on a test device. If the device already has
the old camera bring-up service, use a clean preview root without that service
or its runtime overlay. Keep the project camera launcher unmodified.

## Verify capture and recording

1. After boot, close Snapshot and run `cam -l`. Record the IDs of both the
   IMX596 front camera and the S5KJN1 rear camera. Capture a short stream from
   each camera with the following command, using its number from `cam -l`.
   Check for successfully delivered frames and the EGL debayer log messages.
   An environment variable or EGL context initialization alone does not verify
   that frames used the EGL backend.

   ```sh
   camera_number=1 # set this to the camera number from cam -l
   LIBCAMERA_SOFTISP_MODE=gpu LIBCAMERA_LOG_LEVELS='*:INFO,Debayer:DEBUG' \
     cam -c "$camera_number" --capture=30
   ```

2. Open Camera (GNOME Snapshot), switch between front and rear, and check
   preview, still capture and video playback for each. Use the distro's
   encoder with no local OpenH264 preload. Record the actual settings and note
   any differences in orientation or color. The PipeWire setting can be checked
   with `systemctl --user show pipewire.service -p Environment`.

3. For a controlled 720p comparison with the distro x264 encoder, close
   Snapshot, install `gstreamer1.0-tools` and `ffmpeg` if needed, and run
   the following for each sensor. Use the full camera ID printed by `cam -l`
   and a different output name for each sensor and ISP mode. Record the same
   moving scene for about 60 seconds and press Ctrl+C to finalize the file.
   Repeat with `LIBCAMERA_SOFTISP_MODE=cpu` to compare against GPU processing.

   ```sh
   camera_id='replace-with-camera-id'
   output='front-gpu.mp4'
   env -u LD_PRELOAD LIBCAMERA_SOFTISP_MODE=gpu \
     LIBCAMERA_LOG_LEVELS='*:INFO,Debayer:DEBUG' \
     gst-launch-1.0 -e libcamerasrc camera-name="$camera_id" \
       ! 'video/x-raw,width=1280,height=720,format=BGRx,framerate=30/1,colorimetry=sRGB' \
       ! queue ! videoconvert ! 'video/x-raw,format=I420' \
       ! x264enc speed-preset=ultrafast tune=zerolatency \
       ! h264parse ! mp4mux ! filesink location="$output"

   ffprobe -v error -select_streams v:0 -count_frames \
     -show_entries stream=codec_name,width,height,duration,avg_frame_rate,nb_read_frames "$output"
   ffprobe -v error -select_streams v:0 \
     -show_entries frame=best_effort_timestamp_time -of csv=p=0 "$output"
   ```

   Compare decoded frame counts, duration and gaps in frame timestamps. The
   requested 30 fps caps and average frame rate alone cannot establish smooth
   recording. The earlier 720p samples contained 150 frames over five seconds
   per camera on the local setup. Results with this bundle and the distro
   encoder are still pending.

See the [libcamera GStreamer guide](https://libcamera.org/getting-started.html#using-gstreamer-plugin)
for supported camera IDs and stream caps.

[kernel-pr]: https://github.com/yzddmr6/linux-sm8450-liuqin/pull/16
[gpu-pr]: https://github.com/yzddmr6/xiaomipad-6pro-mainline/pull/36
