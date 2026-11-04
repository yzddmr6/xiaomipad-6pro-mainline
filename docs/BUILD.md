# 构建指南

[English](BUILD.en.md) ｜ [项目首页](../README.md)

## 内核

使用 Linux 主机，将项目和内核源码放在同级目录：

```text
workspace/
  xiaomipad-6pro-mainline/
  linux-sm8450-liuqin/
```

内核 checkout 必须对应 `kernel/source.json` 中的提交。内核仓库基于
`sm8450-mainline/linux`，设备分支为 `liuqin-6.17`。
构建脚本会在编译前核对源码提交和最终配置。

Ubuntu 24.04 主机依赖：

```sh
sudo apt-get update
sudo apt-get install build-essential bc bison flex libssl-dev libelf-dev \
  gcc-aarch64-linux-gnu libc6-dev-arm64-cross binutils-aarch64-linux-gnu python3 git ccache kmod rsync
```

在项目目录执行：

```sh
python3 tools/build-liuqin-kernel.py --configure-only
python3 tools/build-liuqin-kernel.py --jobs 12
```

第二条命令编译 Image、设备树和模块，输出到 `out/kernel`，不会访问平板。

| out/kernel 下的路径 | 内容 |
|---|---|
| arch/arm64/boot/Image | ARM64 内核 |
| arch/arm64/boot/dts/qcom/sm8475-xiaomi-liuqin.dtb | 设备树 |
| modules/lib/modules/ | 安装后的内核模块 |
| .config | 最终内核配置 |
| vmlinux、System.map | 调试与符号信息 |
| build-info.json、SHA256SUMS | 源码/工具链身份与核心产物校验值 |

用 `--source 路径` 指定其他内核目录，用 `--out 路径` 指定其他输出目录。
重复构建复用兼容的输出和 ccache；构建输入改变时需使用新输出目录。
默认编译器为 `aarch64-linux-gnu-gcc`，可通过 `CROSS_COMPILE` 指定工具链前缀。

内核编译成功不代表真机功能验收通过，也不等于生成了完整安装镜像。
逐字节比较还需要固定完整工具链与全部构建输入。

## 用户态与镜像

### 输入来源

| 内容 | 获取与维护方式 |
|---|---|
| 原版 Ubuntu 桌面基础系统 | 脚本从 Canonical 下载固定 ISO、校验后提取；本仓库不镜像原版 ISO |
| 未修改的 Ubuntu 软件包、BusyBox | 从 Ubuntu 软件源下载，复用本地缓存；不另建软件包镜像站 |
| 上游工具与用户态源码 | 使用固定上游版本；本仓库保留调用代码、必要补丁和版本引用 |
| 设备内核与项目适配 | 本项目两仓维护源码；安装版本提供匹配的预编译组件 |
| 板级固件组合 | 安装所需组件由版本物料统一管理，不要求用户拼接实验产物；不重复托管整个原厂 ROM |
| 本机校准、设备地址 | 安装时读取用户自己的平板，不能包含在通用包中 |
| 已适配的 Ubuntu 系统 | 项目安装版本的成品，不等同未修改的上游 rootfs |

