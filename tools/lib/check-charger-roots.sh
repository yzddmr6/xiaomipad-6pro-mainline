#!/bin/sh
# SPDX-License-Identifier: MIT
# This board's charger runtime has no persistent root and no module loader.
set -eu
[ "$#" = 1 ] && [ -r "$1" ] || { echo 'usage: check-charger-roots.sh BUILT_CONFIG' >&2; exit 64; }
for symbol in DRM_MSM DRM_MSM_DSI DRM_PANEL_NOVATEK_NT36532 \
    SM_DISPCC_8450 TYPEC_MUX_FSA4480 QCOM_PMIC_GLINK DRM_AUX_HPD_BRIDGE \
    BACKLIGHT_KTZ8866 USB_CONFIGFS USB_CONFIGFS_NCM USB_DWC3 USB_DWC3_QCOM \
    QCOM_Q6V5_PAS BATTERY_QCOM_BATTMGR INPUT_PM8941_PWRKEY \
    IIO QCOM_SPMI_ADC5 QCOM_VADC_COMMON; do
    grep -qx "CONFIG_$symbol=y" "$1" || {
        echo "charger root dependency CONFIG_$symbol must be built in" >&2
        exit 1
    }
done
printf 'PASS: charger display, USB, battery and key root dependencies are built in\n'
