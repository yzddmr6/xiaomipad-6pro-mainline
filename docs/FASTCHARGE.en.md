# Fast charging

The Xiaomi Pad 6 Pro’s ADSP charger firmware negotiates voltage and current. Once both fuel gauges pass authentication, the system selects a charging path according to the adapter’s capabilities.

| Path | Condition | Result |
| --- | --- | --- |
| MiPPS | Adapter passes Xiaomi’s UVDM handshake | Enables the Xiaomi high-voltage fast-charge path |
| Standard PPS | Adapter does not pass the Xiaomi handshake and the negotiated PPS contract is ready | Enables the standard PPS high-voltage fast-charge path by default |
| Plain PD | Fixed-voltage contract | Uses the negotiated fixed-voltage profile |
| DCP, SDP | Conventional USB power | Firmware charges within the source’s capabilities |

The charging policy is the same with the screen on or off. Cell-temperature protection and the constant-voltage taper remain under firmware control. Computer USB ports supply only the power available from the port and cable. Battery charge can fall when system demand exceeds that input.

## Validation status

Both fuel gauges passed authentication on the tablet. A plain PD adapter negotiated 9 V / 2 A.

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

The power in the table is the fuel gauge’s battery voltage multiplied by the net charging current. Over the same interval, the two charge pumps reported about 3.0 A and 2.8 A at roughly 9 V. The firmware adjusts power as battery temperature and state of charge change.

A third-party Cuktech charger that implements Xiaomi’s protocol also passed the handshake with `adapter_id 0000a819`. No low-state-of-charge power reading was taken with that charger.

## Components

| Path | Role |
| --- | --- |
| `/usr/local/libexec/liuqin-mipps-auth` | authentication daemon (Python, GPL-2.0-only; see `NOTICE`) |
| `/etc/systemd/system/liuqin-mipps-auth.service` | Runs the helper, waits for a ready contract and retries failed handshakes. Allows standard PPS by default. |
| `/etc/systemd/system/liuqin-mipps-battery-auth.service` | authenticates both fuel gauges at boot, before the charger unit |
| `/etc/udev/rules.d/90-liuqin-mipps-auth.rules` | Starts authentication on Type-C partner attach or USB power online. Clears the high-voltage permission when USB power goes offline. |

The daemon talks to the charger firmware through the `xiaomi` attribute group
that the project kernel exports on the PMIC GLINK power-supply device:

```
/sys/devices/platform/pmic-glink/pmic_glink.power-supply.0/xiaomi/
```

The unit is skipped when that group is absent, so the package installs cleanly
on kernels without the interface.

## Authentication sequence

1. At boot, the battery service writes a random challenge to each fuel gauge through `verify_digest`, checks its HMAC-SHA256 response and sets `authentic` and `slave_authentic`. During adapter authentication the helper verifies any gauge that has yet to pass.
2. The service waits for a PD contract. It proceeds when `real_type` is `PD` or `PD_PPS` and `current_state` is `SNK_Ready`. For PPS it also waits if `pdo2` still reads zero. Polling slows after the first 8 seconds. A late contract can still trigger authentication for as long as the same partner stays online.
3. For adapters reporting Xiaomi’s SVID, the helper takes the Type-C host data role first. A failed role request stops that attempt before any adapter command is sent.
4. Through `request_vdm_cmd`, the helper sends UVDM commands 1–3, a session seed (command 4) and a random challenge (command 5). Once it has an adapter ID, it follows the stock completion order, sending REMOVE_COMPENSATION (command 7) followed by VERIFIED (command 6). The original charger used this sequence in testing, skipping reverse authentication (command 8), which timed out on this hardware.
5. The helper logs the Xiaomi handshake result as `pd_auth` and writes `pd_verifed` to tell the firmware whether to allow the high-voltage path. The firmware chooses the active charging mode according to battery level and temperature.

The helper includes the keys used for the handshake.

