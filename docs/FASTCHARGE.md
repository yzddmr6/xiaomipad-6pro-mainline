# 快充

Xiaomi Pad 6 Pro 的 ADSP 充电固件负责协商电压和电流。双电量计认证通过后，系统按充电器的能力选择通路。

| 路径 | 条件 | 结果 |
| --- | --- | --- |
| MiPPS | 适配器通过小米 UVDM 握手 | 开放小米高压快充通路 |
| 标准 PPS | 适配器未通过小米握手，已协商的 PPS 合同就绪 | 默认开放标准 PPS 高压快充通路 |
| 普通 PD | 固定电压合同 | 使用协商的固定电压档位 |
| DCP、SDP | 常规 USB 供电 | 固件按电源能力充电 |

亮屏和熄屏使用同一充电策略。电芯温度保护及充电末段的恒压收尾由充电固件执行。电脑 USB 口的可用功率取决于该口及线材；整机负载高于输入功率时，电量仍会下降。

## 验证状态

真机上两个电量计均通过认证。普通 PD 适配器曾协商到 9 V / 2 A。

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

表中功率由电量计报告的电池电压与净充电电流相乘得出。同一时段，两路电荷泵在约 9 V 下分别报告约 3.0 A 和 2.8 A。固件随温度和电量调整功率。

支持小米协议的酷泰科充电器也以 `adapter_id 0000a819` 通过了同一握手。该充电器没有低电量时的功率读数。

## 组件

| 路径 | 作用 |
| --- | --- |
| `/usr/local/libexec/liuqin-mipps-auth` | 认证守护进程（Python，GPL-2.0-only，见 `NOTICE`） |
| `/etc/systemd/system/liuqin-mipps-auth.service` | 运行认证程序，等待合同就绪并自动重试失败的握手，默认允许标准 PPS |
| `/etc/systemd/system/liuqin-mipps-battery-auth.service` | 开机时先于适配器认证服务验证两个电量计 |
| `/etc/udev/rules.d/90-liuqin-mipps-auth.rules` | Type-C 对端接入或 USB 供电上线时启动认证，供电离线时清除放行状态 |

守护进程通过项目内核在 PMIC GLINK 电源设备上导出的 `xiaomi` 属性组与充电固件通信：

```
/sys/devices/platform/pmic-glink/pmic_glink.power-supply.0/xiaomi/
```

该属性组不存在时单元自动跳过，软件包可在没有此接口的内核上正常安装。

## 认证流程

1. 开机服务通过 `verify_digest` 向两个电量计分别写入随机挑战，核对 HMAC-SHA256 应答并设置 `authentic` 和 `slave_authentic`。适配器认证时会补验尚未通过的电量计。
2. 等待 PD 合同。服务在 `real_type` 为 `PD` 或 `PD_PPS` 且 `current_state` 为 `SNK_Ready` 时继续。PPS 的 `pdo2` 若仍为零，也会继续等待。最初 8 秒后降低轮询频率，只要同一对端仍在线，晚到的合同也能触发认证。
3. 对报告小米 SVID 的适配器，先取得 Type-C host 数据角色。角色请求失败时，本次尝试在发送适配器命令前停止。
4. 通过 `request_vdm_cmd` 发送 UVDM 命令 1–3、会话种子（命令 4）和随机挑战（命令 5）。取得适配器 ID 后，按原厂顺序先发 REMOVE_COMPENSATION（命令 7），再发 VERIFIED（命令 6）。原装充电器实测使用这一顺序，跳过了会超时的反向认证命令 8。
5. 服务记录小米握手结果 `pd_auth`，并用 `pd_verifed` 告知固件是否放行高压通路。固件按电量与温度选择实际充电模式。

握手使用的密钥包含在认证程序中。

同一轮认证最多尝试三次，两次重试前分别退避 1 秒和 2 秒。日志记录失败的步骤和原因。如果固定 PD 合同未能开启高压通路，服务会等待同一连接转为 PPS 后再试。已放行的结果只能在当前连接内复用。

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

仅当对端报告小米的 `adapter_svid`（`0x2717`）时，服务才请求 host 角色。其他对端保持已协商的数据角色，服务仍会探测小米认证。握手期间会检查连接标识，拔线或更换对端后，旧结果失效。

