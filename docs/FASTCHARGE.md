# 快充

Xiaomi Pad 6 Pro 的充电功率由 ADSP 充电固件协商。固件只有在适配器通过小米私有认证后才会
打开高压充电通路，原厂系统在用户空间完成这一认证。本页说明项目如何集成该认证，以及如何
处理标准 USB-PD PPS 适配器。

| 路径 | 条件 | 结果 |
| --- | --- | --- |
| MiPPS | 小米适配器通过 UVDM 认证 | 原厂快充，上限为适配器额定功率 |
| 标准 PPS | 任意 PPS 适配器，未通过认证 | 默认 9 V、约 12 W；启用下述开关后走高压通路 |
| 普通 PD、DCP、SDP | 无 PPS 合同 | 固件默认行为 |

## 验证状态

内核接口与电量计认证已在设备上完成验证：`xiaomi` 属性组的全部属性均可读取，双电芯认证
均通过。完成电量计认证后，标准 PD 适配器可协商到 9 V / 2 A。

**MiPPS 适配器认证已在硬件上验证。** 使用原装小米 67 W MDY-15-EQ（USB-A）适配器与原装
A-C 线，守护进程完成了真实握手，快充随之生效：

| 读数 | 数值 |
| --- | --- |
| `adapter_svid` | `0x2717` |
| `adapter_id` | `0000a661` |
| `real_type` / `current_state` | `PD_PPS` / `SNK_Ready` |
| `pd_auth` / `pd_verifed` / `fastchg_mode` | `1` / `1` / `1` |
| 充电时的数据角色 | `host` |
| 电池侧功率，56 秒内 12 次采样 | 43.99 – 45.94 W（均值 45.4 W） |
| 电量 / 电池温度 | 34 – 36 % / 34.0 – 35.5 ℃ |

"电池侧功率"指电量计报告的 `Vbat × Ibat`；两路电荷泵在约 9 V 下分别报告约 3.0 A 与
2.8 A。它不是充电器输出端的测量，也不代表充电器已达到 67 W：固件会随温度与电量继续调整
充电档位。

实现小米协议的第三方充电器（酷泰科）也以 `adapter_id 0000a819` 通过了同一握手，说明该路径
并不限于原厂适配器。该充电器未做低电量功率测量。

标准 PPS 开关已集成，但仍待使用非小米的 PPS 适配器验收。

## 组件

| 路径 | 作用 |
| --- | --- |
| `/usr/local/libexec/liuqin-mipps-auth` | 认证守护进程（Python，GPL-2.0-only，见 `NOTICE`） |
| `/etc/systemd/system/liuqin-mipps-auth.service` | 以 `--role-first` 运行守护进程的 oneshot 单元 |
| `/etc/systemd/system/liuqin-mipps-battery-auth.service` | 开机时先于充电单元完成双电芯认证 |
| `/etc/udev/rules.d/90-liuqin-mipps-auth.rules` | Type-C 连接与 USB 供电上线时拉起单元 |

守护进程通过项目内核在 PMIC GLINK 电源设备上导出的 `xiaomi` 属性组与充电固件通信：

```
/sys/devices/platform/pmic-glink/pmic_glink.power-supply.0/xiaomi/
```

该属性组不存在时单元自动跳过，软件包可在没有此接口的内核上正常安装。

## 认证流程

守护进程每次 Type-C 连接执行一次，顺序如下：

1. 通过 `verify_digest` 对两个电芯做燃料计认证：写入随机挑战，读回燃料计的 HMAC-SHA256
   应答并比对，按结果设置 `authentic` / `slave_authentic`。开机单元在启动时做一次，守护
   进程只在标志缺失时补做。
2. 等待电源就绪。固件在协商期间不会应答握手，因此守护进程最多等待 8 秒，直到 `real_type`
   为 `PD` 或 `PD_PPS`，且 `current_state` 进入 `SNK_Ready`。
3. 取得 Type-C host 数据角色，即原厂系统连接小米适配器时所处的角色。每次连接只请求一次；
   请求失败时在发送任何适配器命令之前停止。
4. 通过 `request_vdm_cmd` 做适配器认证：UVDM 握手（命令 1–3）、会话种子（命令 4）、随机
   挑战（命令 5），随后按 Android 的收尾顺序先发 REMOVE_COMPENSATION（命令 7）再发 VERIFIED（命令 6）。命令 8（反向
   认证应答）不发送：它在本机超时，且原厂系统日志同样是 7 → 6。
