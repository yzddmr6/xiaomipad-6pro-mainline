# Fast charging

[中文](FASTCHARGE.zh-CN.md)

The Xiaomi Pad 6 Pro negotiates its charging power through the ADSP charger
firmware. The firmware opens the high-voltage charge path only after the adapter
has passed Xiaomi's private authentication, which the stock system performs from
userspace. This page describes how the project integrates that authentication and
how standard USB-PD PPS adapters are handled.

| Path | Condition | Result |
| --- | --- | --- |
| MiPPS | Xiaomi adapter passes UVDM authentication | vendor fast charging, up to the adapter's rated power |
| Standard PPS | any PPS adapter, authentication not passed | 9 V, about 12 W by default; high-voltage path with the switch described below |
| Plain PD, DCP, SDP | no PPS contract | firmware default |

## Validation status

The kernel interface and fuel-gauge authentication are verified on the device:
every attribute of the `xiaomi` group is readable, and both cells authenticate
successfully. After fuel-gauge authentication, standard PD adapters negotiate
9 V / 2 A.

MiPPS adapter authentication and the standard PPS switch are integrated but not
yet validated on hardware. They await validation with a MiPPS-capable Xiaomi
adapter and with a PPS adapter respectively.

## Components

| Path | Role |
| --- | --- |
| `/usr/local/libexec/liuqin-mipps-auth` | authentication daemon (Python, GPL-2.0-only; see `NOTICE`) |
| `/etc/systemd/system/liuqin-mipps-auth.service` | oneshot unit that runs the daemon |
| `/etc/systemd/system/liuqin-mipps-auth.service.d/10-allow-unverified-adapter.conf` | enables the standard PPS switch |
| `/etc/udev/rules.d/90-liuqin-mipps-auth.rules` | starts the unit on Type-C partner attach and USB power online |

The daemon talks to the charger firmware through the `xiaomi` attribute group
that the project kernel exports on the PMIC GLINK power-supply device:

```
/sys/devices/platform/pmic-glink/pmic_glink.power-supply.0/xiaomi/
```

The unit is skipped when that group is absent, so the package installs cleanly
on kernels without the interface.

## Authentication sequence

On USB power online the daemon performs, in order:

1. Fuel-gauge authentication for both cells through `verify_digest`: a random
   challenge is written, the gauge's HMAC-SHA256 response is read back and
   compared, and `authentic` / `slave_authentic` are set from the result.
2. Adapter authentication through `request_vdm_cmd`: the UVDM handshake
   (commands 1 to 3), a session seed (command 4), a random challenge
   (command 5), the verdict (commands 6 and 7) and, on success, the reverse
   authentication response (command 8).
3. `pd_verifed` is written with the verdict. The firmware then selects the
   charge profile; `fastchg_mode`, `power_max` and `real_type` report the result.

The keys used by the handshake are shared across Xiaomi devices and adapters
and are included in the daemon.

## Standard PPS adapters

An adapter that is not Xiaomi-branded cannot pass the handshake, so the firmware
holds the contract at 9 V. With `10-allow-unverified-adapter.conf` installed,
the daemon asserts `pd_verifed` for such adapters when all of the following hold:

- the negotiated contract is `PD_PPS`;
- the cable is rated for the current the high-voltage path draws. When the
  kernel exposes the cable identity, the e-marker must state 5 A. When it does
  not, the decision relies on the USB-PD rule that a source offers more than
  3 A only after reading a 5 A e-marker itself.

The decision and its reason are logged as `unverified_adapter=allowed: …` or
`unverified_adapter=refused: …` in the unit's journal. The desktop notification
reports the negotiated profile (PPS or PD), not MiPPS.

To keep strictly authentication-gated behaviour, remove the drop-in:

```sh
sudo rm -r /etc/systemd/system/liuqin-mipps-auth.service.d
sudo systemctl daemon-reload
```

## Verifying

```sh
X=/sys/devices/platform/pmic-glink/pmic_glink.power-supply.0/xiaomi
journalctl -u liuqin-mipps-auth -n 40
cat "$X/real_type" "$X/pd_verifed" "$X/fastchg_mode" "$X/power_max"
cat /sys/class/power_supply/qcom-battmgr-usb/voltage_now
cat /sys/class/power_supply/qcom-battmgr-bat/current_now
```

The firmware limits the charge profile by state of charge and temperature.
Fast charging is observable between roughly 5 % and 70 % state of charge; above
90 % the firmware selects the standard profile regardless of authentication.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Unit never runs | the `xiaomi` group is missing: the running kernel predates the interface |
| `pd_verifed` stays 0 with a Xiaomi adapter | `journalctl -u liuqin-mipps-auth`: `adapter auth mismatch` means the handshake failed; `pdo2 reports no source PDO` means the adapter offered no PD contract |
| `pd_verifed` is 1 but `fastchg_mode` is 0 | state of charge above 90 %, battery temperature outside the fast-charge window, or the adapter offers no PPS APDO |
| `unverified_adapter=refused` | the reason follows: cable rated below 5 A, or the contract is not PD_PPS |
