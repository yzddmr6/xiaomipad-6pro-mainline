# 512 GB 机型 Ubuntu 单系统安装笔记

> **这是一份现场记录，不是官方验证过的布局。** 下面的步骤来自一台 12 GB/512 GB 机器（硬件版本 16.9.0）在 2026 年 10 月的实际安装。安装器和发布说明仍然是权威来源；本文只记录 512 GB 特有的坑和这台机器最后的状态。安装过程会清空数据。

512 GB 版本的分区名和 256 GB 一样，但尺寸不同。原厂 Android 布局给 Ubuntu 根分区留 64 GiB、给 `/home` 留 271 GiB，中间是 Android 的 `super` 和 `userdata`。单系统安装可以保留这块 Android 空间不用，也可以把它回收成 `/data`。

## 实测布局

`/dev/sda` 是同时承载 Android 和 Linux 分区的 UFS LUN；`/dev/sde` 放启动分区，不要重新分区。

| 分区 | 本机大小 | 用途 |
| --- | --- | --- |
| `sda28` | 136.7 GiB | `/data`，LABEL `LIUQIN_DATA`（回收的 Android 空间） |
| `sda36` | 64 GiB | `/`，LABEL `LIUQIN_ROOT`，ext4 |
| `sda37` | 271.3 GiB | `/home`，LABEL `LIUQIN_HOME`，ext4 |
| `sde14` | 192 MiB | `boot_a`，内嵌 DTB 的 Ubuntu 启动镜像 |
| `sde17` | 8 KiB | `vbmeta_a`，AVB 元数据 |
| `sde18` | 24 MiB | `dtbo_a` |
| `sde25` | 96 MiB | `vendor_boot_a`，bootloader 启动 Ubuntu 时也会加载 |

根文件系统在 `/dev/sda36` 里，initramfs 会把它搬到 `/native-root` 再切换。不要直接格式化 `sda36`。

## 安装前

1. 解锁 bootloader（`fastboot flashing unlock` 和 `critical unlock`）。解锁会清空 userdata。
2. 使用与待安装 release 固定的原厂 ROM 版本；固件版本不一致会导致 bundle 哈希校验失败。
3. 写任何东西之前，先备份原始 `boot_a`、`vbmeta_a` 和两个 GPT。RAM 启动和持久刷写是两回事。
4. 保留原厂固件和刷写说明；回到 Android 需要完整包，只换 boot 镜像不够。

## 安装

RAM 启动安装器，选择 Linux-only 布局：

```sh
fastboot boot installer.img
```

网络/telnet 操作和布局选择按[安装验收](INSTALL-TESTING.zh-CN.md)执行。Linux-only 会把 Android userdata 的空间给 Ubuntu；安装器负责写 boot 镜像、根文件系统和固件。如果所用 release 拒绝 512 GB 容量，换一个明确支持它的 release，不要手改安装器的尺寸表。

本文这台机器是在 release 规则还没有放行 512 GB 时安装的；下面的布局是结果，不是当前安装流程的必需步骤。

## 第一次启动的坑：AVB 读取字节数不足

最常见的故障是：第一次启动停在小米 logo，随后循环或回到 fastboot。`fastboot getvar current-slot` 可能仍显示 `a`，但 `fastboot getvar slot-unbootable:a` 是 `yes`。`logfs` 分区（`/dev/sda13`，FAT）里的 bootloader 日志能看到：

```text
Booting from slot (_a)
Load Image boot_a total time: 102 ms
avb_slot_verify.c:210: ERROR: boot_a: Read fewer than requested bytes.
No bootable slots found enter fastboot mode
VB2: boot state: red(3)
```

原厂镜像的同一段日志是 `Boot state is: orange`，启动正常——这就是线索：原厂镜像带 AVB footer，bootloader 走了另一条读取路径。

解锁状态下 libavb 会用 `get_size_of_partition()` 整分区校验。这台机器的 bootloader 在这条路径上返回的字节数不足，哈希校验失败，槽位被标成不可启动。Ubuntu 的 boot 镜像没有 AVB footer，每次都会走到这条坏路径。

在两个槽的 `vbmeta` 里关掉 verity 和 verification：

```sh
fastboot --disable-verity --disable-verification flash vbmeta_a rom/images/vbmeta.img
fastboot --disable-verity --disable-verification flash vbmeta_b rom/images/vbmeta.img
fastboot --set-active=a
fastboot reboot
```

只改 `vbmeta` 里的 4 个 flag 字节（`HASHTREE_DISABLED | VERIFICATION_DISABLED = 0x3`），不动 boot 镜像，刷回原厂 `vbmeta` 即可完全回滚。第一次成功启动后，initramfs 会把槽位标成 successful，之后每次重启都能直接进 Ubuntu。

启动后在板上验证：

```sh
python3 - <<'PY'
import struct
d = open('/dev/sde17', 'rb').read(4096)
print(hex(struct.unpack_from('>I', d, 120)[0]))   # 期望 0x3
PY
```

## 不要动 initramfs 的根契约

initramfs 里带了一份哈希契约，覆盖一批文件：SLPI、hexagonrpcd、sensor gate、sensor stack 的 systemd unit，若干 `/usr/libexec` 辅助程序，`/usr/local/sbin/liuqin-slpi`、`/usr/local/bin/liuqin-shell`、触摸固件、音频 topology 等。任何一个文件被移动或修改，initramfs 就会拒绝持久根并进入应急 shell：

