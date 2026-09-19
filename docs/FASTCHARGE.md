# Fast charging (MiPPS / standard PPS)

The Xiaomi Pad 6 Pro (`liuqin`) has three charging paths:

| Path | Condition | Typical power |
| --- | --- | --- |
| **MiPPS** | Xiaomi private UVDM + HMAC authentication succeeds | up to 67 W |
| **Standard PPS** | the adapter offers a PPS APDO | ≈ 12 W by default, ≈ 40 W with the switch below |
| Plain PD | no PPS | ≈ 12 W |

This page explains why the standard PPS path is held at 9 V by default, and how
to explicitly enable a higher voltage.

## 1. The gate: `pd_verifed`

The firmware hangs the high-voltage charge path on a single XM property:

```
pd_verifed   (XM property index 19)
```

Its meaning is "the adapter passed Xiaomi's private authentication".

- Genuine Xiaomi adapter → authentication succeeds → `pd_verifed = 1` → high-voltage path opens
- **Third-party adapter** → `adapter_svid ≠ 0x2717` and no access to Xiaomi's private
  keys → authentication can never succeed → `pd_verifed = 0` → **Vbus pinned at 9 V**

So a third-party PPS adapter shows this combination — the protocol layer is
perfectly healthy, but the power does not follow:

```
real_type      = PD_PPS     ← PPS negotiated fine
Vbus           = 8.9 ~ 9.0V ← held at the 9 V ceiling
fastchg_mode   = 0          ← high-voltage path closed
```

## 2. Components

```
/usr/local/libexec/liuqin-mipps-auth                 authentication daemon
/etc/systemd/system/liuqin-mipps-auth.service        oneshot unit
/etc/systemd/system/liuqin-mipps-auth.service.d/
    10-allow-unverified-adapter.conf                 standard-PPS switch
/etc/udev/rules.d/90-liuqin-mipps-auth.rules         trigger rules
```

### Dependency: a kernel module

These components depend on a **Xiaomi sysfs attribute group exported by a device
driver**:

```
/sys/devices/platform/pmic-glink/pmic_glink.power-supply.0/xiaomi/
```

`pd_verifed`, `request_vdm_cmd` and friends live there. **When that group is
absent the unit is skipped automatically by `ConditionPathExistsGlob=` and no
error is produced** — the device simply falls back to plain PD behaviour.

## 3. Authentication flow

Once USB comes online, `liuqin-mipps-auth` does two things.

1. **Fuel gauge authentication**, once per cell.
   It writes a 32-byte random challenge to `verify_digest`, waits 1.4 s, reads it
   back and compares against `HMAC(FG_KEYS[k], challenge)`.

2. **Adapter authentication**, the UVDM command sequence 1–8:
   ```
   send_vdm(1..3)                      handshake preamble
   send_vdm(4, SEEDS[i])               seed selection
   challenge = 16 random bytes
   send_vdm(5, challenge)   -> auth    adapter response
   digest = HMAC(KEYS[i], challenge + adapter_id)
   pd_auth = (auth[:32] == digest[:32])     <- the verdict
   send_vdm(6, pd_auth ? "01000000" : "00000000")
   if it passed: send_vdm(8, digest[32:])   reverse authentication
   send_vdm(7, same payload)
   ```
   Finally it writes the gate:
   ```python
   write_node(root, "pd_verifed", "1" if pd_auth else "0")
   ```

`KEYS[]` are Xiaomi's private keys. A third-party adapter cannot compute
`digest`, so `pd_auth` is always 0.

## 4. Enabling standard PPS fast charging

`10-allow-unverified-adapter.conf` adds `--allow-unverified-adapter` to the
daemon, which asserts the gate even when authentication failed:

```python
gate = pd_auth or args.allow_unverified_adapter
write_node(root, "pd_verifed", "1" if gate else "0")
```

**To turn it back off** (returning to authentication-gated behaviour):

```sh
sudo rm -rf /etc/systemd/system/liuqin-mipps-auth.service.d
sudo systemctl daemon-reload
sudo systemctl restart liuqin-mipps-auth.service
```

## 5. Verifying

```sh
X=$(ls -d /sys/devices/platform/pmic-glink/*/xiaomi | head -1)

cat "$X/pd_verifed"        # expect 1
cat "$X/fastchg_mode"      # expect 1
cat /sys/class/power_supply/qcom-battmgr-usb/voltage_now    # expect > 9.05 V
cat /sys/class/power_supply/qcom-battmgr-bat/current_now
journalctl -u liuqin-mipps-auth -n 30
```

### ⚠️ Measure at the right state of charge

**`fastchg_mode` is strongly SoC-dependent, and no configuration produces fast
charging at a high state of charge.**

| SoC | Behaviour |
| --- | --- |
| 0 – 5 % | deeply discharged, low-current recovery |
| **5 – 70 %** | **the normal fast-charge window** |
| 70 – 90 % | current tapers |
| > 90 % | the firmware disables fast charging outright: `charge_type=Standard`, `fastchg_mode=0`, `apdo_max` compressed to 20 |

**Do not conclude that a configuration failed while above 90 % SoC.**

## 6. Scope and caveats

- **Genuine Xiaomi adapters** are unaffected: when authentication succeeds the
  behaviour is identical to before.
- **Plain PD adapters** are unaffected: without PPS the firmware never takes the
  high-voltage path, so asserting the gate changes nothing.
- **Root is required**: `xiaomi/pd_verifed` is `root:root 0644`.

> **Note**: this switch **overrides the adapter authentication result** — it tells
> the firmware that an unverified adapter is trusted. That is precisely why it is
> a separate, removable drop-in rather than part of the base unit. Whether images
> enable it by default is a build-policy decision.

## 7. Troubleshooting

| Symptom | Where to look |
| --- | --- |
| The unit never runs | the `xiaomi/` group is missing — the driver is not loaded; check the path in `ConditionPathExistsGlob` |
| `pd_verifed` cannot be written | the attribute is read-only — driver revision mismatch |
| `pd_verifed=1` but `fastchg_mode=0` | **check SoC first**; then confirm the adapter actually offers a PPS APDO |
| `fastchg_mode=1` but power is still low | inspect `usb_type`: the bracketed entry must be `[PD_PPS]`. `[PD]` means a fixed 9 V contract — re-plug to renegotiate |
| Current is flat and does not rise | the firmware is limiting current during low-SoC recovery; not a fault. Confirm `battery/temp` shows no thermal derating |
