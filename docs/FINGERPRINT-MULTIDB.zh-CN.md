# 一库一指纹与录入进度提示

> **状态：已在 12 GB/512 GB 机器上实测并由用户验收。** 两根手指各自是独立的单模板数据库，都能解锁，设置里显示两项；录入对话框还会显示实时的 `已录入 X 次 / 共 Y 次`。此前「一个库装多根手指」的实验方案由本方案取代。

FPC1264 的 TA 每个模板对应一个不透明数据库。之前的做法是把多根手指塞进同一个库，再改写手指号让 fprintd 只看到一个 print；这样能用，但设置里只能显示一行、不能单独删除某一根，而且所有手指共用 5 个槽位。

现在的做法是每根手指一个库、一个模板文件。缺的那一环在 fprintd 的验证路径：存了多份模板、设备又不支持 `IDENTIFY` 时，原版只验证 `gallery[0]`。私有 fprintd 构建现在会按手指排序后逐个尝试。

## 工作方式

```text
设置在手指 F 上录入
  -> TOD 驱动把 FP_ENROL_FINGER=F 传给 provider
  -> provider 新建一个单模板 TA 数据库
  -> fprintd 把它存成独立的 FP3 文件

验证（任意手指）
  -> fprintd 按手指号排序全部模板
  -> 第 1 个库：采集一次，匹配或未匹配
  -> 未匹配：发非终结的 verify-retry-scan 和手指提示，换下一个库
  -> 第一个匹配结束本次动作，不再采集后面的库
```

每个候选库都要单独采集一次。测试客户端无法在同一次 TEE 会话里复用一次采集去匹配第二个库（返回 `-249577372`），所以换库时 daemon 会要求再按一次。两根手指时最坏按两次；正常情况下第一次就匹配。

## 组件改动

| 部件 | 改动 |
| --- | --- |
| `libfprint-tod/fpc1264-oem.c` | 把请求的手指号传给 provider，录入阶段数改为 26，保留每指独立的数据库路径 |
| `oem/native_enrol.py` | 要求 `FP_ENROL_FINGER`，发现该手指已有文件就拒绝，始终用 `--enrol` 新建数据库 |
| `oem/fpc_build_info.c` | 布防抬手中断后每 250 ms 检查 TA 状态；只有 TA 确认抬手才算成功，IRQ 本身不算 |
| `fprintd-oem/fprintd-multidb.patch` | 对完整 gallery 排序并排队，逐库验证，发 `verify-retry-scan` 和 `verify-finger-selected`，取消后停止 |
| `settings-progress/` | 可选的 `LD_PRELOAD` GTK 扩展，在原生指纹对话框里加计数行 |

fprintd 补丁保留原有的自适应模板回写：匹配成功写回实际匹配的那个文件，写入失败不会误报成功。

## 实测结果

| 测试 | 结果 |
| --- | --- |
| 两个独立模板文件 | `2`、`7`，各 772,578 B |
| 设置里的条目 | 每根手指一行 |
| 左食指解锁 | 匹配 |
| 右食指解锁 | 匹配 |
| 未录入手指 | 不匹配，随后 daemon 请求下一个库 |
| 删除其中一根 | 对应文件删除，另一根仍可匹配 |
| 录入进度 | 真实对话框显示 `已录入 4 次 / 共 26 次 · 请完全抬起手指` |
| 录入耗时 | 右食指约 78 秒，26 个阶段 |

私有测试还覆盖了取消、daemon 退出、空 gallery、存储失败和删除两根中的一根。用户随后在真实锁屏和设置里验收通过。

## 继续录入

1. 关掉已打开的设置窗口，再打开 **设置 → 系统 → 用户 → 指纹登录**。
2. 用正常密码对话框授权。
3. 按提示录入，按一下、完全抬起、适当换位置。计数只累计被接受的阶段，重试不增加。
4. 换另一根手指重复。每根手指会创建新的一行和新文件。
5. 锁屏后用任意一根解锁。

不要重复录同一根手指：会多出一个库，和原库争同一根手指，拖慢验证。

## 限制

- **每个候选库一次按压。** 测试客户端无法把一次采集复用到多个库；两根手指时第一个库不匹配就要再按一次。
- **PAM 超时。** `common-auth` 仍是 `timeout=15`。两根库在实测范围内，但慢采集或更多手指没有重新测量。
- **顺序按手指号，不按最近成功。** gallery 从左拇指排到右小指，daemon 还没有记住上次成功的手指。
- **设置会占用设备。** 指纹对话框打开期间 `gnome-control-center` 持有设备，`sudo` 和锁屏可能回落到密码。进度扩展会在会话锁屏时关闭对话框，但平时不会主动释放。
- **UI 扩展可选且脆弱。** 它依赖 `CcFingerprintDialog` 类型和 `enroll_message`/`enrollment_view` 控件名；设置更新后可能静默失效，但不影响录入本身。
- **没有去重。** 同一根手指重复录入会再建一个库，而不是替换或拒绝。

## 构建与安装

驱动、provider、客户端和 fprintd 由组件脚本构建：

```sh
tools/build-tod.sh
tools/build-userspace.sh --with-rpmb-provider
FPRINTD_SOURCE=/path/to/fprintd-v1.94.5 tools/build-fprintd-oem.sh
```

`build-fprintd-oem.sh` 会在原有自适应补丁之后应用 `fprintd-multidb.patch`。进度扩展单独安装：

```sh
device/fingerprint/settings-progress/build.sh
sudo device/fingerprint/settings-progress/install.sh
```

安装脚本保留发行版 Settings 二进制，加一个本地 `dpkg-divert`，再装一个设置 `LD_PRELOAD` 的启动器。Settings 包更新后需要重新运行。

## 回滚

停掉 fprintd，恢复切换前的组件和模板备份。测试机使用的部署备份是 `/root/fpc-deploy-single-db-20261030-123745/`，其中的 `rollback.sh` 会恢复上一版 round2 套件和合并模板，并保留新录入的文件。进度扩展用下面的命令移除：

```sh
sudo device/fingerprint/settings-progress/uninstall.sh
```

## 来源

一库一指纹拆分、串行验证 daemon 补丁和进度扩展都是为这台平板开发的。fprintd 改动是对 1.94.5 的本地补丁，不是上游提交。TA 协议部分基于本仓库的 OEM 组件，不分发任何原厂二进制。