The service makes up to three attempts per round, waiting 1 second before the first retry and 2 seconds before the second. The journal records the failed step and reason. If a fixed PD contract does not enable the high-voltage path, the service waits for a switch to PPS and tries again on the same connection. A successful result can be reused only for the current attachment.

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

The service requests the host role only when the partner reports Xiaomi’s `adapter_svid` (`0x2717`). Other partners keep their negotiated data role while the helper probes for Xiaomi authentication. The helper checks the attachment identity during the handshake. Unplugging or replacing the partner invalidates the previous result.

## Standard PPS adapters

The service enables `--allow-unverified-adapter` by default. Even if a third-party adapter does not answer the Xiaomi handshake, the service sets `pd_verifed=1` when both gauges have passed and the PPS contract is still ready. In this case the journal shows `pd_auth=0` for standard PPS. Upgrades remove the old packaged `10-allow-unverified-adapter.conf` override. The helper accepts `--no-allow-unverified-adapter` to require Xiaomi authentication for the high-voltage path.

Standard PPS also works with a 3 A cable. A compliant source advertises at most 3 A with a detachable 3 A cable. Before constructing a PPS request, the tablet firmware checks each Augmented Power Data Object (APDO) for its voltage range and maximum current. It skips any entry that cannot supply the requested voltage or current.

## Charging records

`tablet-charge-sample.timer` samples about once a minute while the system is awake. The sleep hook also records one sample before suspend and one after resume. During continuous battery use, the periodic log records only the offline boundary. Powered samples include the time, battery level and temperature, USB and battery voltage and current (µV and µA), firmware mode, authentication flags, pump bus voltage, both pump currents and the requested thermal level. Positive battery current means net charging. The connector temperature field `pump.connector_temp_decic` is in tenths of a degree Celsius. Unavailable measurements are recorded as `null`.

Records are stored only on the tablet in `/var/lib/tablet-charge/` (mode 0700), one JSON object per line. The current file, `samples.jsonl`, rotates to `samples.1.jsonl` at about 4 MiB. Both files have mode 0600. To print one live sample to the terminal without appending to the log, run

```sh
/usr/libexec/tablet-charge-sample --stdout
```

## Connector overtemperature protection

While USB power is online, the kernel reads the charging port thermistor once per second and sends the temperature to the ADSP charger firmware. The stock firmware determines the overtemperature threshold and protective action.

## Verifying

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

`/sys/class/power_supply/qcom-battmgr-usb/current_now` can read 0 while the
charge pumps carry the current; use the battery-side power and the
`bq2597x_bus_*` attributes for the charge path.

Multiply the battery’s `voltage_now` by its `current_now` and divide by 10¹² to obtain net charging power in watts. Read it alongside battery level, temperature and pump current to see how current tapers during the constant-voltage finish.

## Troubleshooting

| Symptom or journal entry | What to check |
| --- | --- |
| Service does not start on plug-in | Check the USB supply’s `online` value, the Type-C partner and the `xiaomi/request_vdm_cmd` node. systemd skips the service if this node is absent. |
| `role_first=waiting` | Check `real_type`, `current_state` and `pdo_pending` on the same line. The service keeps waiting during negotiation and can also wait for PPS after trying fixed PD. |
| `auth_attempt=…/3 failed` | Read the error for that attempt. For `host role request failed`, check `data_role` and `role_request=end`. For a gauge verification failure, check `authentic` and `slave_authentic`. |
| `role_first=skipped_non_xiaomi` | The partner did not report Xiaomi’s SVID, so it keeps its data role. Check the subsequent `pd_auth` and `unverified_adapter` entries for the selected path. |
| `unverified_adapter=refused` | Check for `real_type=PD_PPS` and `current_state=SNK_Ready`, and check whether `pdo2` still reads zero. |
| `pd_verifed=1` but `fastchg_mode=0` | The high-voltage path is allowed. Check battery level, battery temperature and pump currents to see whether the firmware is using fast charging at this point. |
