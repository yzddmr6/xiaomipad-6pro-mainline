# GNOME 设置适配

本组件基于 Ubuntu 的 `gnome-control-center 1:50.0-0ubuntu6`，保留发行版补丁和各设置面板，
增加平板电源键策略的原生设置入口，并在“设置 → 系统 → 关于”底部加入“作者”分组
（yzddmr6 与项目地址，点击打开项目主页；分组标题按系统语言显示“作者”或“Author”）。

源码归档和项目补丁的校验值记录在 [source.json](source.json)。从主项目根目录准备源码：

```sh
python3 tools/build-liuqin-settings.py --prepare-only
```

在配置好 AArch64 binfmt 的 Linux 主机上，使用已准备的 Ubuntu ARM64 根文件系统构建：

```sh
sudo python3 tools/build-liuqin-settings.py --jobs 8
```

构建使用隔离的挂载命名空间和 overlay，不修改输入根文件系统。默认输出目录是
`out/gnome-control-center/`；同一输入重复构建会复用编译目录，输入改变时需使用新的
`--out`。可用 `--rootfs` 指定根文件系统。

输出 `gnome-control-center` 与 `build-info.json` 由设备包构建器消费，
`build-packages.tsv` 记录实际构建环境中的包版本。构建器不会向平板安装程序。

前端与电源键服务共享 `io.github.liuqin.power` 设置 schema。
源码及其修改保留 GNOME Control Center 的许可证和原作者声明，详见源码包中的
`COPYING` 与 `debian/copyright`。
