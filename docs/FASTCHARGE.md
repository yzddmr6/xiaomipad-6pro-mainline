# 快充

[English](FASTCHARGE.en.md)

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

MiPPS 适配器认证与标准 PPS 开关已集成，但尚未在硬件上验证，分别有待使用支持 MiPPS 的
小米适配器和 PPS 适配器完成验收。

## 组件

| 路径 | 作用 |
| --- | --- |
| `/usr/local/libexec/liuqin-mipps-auth` | 认证守护进程（Python，GPL-2.0-only，见 `NOTICE`） |
| `/etc/systemd/system/liuqin-mipps-auth.service` | 运行守护进程的 oneshot 单元 |
| `/etc/systemd/system/liuqin-mipps-auth.service.d/10-allow-unverified-adapter.conf` | 启用标准 PPS 开关 |
| `/etc/udev/rules.d/90-liuqin-mipps-auth.rules` | Type-C 连接与 USB 供电上线时拉起单元 |

守护进程通过项目内核在 PMIC GLINK 电源设备上导出的 `xiaomi` 属性组与充电固件通信：

```
/sys/devices/platform/pmic-glink/pmic_glink.power-supply.0/xiaomi/
```

该属性组不存在时单元自动跳过，软件包可在没有此接口的内核上正常安装。

## 认证流程

USB 供电上线后，守护进程依次执行：

1. 通过 `verify_digest` 对两个电芯做燃料计认证：写入随机挑战，读回燃料计的 HMAC-SHA256
   应答并比对，按结果设置 `authentic` / `slave_authentic`。
2. 通过 `request_vdm_cmd` 做适配器认证：UVDM 握手（命令 1–3）、会话种子（命令 4）、随机
   挑战（命令 5）、判定（命令 6、7），通过时再发反向认证应答（命令 8）。
3. 按判定写入 `pd_verifed`。固件随后选择充电档位，结果体现在 `fastchg_mode`、`power_max`
   与 `real_type`。

握手所用密钥在小米设备与适配器之间通用，已包含在守护进程中。

## 标准 PPS 适配器

非小米适配器无法通过握手，固件会把合同保持在 9 V。安装 `10-allow-unverified-adapter.conf`
后，守护进程在同时满足下列条件时为此类适配器置位 `pd_verifed`：

- 已协商的合同为 `PD_PPS`；
- 线缆额定电流满足高压通路的需求。内核导出线缆身份时，e-marker 必须标注 5 A；未导出时，
  依据 USB-PD 规范中"源端只有自行读到 5 A e-marker 才会提供 3 A 以上档位"的规则做判定。

判定及其原因记录在单元日志中，形如 `unverified_adapter=allowed: …` 或
`unverified_adapter=refused: …`。桌面通知显示实际协商的档位（PPS 或 PD），不会显示 MiPPS。

如需保持严格的认证门控行为，删除该 drop-in：

```sh
sudo rm -r /etc/systemd/system/liuqin-mipps-auth.service.d
sudo systemctl daemon-reload
```

## 验证

```sh
X=/sys/devices/platform/pmic-glink/pmic_glink.power-supply.0/xiaomi
journalctl -u liuqin-mipps-auth -n 40
cat "$X/real_type" "$X/pd_verifed" "$X/fastchg_mode" "$X/power_max"
cat /sys/class/power_supply/qcom-battmgr-usb/voltage_now
cat /sys/class/power_supply/qcom-battmgr-bat/current_now
```

固件按电量与温度限制充电档位。快充在电量约 5 %–70 % 区间可观察到；电量高于 90 % 时无论
是否认证固件都选择标准档位。

## 排障

| 现象 | 检查 |
| --- | --- |
| 单元从不运行 | `xiaomi` 属性组不存在：运行中的内核早于该接口 |
| 小米适配器下 `pd_verifed` 仍为 0 | 看 `journalctl -u liuqin-mipps-auth`：`adapter auth mismatch` 表示握手失败；`pdo2 reports no source PDO` 表示适配器未提供 PD 合同 |
| `pd_verifed` 为 1 但 `fastchg_mode` 为 0 | 电量高于 90 %、电池温度在快充窗口之外，或适配器未提供 PPS APDO |
| `unverified_adapter=refused` | 原因随后给出：线缆额定低于 5 A，或合同不是 PD_PPS |