5. 按判定写入 `pd_verifed`。固件随后选择充电档位，结果体现在 `fastchg_mode`、`power_max`
   与 `real_type`。

握手所用密钥在小米设备与适配器之间通用，已包含在守护进程中。

顺序很重要。先握手后切 host，或沿用旧的 6 → 8 → 7 收尾，认证都能完成，但在本机上无法维持
快充。每次 Type-C 连接的尝试会被记录，失败的适配器不会反复重试。

一次完整运行在日志中给出如下序列：

```
role_request=begin
role_request=end returncode=0 role=[host] device stderr=
role_first=proceed
real_type=PD_PPS
adapter_svid=0x2717
adapter_id=0000a661
adapter_sequence=android:7,6
pd_auth=1
fastchg_mode=1
pd_verifed=1
```

### 数据角色

仅当对端报告小米的 `adapter_svid`（`0x2717`）时才请求 host 角色。其他来源——电脑、扩展坞、
第三方充电器——保持已协商的 device 角色，因此充电时 USB 设备模式与 USB 网络仍可正常工作。
这些来源仍会做认证探测；小米手机同样报告该 SVID，处理方式与小米适配器一致。

守护进程等合同稳定后才读取 SVID 和数据角色，并在握手期间检查 Type-C partner，
拔线或连接替换不能继承上一次的成功判定。快速拔插仍需在候选上做硬件验收。

## 标准 PPS 适配器

默认只有通过小米协议认证的适配器才能打开高压通路，包括支持该协议的第三方产品；
品牌不是判据。其他适配器保持固件的普通 PD/PPS 合同。升级会删除旧包默认安装的
`10-allow-unverified-adapter.conf`；新装和升级都会启用电池认证单元。

命令行 `--allow-unverified-adapter` 仍保留为显式开关，要求 PD_PPS 合同并检查线缆：
3 A 拒绝、5 A 允许，未导出身份时依赖源端执行线缆限制。默认服务不传入此开关，
即使使用 5 A 线或适配器不应答 UVDM 也不放行。认证失败、提前退出和拔线会清除
`pd_verifed`；成功结果只能在同一个 Type-C partner 连接内复用。

## 验证

```sh
X=/sys/devices/platform/pmic-glink/pmic_glink.power-supply.0/xiaomi
journalctl -u liuqin-mipps-auth -n 40
cat "$X/real_type" "$X/pd_verifed" "$X/fastchg_mode" "$X/power_max"
cat /sys/class/typec/port0/data_role
cat /sys/class/power_supply/qcom-battmgr-usb/voltage_now
cat /sys/class/power_supply/qcom-battmgr-bat/current_now
```

电荷泵工作期间 `/sys/class/power_supply/qcom-battmgr-usb/current_now` 可能为 0，判断充电
通路的电流要看电池侧功率与 `bq2597x_bus_*` 属性。

固件按电量与温度限制充电档位。快充在电量约 5 %–70 % 区间可观察到；电量高于 90 % 时无论
是否认证固件都选择标准档位，电池温度超出快充窗口时同样如此。

单元每次 Type-C 连接最多运行一次。需要重试时请拔插适配器；只重启单元会一直报告
`role_first=already_attempted_this_attach`，直到连接标识改变。

## 排障

| 现象 | 检查 |
| --- | --- |
| 单元从不运行 | `xiaomi` 属性组不存在：运行中的内核早于该接口 |
| 小米适配器下 `pd_verifed` 仍为 0 | 看 `journalctl -u liuqin-mipps-auth`：`adapter auth mismatch` 表示握手失败；`pdo2 reports no source PDO` 表示适配器未提供 PD 合同 |
| `role_first=skipped_non_xiaomi` | 适配器未报告 `adapter_svid 0x2717`，因此不改动数据角色；仍会探测认证，失败时默认不开放高压通路 |
| `role_first=source_not_ready` | 适配器未在超时内以 PD 合同进入 `SNK_Ready`：普通 5 V、DCP，或协商仍在进行 |
| `role_first=no_host_no_adapter_commands` | 端口拒绝 host 角色，未发送任何适配器命令。请拔插；若持续出现，检查 `/sys/class/typec/port0/data_role` |
| `role_first=already_attempted_this_attach` | 本次连接已运行过，请拔插而不是重启单元 |
| `pd_verifed` 为 1 但 `fastchg_mode` 为 0 | 电量高于 90 %、电池温度在快充窗口之外，或适配器未提供 PPS APDO |
| `unverified_adapter=refused` | 原因随后给出：线缆额定低于 5 A，或合同不是 PD_PPS |