构建者从上游下载基础输入后执行本项目装配；普通安装用户使用匹配的成品包，
不需要自己编译内核、设置程序或逐项查找依赖。安装包见 [GitHub Releases](https://github.com/yzddmr6/xiaomipad-6pro-mainline/releases)，
请遵守对应版本的验证范围与限制。

### Ubuntu 基础系统

需要 curl、util-linux（flock）、7z 和 squashfs-tools。按顺序执行：

```sh
sh tools/build-liuqin-ubuntu-desktop-rootfs.sh download
sh tools/build-liuqin-ubuntu-desktop-rootfs.sh casper
sudo sh tools/build-liuqin-ubuntu-desktop-rootfs.sh extract
```

下载复用已校验缓存，传输中断可续传；`UBUNTU_DESKTOP_URL` 可指定提供同一文件的镜像，
不会接受不同版本。输入位置可用 `UBUNTU_DESKTOP_INPUT` 指定，后续步骤须使用相同值。
提取只准备桌面基础系统；还需装入项目设备组件，不能直接作为平板启动镜像。

### 设备组件

第三方组件不要求全部从源码重建：可以使用固定版本的上游二进制，或在允许分发的前提下
使用项目提供的预备组件。保留来源、版本、校验值及必要许可/对应源码材料；本项目的修改提供源码。
目前尚未发布预备固件包，不能将本地缓存视为已有公共下载。

Wi-Fi 固件组合可通过 `tools/build-liuqin-wlan.py` 准备，输入为原厂 QCA6490 目录
（amss20.bin、m3.bin、regdb.bin、bd_m81gf.elf）及固定版本的上游 board-2.bin.zst。
输出可直接传给固件准备器的 `HSP2_TUPLE_DIR`。`--bdencoder` 指向 Qualcomm
qca-swiss-army-knife 提交 `6df4dae3e2f5e4c2903f3cafd40996fc1b3639ce` 下的
`tools/scripts/ath11k/ath11k-bdencoder`，需要 Python 3 和 zstd。
通过 `--vendor`、`--base-board` 和 `--out` 指定输入和新输出目录。
原厂输入的获取说明与分发边界仍待完善；本工具不下载或分发固件。

设备适配源码包括系统服务、音频配置、电源键支持、传感器补丁和 GNOME 设置程序补丁。
传感器源码版本记录在 `device/sensors/sources.manifest`。

GNOME 设置程序的源码与补丁由 `device/gnome-control-center/source.json` 固定。
主机需提供 Python 3.12、curl、patch、dpkg-dev 和 AArch64 binfmt 解释器，并准备好
Ubuntu ARM64 根文件系统。先准备源码：

```sh
python3 tools/build-liuqin-settings.py --prepare-only
```

再执行 `sudo python3 tools/build-liuqin-settings.py --jobs 8`。构建使用独立挂载命名空间，
不修改输入根文件系统。设备包构建器从 `out/gnome-control-center/` 读取程序与源码身份，
不再依赖历史预编译的设置程序。

### 输入准备与产物缓存

`python3 tools/prepare-image-inputs.py run` 一次备齐 `build-liuqin-image.py`
需要的全部输入并写出 `out/image-inputs.local.json`。音频拓扑、固件树等便宜的
输入每次新鲜重建；传感器栈与 GNOME 设置程序这两个 QEMU 慢构建按来源指纹缓存
在 `tools/local/artifacts-cache/`：输入未变时经 sha256 复验后直接复用，
输入一变就自动重建。`check` 子命令只报告复用/重建，不动手。需要重建时
会经 sudo 调用对应官方构建器（先 `sudo -v`）。

扬声器拓扑使用
[linux-msm/audioreach-topology](https://github.com/linux-msm/audioreach-topology)
的 `2af1f1ebb8d4fd03b5f53891467ddde2e208a8a0` 提交。准备好对应源码后执行：

```sh
AUDIOREACH_TOPOLOGY_DIR="$PWD/../audioreach-topology" \
  OUTPUT="$PWD/out/audio-topology/Xiaomi-Pad-6-Pro-tplg.bin" \
  sh tools/build-liuqin-audio-topology.sh
```

native boot 构建器直接使用指定内核的 Image、DTB，以及已装配根文件系统的文件清单、
音频拓扑和固件输入，不需要旧 boot 镜像或 recovery 根文件系统。

| 输入 | 环境变量 |
|---|---|
| 内核源码与构建输出 | KERNEL_SOURCE、KERNEL_OUT |
| 根文件系统的 native-root.hashes | NATIVE_ROOT_HASHES |
| 编译后的扬声器拓扑 | AUDIO_TOPOLOGY |
| 已准备的设备固件目录 | FIRMWARE_POOL |
| HSP2 WLAN 固件组合 | WLAN_HSP2_TUPLE |
| 原厂 DTBO 条目及基础 DTB | STOCK_OVERLAY_DIR、STOCK_BASE_DIR |

提供这些输入后，执行 `sh tools/build-liuqin-native-boot.sh`，输出为
`out/native-boot/boot-liuqin-native.img`。同目录的 `native-boot.identity`
记录输入与输出校验值，不包含本地路径。离线装配结果需经过真机验证后才能分发。

完整镜像从干净 checkout 构建的流程仍在准备，需补齐版本化固件输入、用户态构建依赖
后，才能提供受支持的一体化入口。不要用旧 boot 镜像或其他构建的内核代替。

## 根文件系统归档

装配及 manifest 阶段完成后，在相同 `OUT_DIR` 下执行
`sudo -E sh tools/build-liuqin-native-root.sh pack`，生成 `rootfs.tar.gz` 及校验文件。
归档期间输入树不可修改，不要对已经启动、创建账户或注入本机数据的根目录打公共包。
这一步不重编组件，也不操作平板；不会覆盖已有归档。

归档保留数字UID/GID、权限、符号/硬链接、ACL与扩展属性，包括 `security.capability`。
本地解包使用 `sudo sh tools/lib/rootfs-archive.sh extract 新目录 rootfs.tar.gz`；
仅解包可信且校验通过的项目归档，需要 GNU tar，不可换成 BusyBox tar。
安装环境也必须具备同样的解包能力；本地归档通过不代表首次安装/恢复已经验证。

## 持续集成

工作流使用与本地相同的 Python 入口编译固定版本内核，产物为内核构建文件，
不是可安装的 Ubuntu 发行包。内核仓库的提交构建与主项目的锁定版本构建共用同一入口。
整包装配使用 `tools/build-liuqin-image.py`，支持单阶段续跑；GitHub 整包任务需要配置专用构建机，
默认手动触发，可在配置完成后启用 main 更新自动装配。详见[CI 配置](CI.md)。
真机测试按[安装步骤](INSTALL-TESTING.md)进行。CI 编译成功不代表新安装包已通过真机验证。

## Release 文件

完成真机安装测试后，直接导出现有安装包，不重建镜像：

```sh
sudo python3 tools/build-liuqin-image.py --inputs inputs.local.json \
  --kernel-out out/kernel --out out/image --stage release-assets --device-tested
```

将 `out/image/release-assets/` 内的文件上传至主项目的 GitHub Release。
根文件系统自动分卷以满足单文件大小限制，合并方式见安装步骤。
未完成真机测试的包不得使用 `--device-tested`。
