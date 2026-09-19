# 快充（MiPPS / 标准 PPS）

Xiaomi Pad 6 Pro（`liuqin`）上有三条充电路径：

| 路径 | 条件 | 典型功率 |
| --- | --- | --- |
| **MiPPS** | 小米私有 UVDM + HMAC 认证通过 | 最高 67 W |
| **标准 PPS** | 适配器提供 PPS APDO | ≈ 12 W（默认）／≈ 40 W（本页所述开关启用后） |
| 纯 PD | 无 PPS | ≈ 12 W |

本页说明标准 PPS 路径为什么默认被限制在 9 V，以及如何显式启用更高电压。

## 1. 门控：`pd_verifed`

固件把高压充电通路挂在一个 XM 属性上：

```
pd_verifed   （XM 属性索引 19）
```

它的语义是「适配器已通过小米私有认证」。

- 小米原装适配器 → 认证通过 → `pd_verifed = 1` → 放开高压通路
- **第三方适配器** → `adapter_svid ≠ 0x2717`，且不持有小米私钥 → 认证必然失败
  → `pd_verifed = 0` → **Vbus 钉死在 9 V**

所以第三方 PPS 适配器会出现这种组合：协议层完全正常，但功率上不去。

```
real_type      = PD_PPS     ← PPS 已协商成功
Vbus           = 8.9 ~ 9.0V ← 被 9V 上限卡住
fastchg_mode   = 0          ← 高压通路未开
```

## 2. 组件

```
/usr/local/libexec/liuqin-mipps-auth                  认证守护进程
/etc/systemd/system/liuqin-mipps-auth.service          oneshot 单元
/etc/systemd/system/liuqin-mipps-auth.service.d/
    10-allow-unverified-adapter.conf                  标准 PPS 开关
/etc/udev/rules.d/90-liuqin-mipps-auth.rules          触发规则
```

### 依赖：内核模块

上述组件依赖一个**设备驱动导出的小米 sysfs 属性组**：

```
/sys/devices/platform/pmic-glink/pmic_glink.power-supply.0/xiaomi/
```

其中 `pd_verifed` / `request_vdm_cmd` 等节点由该驱动提供。
**若该组不存在，认证单元会被 `ConditionPathExistsGlob=` 自动跳过，不会报错**，
设备退回纯 PD 行为。

## 3. 认证流程

`liuqin-mipps-auth` 在 USB 上线后依次做两件事：

1. **燃料计认证**（两个电芯各一次）
   写入 32 字节随机挑战到 `verify_digest`，等 1.4 s 读回，
   比对 `HMAC(FG_KEYS[k], challenge)`。

2. **适配器认证**（UVDM 命令序列 1–8）
   ```
   send_vdm(1..3)                      握手预备
   send_vdm(4, SEEDS[i])               下发种子
   challenge = 16 字节随机
   send_vdm(5, challenge)   → auth     取回适配器响应
   digest = HMAC(KEYS[i], challenge + adapter_id)
   pd_auth = (auth[:32] == digest[:32])      ← 认证判定
   send_vdm(6, pd_auth ? "01000000" : "00000000")
   若通过：send_vdm(8, digest[32:])        反向认证
   send_vdm(7, 同上)
   ```
   最后写入门控：
   ```python
   write_node(root, "pd_verifed", "1" if pd_auth else "0")
   ```

`KEYS[]` 是小米私钥。第三方适配器无法算出 `digest`，因此 `pd_auth` 恒为 0。

## 4. 启用标准 PPS 快充

`10-allow-unverified-adapter.conf` 会给守护进程加上
`--allow-unverified-adapter`，在认证失败时仍然置位门控：

```python
gate = pd_auth or args.allow_unverified_adapter
write_node(root, "pd_verifed", "1" if gate else "0")
```

**关闭**（恢复到认证门控行为）：

```sh
sudo rm -rf /etc/systemd/system/liuqin-mipps-auth.service.d
sudo systemctl daemon-reload
sudo systemctl restart liuqin-mipps-auth.service
```

## 5. 验证

```sh
X=$(ls -d /sys/devices/platform/pmic-glink/*/xiaomi | head -1)

cat "$X/pd_verifed"        # 期望 1
cat "$X/fastchg_mode"      # 期望 1
cat /sys/class/power_supply/qcom-battmgr-usb/voltage_now    # 期望 > 9.05 V
cat /sys/class/power_supply/qcom-battmgr-bat/current_now
journalctl -u liuqin-mipps-auth -n 30
```

### ⚠️ 必须在合适的电量区间测量

**`fastchg_mode` 与 SoC 强相关，高电量下任何配置都测不出快充。**

| SoC | 行为 |
| --- | --- |
| 0 ~ 5 % | 深度亏电，低电流保护恢复 |
| **5 ~ 70 %** | **正常快充区间** |
| 70 ~ 90 % | 电流逐步回落 |
| > 90 % | 固件主动禁快充：`charge_type=Standard`、`fastchg_mode=0`、`apdo_max` 压到 20 |

**请勿在 SoC > 90 % 时判定配置失效。**

## 6. 影响范围与注意事项

- **小米原装适配器**：认证通过，行为与启用前完全一致
- **纯 PD 适配器**：无 PPS，固件本就不走高电压通路，置位无副作用
- **需要 root**：`xiaomi/pd_verifed` 为 `root:root 0644`

> **注意**：该开关会**覆盖适配器认证结果**——即告诉固件一个未经认证的适配器是可信的。
> 这正是把它做成独立、可删除的 drop-in 而不是写进基础单元的原因。
> 是否默认启用由镜像构建策略决定。

## 7. 排障

| 现象 | 排查方向 |
| --- | --- |
| 单元不运行 | `xiaomi/` 属性组不存在 → 驱动未加载；确认 `ConditionPathExistsGlob` 指向的节点 |
| `pd_verifed` 写不进去 | 节点是只读的 → 驱动版本不匹配 |
| `pd_verifed=1` 但 `fastchg_mode=0` | **先看 SoC**；再确认适配器提供 PPS APDO |
| 功率仍低但 `fastchg_mode=1` | 看 `usb_type`：方括号落在 `[PD_PPS]` 才是 PPS；落在 `[PD]` 是 9 V 固定档，拔插重协商 |
| 电流恒定不涨 | 固件锁流（低电量保护），非故障；确认 `battery/temp` 未过热降额 |
