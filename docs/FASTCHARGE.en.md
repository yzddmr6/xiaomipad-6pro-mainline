# Fast charging

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

**MiPPS adapter authentication is verified on hardware.** With an original
Xiaomi 67 W MDY-15-EQ adapter (USB-A) and the original A-to-C cable, the daemon
completed the real handshake and fast charging followed:

| Reading | Value |
| --- | --- |
| `adapter_svid` | `0x2717` |
| `adapter_id` | `0000a661` |
| `real_type` / `current_state` | `PD_PPS` / `SNK_Ready` |
| `pd_auth` / `pd_verifed` / `fastchg_mode` | `1` / `1` / `1` |
| data role while charging | `host` |
| battery-side power, 12 samples in 56 s | 43.99 – 45.94 W (mean 45.4 W) |
| state of charge / battery temperature | 34 – 36 % / 34.0 – 35.5 °C |

"Battery-side power" is `Vbat × Ibat` as reported by the fuel gauge, with the two
charge pumps reporting about 3.0 A and 2.8 A at roughly 9 V. It is not a
measurement at the adapter output, and it is not a claim that the adapter
delivers its full 67 W: the firmware keeps adjusting the charge profile as
temperature and state of charge change.

A third-party charger that implements Xiaomi's protocol (Cuktech) also passed
the same handshake with `adapter_id 0000a819`, so the path is not limited to
first-party adapters. No low-state-of-charge power measurement was taken with
that charger.

The standard PPS switch is integrated but still awaits validation with a PPS
adapter that is not Xiaomi-branded.

## Components

| Path | Role |
| --- | --- |
| `/usr/local/libexec/liuqin-mipps-auth` | authentication daemon (Python, GPL-2.0-only; see `NOTICE`) |
| `/etc/systemd/system/liuqin-mipps-auth.service` | oneshot unit that runs the daemon with `--role-first` |
| `/etc/systemd/system/liuqin-mipps-battery-auth.service` | authenticates both fuel gauges at boot, before the charger unit |
| `/etc/udev/rules.d/90-liuqin-mipps-auth.rules` | starts the unit on Type-C partner attach and USB power online |

The daemon talks to the charger firmware through the `xiaomi` attribute group
that the project kernel exports on the PMIC GLINK power-supply device:

```
/sys/devices/platform/pmic-glink/pmic_glink.power-supply.0/xiaomi/
```

The unit is skipped when that group is absent, so the package installs cleanly
on kernels without the interface.

## Authentication sequence

The daemon runs once per Type-C attach, in this order:

1. Fuel-gauge authentication for both cells through `verify_digest`: a random
   challenge is written, the gauge's HMAC-SHA256 response is read back and
   compared, and `authentic` / `slave_authentic` are set from the result. The
   boot unit does this once at boot; the daemon repeats it only when the flags
   are missing.
2. Wait for the source to settle. The firmware does not answer the handshake
   while it is still negotiating, so the daemon waits up to 8 s until `real_type`
   reports `PD` or `PD_PPS` and `current_state` reaches `SNK_Ready`.
3. Take the Type-C host data role, which is the role the stock system holds
   while a Xiaomi adapter is attached. The role is requested once per attach,
   and a refused request stops the run before any adapter command is sent.
4. Adapter authentication through `request_vdm_cmd`: the UVDM handshake
   (commands 1 to 3), a session seed (command 4), a random challenge
   (command 5), then the Android completion order, REMOVE_COMPENSATION (command 7) followed
   by VERIFIED (command 6). Command 8, the reverse authentication response, is not sent: it
   timed out on this hardware, and the stock system's own log shows the same
   7 → 6 order.
5. `pd_verifed` is written with the verdict. The firmware then selects the
   charge profile; `fastchg_mode`, `power_max` and `real_type` report the result.

The keys used by the handshake are shared across Xiaomi devices and adapters
and are included in the daemon.