```text
liuqin-initramfs: native root rejected: not a regular file: ...
liuqin-initramfs: persistent root marker or root contract is invalid
liuqin-initramfs: starting local emergency shell
```

要禁用服务，用 systemd drop-in 或 `ConditionPathExists`，不要改契约里的文件本身。

应急 shell 开在 USB 网卡的 `192.168.7.2:2323`。从里面可以把根文件系统挂到 `/mnt/r/native-root`，恢复备份后重启。

## 装完必须做的收尾

把桌面用户加入设备组；不做的话 PipeWire 只有 `Dummy Output`，`/dev/video*` 也没权限：

```sh
usermod -aG audio,video,render,input <用户名>
```

这台机器上还做了这些：

```sh
# 现代 xkb-data 不接受 XKBLAYOUT="default,us"，console-setup 会失败
sed -i 's/^XKBLAYOUT=.*/XKBLAYOUT="us"/' /etc/default/keyboard

# 这个内核没有 zram 模块，只能用 swapfile
fallocate -l 16G /home/swapfile && chmod 600 /home/swapfile
mkswap /home/swapfile && swapon /home/swapfile
echo '/home/swapfile none swap sw,nofail,pri=10 0 0' >> /etc/fstab

# unattended-upgrades 会持 block inhibitor，导致 systemctl reboot 被拒
systemctl mask apt-daily.timer apt-daily-upgrade.timer
```

## 回收 Android 空间

这一步可选，而且没有原厂 ROM 就无法恢复。它会删掉 Android 的 `super` 和 `userdata`，在原位置新建 `linux_data`。先备份 GPT，并按 `sgdisk -p /dev/sda` 的实际输出调整分区号；下面的数字只适用于这台机器。

```sh
sgdisk --backup=/root/gpt-backup.bin /dev/sda
sgdisk --delete=28 --delete=29 --delete=30 --delete=31 \
       --delete=32 --delete=33 --delete=34 --delete=35 /dev/sda
sgdisk --new=28:480768:36313087 --typecode=28:8300 --change-name=28:linux_data /dev/sda
partprobe /dev/sda
mkfs.ext4 -L LIUQIN_DATA /dev/sda28
mkdir -p /data
echo 'LABEL=LIUQIN_DATA /data ext4 defaults,nofail,x-systemd.device-timeout=30s 0 2' >> /etc/fstab
mount /data
```

这台机器最后是 `/` 64 GiB、`/home` 271 GiB、`/data` 136.7 GiB。做完之后 Android 无法启动；恢复需要完整原厂固件。

## 备份与回滚

第一次成功启动后立刻做这些备份：

| 内容 | 命令 |
| --- | --- |
| Ubuntu boot 镜像 | `dd if=/dev/sde14 of=/root/boot-ubuntu.img bs=1M` |
| 当前 `vbmeta_a` | `dd if=/dev/sde17 of=/root/vbmeta-current.img`（关闭校验前应先备份原厂版本） |
| GPT | `sgdisk --backup=/root/gpt-sda.bin /dev/sda`，`/dev/sde` 同理 |
| `misc` 前 4 KiB | `dd if=/dev/sda11 of=/root/misc-first4k.bin bs=4k count=1` |

槽位不能启动时的最小恢复操作：

```sh
fastboot --disable-verity --disable-verification flash vbmeta_a vbmeta-stock.img
fastboot flash boot_a boot-ubuntu.img
fastboot --set-active=a
fastboot reboot
```

## 排障

| 症状 | 原因 | 处理 |
| --- | --- | --- |
| 重启回 fastboot，`avb_slot_verify.c:210` | AVB 短读 | 两个槽都刷带 disable flag 的 `vbmeta` |
| 两个槽都不可启动，`fastboot boot` 报 `Load Error` | 槽状态被标死 | `fastboot --set-active=a` 重置该槽状态 |
| 小米 logo 循环、USB 不出现 | 镜像不兼容（例如 Android 的 header v4 boot 镜像） | 长按电源 12 秒，再按电源+音量减进 fastboot，刷回 Ubuntu 镜像 |
| 进 initramfs 应急 shell，`native root rejected` | 改了契约文件 | 从应急 shell 恢复 |
| 装完没有声音 | 用户不在 `audio` 组 | 按上面的命令加组 |
| `systemctl reboot` 被拒 | `unattended-upgrades` 的 block inhibitor | mask 上面的 timer |
| `/` 或 `/home` 挂不上、fsck 失败 | 文件系统损坏 | `e2fsck -y /dev/sda37`（或 `sda36`） |

## 单系统还是双系统

Ubuntu 需要上面那种关闭校验的 `vbmeta`。Android 能否在同一个 `vbmeta` 下启动，这台机器上没有验证成功：原厂 boot、KernelSU boot 和干净 userdata 三次都在 USB 初始化前挂死。如果 Android 必须用原厂 `vbmeta`，两套系统就无法共用一份配置，切换就得每次用 fastboot 重写 `vbmeta`。

所以这台机器最终选择 Ubuntu 单系统，并把 Android 空间回收成 `/data`。要装双系统，先用 `fastboot boot` 证明 Android 镜像能在 Ubuntu 需要的 `vbmeta` 下启动，不要假设它一定可以。

## 来源

AVB 短读的结论来自 `logfs` 里的 bootloader 日志，以及原厂和 Ubuntu 两条启动路径的对比。分区号和尺寸来自同一台机器上的 `sgdisk -p` 和 `lsblk`。