## 标准 PPS 适配器

服务默认启用 `--allow-unverified-adapter`。第三方适配器即使不应答小米握手，只要双电量计认证通过、PPS 合同仍然就绪，服务就会写入 `pd_verifed=1`，日志中的 `pd_auth=0` 表示此次走标准 PPS。升级会清理旧包附带的 `10-allow-unverified-adapter.conf`。如需仅允许小米认证通路，认证程序支持 `--no-allow-unverified-adapter`。

3 A 线也可使用标准 PPS。对于可拆卸线缆，合规电源接 3 A 线时最多通告 3 A。平板固件在构造 PPS 请求前检查每个可编程电源档位（APDO）的电压范围和最大电流，跳过电压不匹配或请求电流超出上限的档位。

## 充电记录

`tablet-charge-sample.timer` 在系统清醒时约每分钟采样一次，挂起前和唤醒后也各记一条。持续使用电池时，周期记录只保留断电边界。接电样本包含时间、电量、电池温度、USB 和电池侧电压电流（µV、µA），以及固件模式、认证标志、泵侧电压和主副泵电流、请求的热档。电池电流为正表示净充电。连接器温度字段 `pump.connector_temp_decic` 的单位为 0.1 °C，无法读取的测量值记为 `null`。

记录只保存在本机的 `/var/lib/tablet-charge/`（0700）。每行是一条 JSON 记录。当前文件 `samples.jsonl` 达到约 4 MiB 时轮换为 `samples.1.jsonl`，两个文件的权限均为 0600。运行以下命令可查看一次实时采样，结果只输出到终端。

```sh
/usr/libexec/tablet-charge-sample --stdout
```

⟦DRAFT:G-CONN⟧

## 连接器过温保护

USB 供电在线时，内核每秒读取充电口热敏电阻，并将温度传给 ADSP 充电固件。过温阈值和保护动作由原厂充电固件决定。

⟦DRAFT:G-CONN⟧

## 验证

```sh
X=/sys/devices/platform/pmic-glink/pmic_glink.power-supply.0/xiaomi
journalctl -u liuqin-mipps-auth -n 40
cat "$X/real_type" "$X/current_state" "$X/pd_verifed" "$X/fastchg_mode"
cat "$X/bq2597x_bus_voltage" "$X/bq2597x_bus_current" "$X/bq2597x_slave_bus_current"
cat /sys/class/typec/port0/data_role
cat /sys/class/power_supply/qcom-battmgr-usb/voltage_now
B=/sys/class/power_supply/qcom-battmgr-bat
cat "$B/voltage_now" "$B/current_now" "$B/capacity" "$B/temp"
```

电荷泵工作期间 `/sys/class/power_supply/qcom-battmgr-usb/current_now` 可能为 0，判断充电
通路的电流要看电池侧功率与 `bq2597x_bus_*` 属性。

可将电池的 `voltage_now × current_now` 除以 10¹² 得到净充电功率（W）。结合电量、温度和泵侧读数观察高电量时的降流和恒压收尾。

## 排障

| 现象或日志 | 检查 |
| --- | --- |
| 插电后服务没有启动 | 查看 USB 电源的 `online`、Type-C 对端和 `xiaomi/request_vdm_cmd` 节点。缺少该节点时 systemd 会跳过服务。 |
| `role_first=waiting` | 查看同一行的 `real_type`、`current_state` 和 `pdo_pending`。协商未完成时服务会继续等待，固定 PD 尝试结束后也可能在等待 PPS。 |
| `auth_attempt=…/3 failed` | 查看同次尝试的错误。若为 `host role request failed`，核对 `data_role` 和 `role_request=end`；若为电量计验证失败，核对 `authentic` 和 `slave_authentic`。 |
| `role_first=skipped_non_xiaomi` | 对端未报告小米 SVID，服务保留数据角色。查看后续的 `pd_auth` 和 `unverified_adapter`，确认选用的通路。 |
| `unverified_adapter=refused` | 核对 `real_type=PD_PPS`、`current_state=SNK_Ready`，并检查 `pdo2` 是否仍为零。 |
| `pd_verifed=1`，但 `fastchg_mode=0` | 前者表示高压通路已放行。对照电量、电池温度和泵侧电流，确认固件此时是否启用快充。 |
