# Waydroid Support

[中文](WAYDROID.md) | [Project overview](../README.en.md)

The device kernel in this project already carries what Waydroid needs.
Waydroid runs an Android userspace container on the host Linux kernel, so it
requires Binder IPC, binderfs and a handful of generic kernel features:

| Requirement | Kernel status | Source |
|---|---|---|
| Binder IPC / binderfs | ✅ Built in | `device/configs/liuqin-waydroid.config` |
| IPv4 and IPv6 policy routing (FIB rules) | ✅ Built in | `device/configs/liuqin-waydroid.config` |
| PSI (`/proc/pressure`) | ✅ Built in | `device/configs/liuqin-desktop.config` |
| memfd, network namespaces, cgroup freezer | ✅ Built in | Base configuration |
| veth | ✅ Module | Installed with the kernel modules |

`device/configs/liuqin-waydroid.config` adds the Binder and routing symbols;
the result is locked by `kernel/source.json` (`config_fragments` and
`config_sha256`). After changing the fragment, regenerate and update
`config_sha256` with the same toolchain.

Android's `netd` installs the container's default route and DNS through policy
routing (`ip rule`), so the kernel must provide the advanced router and
multiple routing tables for **both** IPv4 and IPv6. If either rule set is
missing, `netd` aborts `RouteController` initialisation and the container comes
up with only its on-link `/24` route and no internet. These options change the
layout of `struct net`, so the kernel modules must be rebuilt together with the
image (`make Image modules`) and reinstalled; flashing a new `Image` alone
leaves the old modules with stale structure offsets.

## Installation

Waydroid downloads its Android image over the network (about 1 GB), so bring
the tablet online first:

```sh
sudo apt update
sudo apt install waydroid
sudo waydroid init
sudo systemctl enable --now waydroid-container
```

Then start Waydroid from the application list, or run
`waydroid session start` inside a graphical session.

## Known Limitations

- The default image ships **without Google services**; adding them is subject
  to their terms.
- Features that depend on vendor HALs -- camera, microphone, sensors and
  cellular telephony -- are unavailable, matching the Ubuntu limitations on
  this device.
- Graphics use hardware acceleration through Mesa/Freedreno; compatibility
  with games and 3D applications is not established.
- This is experimental and has not been separately validated on hardware for a
  release bundle.
