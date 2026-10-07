# 安装步骤

[English](INSTALL-TESTING.md)

首次安装和首次启动已在 256 GB 机型完成真机验证；128 GB 与 512 GB 变体按
[安装指南](FLASHING.zh-CN.md)的规则放行但未逐一真机验证。本项目仍属于实验性设备移植，
安装时请保持有人在场，并准备恢复条件。

早先版本的双系统布局已在 256 GB 机型完成真机安装：分区表完成划分，Ubuntu 安装到
`linux_root`、`/home` 位于 `linux_home`，并从 B 槽正常启动。当前版本让两个系统都从 A 槽启动，
通过更换 `boot_a` 中的 boot 镜像在两者之间切换（见[双系统切换](#双系统切换)）。
v0.6.0 已在同一台 256 GB 机型以 `--layout dual --keep-home --no-backup` 完成完整安装、首次设置和用户功能确认；A/B 启动镜像回读一致，原 `/home` 保留。此前已验证原厂固件修复后的 Android 首次启动及调试 Android 下 KernelSU 脚本往返；普通 Android 下的 KernelSU 自动加载与 WebUI 按钮仍未单独验收。
单系统布局与双系统共用同一布局引擎，但尚未在真机上安装。两者均应按有人在场的实验流程对待。

## 分区布局

安装器提供两种布局，由同一套布局引擎生成。`dual` 布局下两个系统都从 A 槽启动；
`linux-only` 布局把 Ubuntu 安装到 B 槽、不触碰 A 槽，使通过 `--android-size` 保留的应急 Android 仍可启动。

| 布局 | Android | Ubuntu 系统 | Ubuntu 用户目录 |
| --- | --- | --- | --- |
| `linux-only` | 默认不保留 | `linux_root`，32 GiB | `linux_home`，剩余全部 |
| `dual` | `userdata`，96 GiB | `linux_root`，32 GiB | `linux_home`，剩余全部 |

使用 `--layout linux-only` 或 `--layout dual` 选择。尺寸可写作 `NNG`，
也可写作磁盘尾部可用区域的百分比：

- `--android-size` 指定 Android 数据分区大小。`dual` 默认 `96G`，`linux-only` 默认 `0`。
  取 `0` 表示删除 `userdata`，尾部空间全部归 Ubuntu；非零值不得小于 16 GiB。
  `--layout linux-only --android-size 32G` 可保留一个应急用的小 Android。
- `--root-size` 指定 `linux_root` 大小，默认 `32G`，不得小于 16 GiB。
- `linux_home` 取剩余全部空间，不得小于 8 GiB。

所有分区按 4 MiB 对齐。`userdata` 只做原地缩小，保留原有的类型 GUID、唯一 GUID
与属性位，不移动任何既有分区。超出可行范围的尺寸会在访问设备之前被拒绝，
并打印可行区间。

安装器会打印完整的布局计划（分区、起始扇区、大小、操作），并沿用原有的交互确认。

### 两种布局各自写入什么

`linux-only` 写入分区表、`linux_root`、`linux_home` 与 `boot_b`，
不触碰 `super`、`metadata` 以及任何 A 槽分区。

`dual` 另外清零 `userdata` 与 `metadata` 的前 16 MiB，使 Android 首次开机重新格式化这两个分区，
而不是读到过期的文件级加密密钥；并用用户提供的 ROM 目录恢复 A 槽的原厂 Android 分区。
只有与设备当前内容不一致的镜像才会被写入，且只写入 `_a` 后缀的分区。
`super` 是 Android 稀疏镜像，无法与分区内容逐字节比对，因此选择 `dual` 时总会写入。

ROM 中的 `boot.img` 例外，不会被刷入：`boot_a` 写入本项目的 boot 镜像，`boot_b` 写入同一镜像
作为 Bootloader 的回退副本，Android 的 boot 镜像则保存在 `linux_root` 上，供下文所述的切换工具使用。
安装器不会执行原厂 `flash_all` 脚本，除 `boot_b` 外不写入任何 `_b` 分区。

### 双系统模式的前提

`--layout dual` 必须提供 `--rom-dir`，指向解包后的原厂小米 Fastboot ROM 目录。
原厂 ROM 从上游取得，不在本项目重复托管。安装器会用 `liuqin-rom-images.json` 中固定的校验值
核对 `boot.img`、`vendor_boot.img`、`dtbo.img`、`vbmeta.img`、`vbmeta_system.img`、`super.img`、
`NON-HLOS.bin`、`BTFM.bin` 与 `dspso.bin`，
不匹配即拒绝。这些校验值对应本移植验证过的确切 ROM 版本；更换版本需要重新完成该验证。

`NON-HLOS.bin` 恢复包含 WLAN 与 DSP 固件的 `modem_a`，`BTFM.bin` 恢复 `bluetooth_a`，
`dspso.bin` 恢复 `dsp_a`。
固件文件系统缺失或损坏会使 Android 在 ADB 启动前崩溃，即使 boot 与系统镜像完全匹配。
已匹配的分区保持不动；需要替换时，安装器先将原分区完整保存为私有备份目录中的
`modem_a.img`、`bluetooth_a.img` 或 `dsp_a.img` 并核对校验值。
替换镜像按实际分区大小零填充，刷入前再次核验，避免 ROM 文件末尾之后残留旧损坏内容。
若要撤销该替换，先核验备份目录的 `SHA256SUMS`，再执行
`fastboot -s SERIAL flash modem_a /path/to/backup/modem_a.img`（蓝牙与 DSP 分别对应 `bluetooth_a`、`dsp_a`）。
这会恢复备份的原字节，包括原有损坏，不改变分区表或 Android 数据。

同时需遵守 ROM 自身的防回滚要求：ROM 版本不得低于设备已熔断的版本。

### 替换 Android 侧 boot 镜像

`--layout dual` 可附加 `--android-boot IMG`，以 `IMG` 取代 ROM 自带的 `boot.img` 作为 Android 的
boot 镜像：它保存在 `linux_root` 上，每次切换到 Android 时写入 `boot_a`。其余原厂镜像仍按固定校验值
逐一核对；替换镜像本身只有在长度与原厂 `boot.img` 完全一致、且头部表明其为 Android boot 镜像
（头版本 3 或 4）时才被接受。其 sha256 会出现在布局计划与清除数据确认中。
该参数在 `dual` 以外的布局下一律拒绝。

这是在 Ubuntu 旁安装带 root 的 Android 的方式。`tools/patch-android-boot-ksu.py`
完全在主机侧生成这样的镜像：解包原厂 `boot.img`，将 ramdisk 中的 `init` 改名为 `init.real`，
以 KernelSU 的 `ksuinit` 作为新的 `init`，并加入匹配的内核模块 `/kernelsu.ko`，
重新打包时保持内核、命令行以及其余头部字段不变。所依据的 KernelSU 发行版固定记录在
`tools/lib/kernelsu-assets.json`，由 `tools/fetch-kernelsu-assets.sh` 下载并校验。

```sh
sh tools/fetch-kernelsu-assets.sh
python3 tools/patch-android-boot-ksu.py \
  --boot /path/to/extracted-stock-rom/images/boot.img \
  --ksuinit tools/local/downloads/kernelsu/v3.3.0/ksuinit-aarch64 \
  --lkm tools/local/downloads/kernelsu/v3.3.0/lkm-aarch64-android12-5.10_kernelsu.ko \
  --out out/android-ksu/boot-ksu.img --report out/android-ksu/boot-ksu.report.txt
```

该工具会先对同一镜像做一次不修改内容的解包重打包，只有结果与原厂镜像逐字节一致才继续，
因此输出与原厂镜像的差异只存在于被要求修改的 ramdisk。

修改 ramdisk 会使该分区的 AVB boot 签名与 vbmeta 哈希描述符失效，
因此生成的镜像只能在已解锁 Bootloader 的设备上启动。root 之后 Android 同样具备改写 B 槽的能力：
`liuqin_boot_ubuntu` KernelSU 模块因此在每次开机时停用系统更新程序——
Android 的 OTA 会改写非活动槽并将其设为活动槽，而该槽保存着 Ubuntu boot 镜像的回退副本。

## 准备

- Xiaomi Pad 6 Pro（liuqin），出厂分区表、4096 字节逻辑扇区，且 `userdata` 为最后一个分区；
  自定义分区布局会被拒绝。
- Bootloader 已解锁，平板进入 Fastboot，电量至少 30%。
- Linux 主机、Python 3.11 或更新版本、Android platform-tools，以及正常的 USB 网络支持。
- 个人文件已备份到平板以外；安装会清空整个 userdata，安装器不会备份个人文件。
- 已准备适配本机、满足防回滚要求的原厂 Fastboot ROM，并明确如何恢复 Android。

## 执行安装

下载同一版本的全部文件，在安装包目录执行。`install.py`、`liuqin_layout.py` 与
`liuqin-rom-images.json` 必须齐备，缺一安装器拒绝运行。若系统归档分卷提供，先合并：

```sh
if [ ! -f rootfs.tar.gz ]; then
  cat rootfs.tar.gz.part-* > rootfs.tar.gz
fi
```

安装器会在访问设备前自动校验镜像，无需重复校验。开始安装前需交互输入
`YES` 确认清空数据（脚本或无交互环境显式加 `--yes`）：

```sh
python3 install.py --bundle . --serial DEVICE_SERIAL \
  --backup /path/to/new-private-backup --erase-userdata --layout linux-only
```

```sh
python3 install.py --bundle . --serial DEVICE_SERIAL \
  --backup /path/to/new-private-backup --erase-userdata --layout dual \
  --rom-dir /path/to/extracted-stock-rom --android-size 96G --root-size 32G
```

```sh
python3 install.py --bundle . --serial DEVICE_SERIAL \
  --backup /path/to/new-private-backup --erase-userdata --layout dual \
  --rom-dir /path/to/extracted-stock-rom --android-boot /path/to/boot-ksu.img \
  --android-size 96G --root-size 32G
```

只检查文件、不访问设备时使用 `python3 install.py --bundle . --check`；
加上 `--layout` 可在不访问设备的情况下打印布局计划。
自行构建或 CI 生成的未验收包，需要在有人在场的测试中显式添加 `--allow-unverified`。

安装器临时启动 installer.img，等待 USB 网络，备份并校验 boot_a、boot_b、persist
以及分区表的主备两份副本；随后修改分区表，下载并校验系统归档，
把 `linux_root` 与 `linux_home` 格式化为 ext4，安装系统并提取本机校准和地址，
写入把 `LABEL=LIUQIN_HOME` 挂载到 `/home` 的 `/etc/fstab` 条目。
双系统布局下还会把 Ubuntu 与 Android 的 boot 镜像逐一核对校验值和头部后，放入 `linux_root` 上的切换存储区。
根文件系统安装成功且卸载后，才写入本项目的 boot 镜像（双系统布局写入 `boot_a` 与 `boot_b`，
单系统布局写入 `boot_b`），通过 Fastboot 选定启动槽（双系统为 A 槽，单系统为 B 槽）并重启。
不会写入 persist，也不会重新锁定 Bootloader。备份必须放在安装包目录以外，并保持私密。

分区表写入后，`sgdisk` 会校验新表，安装器随即重新读取并与计划逐项比对；
任何一项不符即停止安装，可用备份写回原分区表。

USB 网络通常通过 DHCP 配置；必要时可用 `--host-address` 指定主机 USB 网卡地址。
安装 RAM 环境的救援 shell 没有身份认证，只能使用可信的直连 USB，不要接入共享网络。
失败后先保留报错与备份，确认已完成哪些步骤，不要直接反复重跑。

## 重装与调整切分比例

对已经是本布局的平板，安装器拒绝调整分区尺寸。尺寸无法原地修改，
因为 `linux_root` 与 `linux_home` 必须移动，移动过程中的数据无法保留。

若只重装系统并保留 `/home`，使用 `--keep-home`：沿用现有尺寸，
不触碰 `linux_home`，只重装 `linux_root`；该选项不可与 `--android-size`
或 `--root-size` 同时使用。格式化任何分区之前，安装器先核对 `linux_home` 上现有的
`LIUQIN_HOME` 文件系统（先查卷标，再运行 `e2fsck -p`），不符即停止；通过后原样保留，
仅当其为空时才写入默认的 `/home` 内容。

要更改切分比例，先恢复出厂分区表，再重新安装：

```sh
python3 install.py --bundle . --serial DEVICE_SERIAL \
  --restore-partition-table /path/to/private-backup
```

恢复操作拒绝来自其他平板的备份，也拒绝校验值与随附清单不符的备份。
恢复出厂分区表后平板上没有可用系统，需继续执行完整安装，
或执行原厂 ROM 的完整清刷流程。

## 双系统切换

双系统布局下两个系统都从 A 槽启动。切换时把另一系统的 boot 镜像写入 `boot_a`，回读核对后重启平板，
不改变活动槽。本机 Bootloader 只通过其自身的 `fastboot --set-active` 切换槽位，该操作同时交换每一对
`_a`/`_b` 分区的类型 GUID 以及 UFS 启动 LUN；以其他方式切换槽位会使所有镜像都无法加载。
切换只涉及 boot 镜像：Android 的 `vendor_boot_a`、`dtbo_a`、`vbmeta_a`、`super`、`metadata` 与
`userdata` 保持安装时的状态；Ubuntu 的 boot 镜像自带内核、设备树与 initramfs。

未安装在 `boot_a` 中的镜像保存在 Ubuntu 根文件系统上：

```
/var/lib/liuqin/switch/
  ubuntu/    boot.img  SHA256SUMS  meta.json
  android/   boot.img  SHA256SUMS  meta.json
  state.json switch.log
```

两组镜像均由安装器写入。该目录不在 boot 镜像启动 Ubuntu 前核对的文件清单之内，
修改它不会妨碍 Ubuntu 启动。`boot_b` 保存 Ubuntu boot 镜像的一份副本，
供 `boot_a` 无法加载时 Bootloader 自动回退使用；切换工具从不写入 `boot_b`。

**在 Ubuntu 中**，从应用列表打开"重启到 Android"（需要输入管理员密码），或执行：

```sh
sudo liuqin-switch status          # boot_a 与存储区当前内容
sudo liuqin-switch verify          # 重新核对两组镜像；有任何异常即返回非零
sudo liuqin-switch to-android      # 将 Android 镜像写入 boot_a 并重启
```

**在 Android 中**，使用 KernelSU 模块 `liuqin_boot_ubuntu` 的 WebUI 中的"Reboot to Ubuntu"按钮，
或执行 `su -c 'sh /data/adb/modules/liuqin_boot_ubuntu/system/bin/boot-ubuntu'`。
切换期间 Android 以只读方式挂载 Ubuntu 根分区；Android 内核无法以读写方式挂载该文件系统。

`--no-reboot` 在重启前停止，`--dry-run` 只报告将要写入的内容，不写入任何数据。

切换工具依次执行以下规则：

- 存储区中的镜像缺失、与 `SHA256SUMS` 不符、大于 `boot_a`，或类型与所在目录不符时，
  在任何写入之前拒绝。
- 没有经过核对的返回途径就不切换。切换到 Android 前，存储区中必须有可用的 Ubuntu 镜像；
  切换到 Ubuntu 前，`boot_a` 中当前的 Android 镜像必须与存储区中的副本一致——在 Ubuntu 中
  会先将其归档，在存储区只读的 Android 中则直接拒绝。
- 识别为本项目镜像的内容绝不会被当作 Android 镜像归档。
- `boot_a` 按整个分区写入（镜像加零填充），同步后回读比对。回读不一致或写入失败时，
  写回切换前系统的镜像，并以错误状态退出，不重启平板。
- 存储保护每次开机都会为 `boot_a` 及其所在磁盘设置内核只读标志；切换工具只在写入期间清除该标志，
  并在所有退出路径上恢复。写入 `boot_a` 期间忽略中断、挂断与终止信号。

两类镜像按 boot 镜像头部区分：头版本 2、页大小 4096 字节、头部 1660 字节且内嵌设备树的是本项目的
Ubuntu 镜像；头版本 3 或 4（头部分别为 1580 或 1584 字节）的是 Android 镜像；其他一律拒绝。

**Android 的 boot 镜像发生变化时。** 在平板上直接替换 Android 的 boot 镜像（例如使用 KernelSU
管理器的直接安装功能）会使其与存储区中的副本不一致，此后从 Android 切换到 Ubuntu 会被拒绝。
请在主机上制作此类镜像，并在切换前从 Ubuntu 导入：

```sh
sudo liuqin-switch import-android /path/to/boot-ksu.img
```

若切换已被拒绝，可在 Android 中把 `boot_a` 复制出平板，在主机上按下文的 Fastboot 命令
恢复 Ubuntu 的 boot 镜像，再从 Ubuntu 导入该副本。

**平板从 B 槽启动时。** `boot_a` 无法加载时，Bootloader 会自行切换到 B 槽，从回退副本启动 Ubuntu。
切换工具会识别这种情况并拒绝写入，因为写入 `boot_a` 无法改变下次启动的内容。
请在 Fastboot 模式下从主机修复：

```sh
cp /path/to/boot.img boot-padded.img
truncate -s 201326592 boot-padded.img
fastboot flash boot_a boot-padded.img
fastboot --set-active=a
```

Fastboot 只写入发送的字节，而 `liuqin-switch` 按整个分区识别 `boot_a` 的内容，因此与安装器一样，
镜像需零填充到 192 MiB 分区大小后再刷写。直接刷写原始镜像同样可以启动，但分区尾部会保留原有内容，
此时 `liuqin-switch verify` 会报告“不是存储区中的 Ubuntu 镜像”，直到 `liuqin-switch to-ubuntu`
重写整个分区。

切换工具无法拦截的写入中断（例如断电）会导致相同的结果，修复方法相同。

**验证状态。** 切换工具的判定与校验逻辑已由主机侧测试覆盖，这些测试以普通文件代替分区运行。
两个方向的切换、Android 侧的只读挂载，以及切换后任一系统从 A 槽冷启动，均尚未在平板上执行。
早先版本中 Ubuntu 曾从 B 槽启动；从 A 槽启动时 Bootloader 会搭配 A 槽的原厂 `dtbo_a` 与
`vbmeta_a`，这一组合尚未测试。

**升级早先的双系统安装。** 早先版本的安装从 B 槽启动 Ubuntu，且没有切换存储区。
使用当前版本重装系统并保留 `/home`，即可迁移到新方案：

```sh
python3 install.py --bundle . --serial DEVICE_SERIAL \
  --backup /path/to/new-private-backup --erase-userdata --layout dual \
  --rom-dir /path/to/extracted-stock-rom --android-boot /path/to/boot-ksu.img \
  --keep-home
```

`--android-boot` 可省略；省略时保存 ROM 自带的 `boot.img`。

## 双系统下的 Android

双系统安装后的首次 Android 开机会重新格式化 `userdata` 与 `metadata`，需要数分钟。

安装器不会代为取得 KernelSU root。请按上文在主机上生成打过补丁的镜像，安装时通过
`--android-boot` 传入，或之后在 Ubuntu 中用 `liuqin-switch import-android` 导入。
不要直接把它刷入 `boot_a`。

**切勿使用 KernelSU 管理器的"安装到未使用的槽位"。** 未使用的槽位是 B 槽，
其中保存着 Ubuntu boot 镜像的回退副本。

**必须冻结系统更新。** MIUI / HyperOS 的 OTA 会写入未使用的槽位（B 槽）并将其设为活动槽。
Android 无法从 B 槽启动，该更新还会破坏 Ubuntu boot 镜像的回退副本。

**切勿再次执行原厂 `flash_all` 脚本。** 该脚本绝大多数镜像使用 `_ab` 后缀，
即一次写入两个槽位，并在结尾执行 `fastboot set_active a`。
双系统建成后运行它会覆盖 Ubuntu 的启动链，使平板退回到只有 Android 的状态。
`flash_all_lock.sh` 与 `flash_all_except_storage.sh` 同理。

## 桌面诊断

有人在场的安装测试可在安装命令后添加 `--enable-rescue`，让救援通道从首次启动即可使用。
该选项会开放免认证 root 访问，仅适用于可信连接；默认安装不启用。

也可以在平板上手动开启：

```sh
sudo liuqin-rescue on
```

使用 `liuqin-rescue status` 查看状态。开启后，`192.168.7.2:2323` 提供救援访问，重启后仍然有效。
不要将此端口转发或暴露到其他网络。诊断结束后，在平板上执行 `sudo liuqin-rescue off` 关闭通道；
现有救援连接也会断开。

## 已知问题

部分平板在启动传感器处理器（SLPI）时会复位。遇到这种情况，可创建空标记文件
`/etc/liuqin-slpi-disabled`（例如执行 `sudo touch /etc/liuqin-slpi-disabled`）后重启：
`liuqin-slpi.service` 将被跳过，SLPI 不再启动。标记存在期间自动旋转不可用；删除该文件并重启即可恢复启动 SLPI。
切勿直接修改 `/etc/systemd/system/liuqin-slpi.service`：boot 镜像会校验该文件，文件被改动后将拒绝启动该根分区。

## 恢复 Android

仅恢复 Android 会清除 Ubuntu，需要使用匹配的原厂 Fastboot ROM 完成系统恢复和 userdata 初始化。
仅还原 boot 分区不等于恢复 Android，也不会恢复分区表；
若平板使用过分区布局安装，应先执行 `--restore-partition-table`。

使用原厂完整清刷流程，不用保留数据或重新上锁的变体；保留原厂防回滚检查。
不得恢复其他平板的 persist 或校准。在仍有非原厂镜像时保持 Bootloader 解锁。
原厂 ROM 从上游取得，不在本项目重复托管。

Android 恢复路线仍待独立真机验证，不应将 Ubuntu 安装通过等同于恢复已验证。

## 明确跳过备份的重装

已有受支持的 split 布局时，可在 `--keep-home` 重装命令中用 `--no-backup` 替代 `--backup 路径`，明确跳过启动分区、persist 与 GPT 备份。该选项不允许首次分区或恢复分区表。默认安装仍执行备份。

`--keep-home` 只保留独立 `/home`，仍会重建 Ubuntu 系统分区，旧账号配置与系统中的指纹模板/凭据会清除；首次设置时需重新创建账号并录入指纹。

本版完整包同时附带 `liuqin_boot_ubuntu-v0.3.zip`、`dualboot-tools.zip` 和公开指纹构建输入；正常使用指纹无需再安装单独应用。解压 `dualboot-tools.zip` 后可按本文命令获取、校验 KernelSU 上游组件并生成 Android boot。