The order matters. Taking the host role after the handshake, or finishing with
the old 6 → 8 → 7 sequence, completed authentication but did not sustain fast
charging on this hardware. The attempt is remembered per Type-C attach, so a
failing adapter is not retried in a loop.

A full run reports the sequence in the journal:

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

### Data role

The host role is requested only when the attached partner reports Xiaomi's
`adapter_svid` (`0x2717`). Any other source — a computer, a dock, a third-party
charger — keeps the negotiated device role, so USB device mode and USB
networking keep working while charging. Those sources are still probed for
authentication, and a Xiaomi phone presents the same SVID and is treated the same
way as a Xiaomi adapter.

The daemon waits for the negotiated contract before reading SVID and data role.
It checks the Type-C partner throughout the handshake, so detach or replacement
cannot reuse a previous verdict. Hardware testing must still cover rapid replug.

## Standard PPS adapters

Only adapters that pass Xiaomi authentication enable the high-voltage path by default.
This includes compatible third-party adapters; branding alone is not the criterion.
Other adapters retain the firmware's ordinary PD/PPS contract. The old packaged
`10-allow-unverified-adapter.conf` is removed on upgrade. The battery authentication
unit is enabled on installation and upgrade.

The command-line `--allow-unverified-adapter` override remains an explicit opt-in,
subject to a PD_PPS contract and the cable capability check (3 A is rejected;
5 A is accepted; without cable identity it relies on source-side enforcement).
The shipped service does not pass this option, even for a 5 A cable or a charger
that ignores UVDM. Authentication failures and detach clear `pd_verifed`; only a
successful handshake bound to the current Type-C partner can be reused.

## Verifying

```sh
X=/sys/devices/platform/pmic-glink/pmic_glink.power-supply.0/xiaomi
journalctl -u liuqin-mipps-auth -n 40
cat "$X/real_type" "$X/pd_verifed" "$X/fastchg_mode" "$X/power_max"
cat /sys/class/typec/port0/data_role
cat /sys/class/power_supply/qcom-battmgr-usb/voltage_now
cat /sys/class/power_supply/qcom-battmgr-bat/current_now
```

`/sys/class/power_supply/qcom-battmgr-usb/current_now` can read 0 while the
charge pumps carry the current; use the battery-side power and the
`bq2597x_bus_*` attributes for the charge path.

The firmware limits the charge profile by state of charge and temperature.
Fast charging is observable between roughly 5 % and 70 % state of charge; above
90 % the firmware selects the standard profile regardless of authentication, and
a battery temperature outside the fast-charge window has the same effect.

The unit runs at most once per Type-C attach. To try again, unplug and replug the
adapter; restarting the unit alone reports `role_first=already_attempted_this_attach`
until the attach token changes.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Unit never runs | the `xiaomi` group is missing: the running kernel predates the interface |
| `pd_verifed` stays 0 with a Xiaomi adapter | `journalctl -u liuqin-mipps-auth`: `adapter auth mismatch` means the handshake failed; `pdo2 reports no source PDO` means the adapter offered no PD contract |
| `role_first=skipped_non_xiaomi` | the adapter does not report `adapter_svid 0x2717`, so the host role is left alone and MiPPS cannot engage. Use the standard PPS switch if it offers PPS |
| `role_first=source_not_ready` | the adapter did not reach `SNK_Ready` with a PD contract within the timeout: plain 5 V, DCP, or a negotiation that is still running |
| `role_first=no_host_no_adapter_commands` | the port refused the host role and no adapter command was sent. Replug; if it persists, check `/sys/class/typec/port0/data_role` |
| `role_first=already_attempted_this_attach` | this attach already ran. Replug instead of restarting the unit |
| `pd_verifed` is 1 but `fastchg_mode` is 0 | state of charge above 90 %, battery temperature outside the fast-charge window, or the adapter offers no PPS APDO |
| `unverified_adapter=refused` | the reason follows: cable rated below 5 A, or the contract is not PD_PPS |
