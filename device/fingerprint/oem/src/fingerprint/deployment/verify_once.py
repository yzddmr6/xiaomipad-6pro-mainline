#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""Real fprintd Claim/Verify on the system bus, with bounded cleanup."""
import signal
from gi.repository import Gio, GLib

def verify(username, finger, expected=None, announce=True):
    connection = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    flags = Gio.DBusProxyFlags.DO_NOT_AUTO_START
    manager = Gio.DBusProxy.new_sync(connection, flags, None, "net.reactivated.Fprint",
        "/net/reactivated/Fprint/Manager", "net.reactivated.Fprint.Manager", None)
    paths = manager.call_sync("GetDevices", None, Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
    device = None
    for path in paths:
        proxy = Gio.DBusProxy.new_sync(connection, flags, None, "net.reactivated.Fprint", path,
                                       "net.reactivated.Fprint.Device", None)
        name = proxy.get_cached_property("name")
        if name and name.unpack() == "FPC1264 OEM on liuqin":
            device = proxy
            break
    if device is None:
        raise RuntimeError("OEM fprintd device discovery failed")
    loop = GLib.MainLoop()
    observed = None
    claimed = started = False
    def status(proxy, sender, name, parameters):
        nonlocal observed
        if name == "VerifyStatus":
            result, done = parameters.unpack()
            if announce:
                print("fprintd_verify_status=" + result, flush=True)
            if done:
                observed = result
                loop.quit()
    def cancel(signum=None, frame=None):
        nonlocal observed
        observed = "cancelled"
        loop.quit()
        return GLib.SOURCE_REMOVE
    original_handlers = {s:signal.signal(s, cancel) for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    timer = GLib.timeout_add_seconds(200, cancel)
    device.connect("g-signal", status)
    try:
        device.call_sync("Claim", GLib.Variant("(s)", (username,)), Gio.DBusCallFlags.NONE, 10000, None)
        claimed = True
        enrolled = device.call_sync("ListEnrolledFingers", GLib.Variant("(s)", (username,)), Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
        if finger not in enrolled:
            raise RuntimeError("The requested real template is not loaded by fprintd")
        device.call_sync("VerifyStart", GLib.Variant("(s)", (finger,)), Gio.DBusCallFlags.NONE, 10000, None)
        started = True
        loop.run()
        if observed not in ("verify-match", "verify-no-match"):
            raise RuntimeError("Real fingerprint verification did not complete: " + str(observed))
        if expected and observed != expected:
            raise RuntimeError("Fingerprint acceptance expected " + expected + " but received " + observed)
        return observed
    finally:
        try:
            if started:
                try:
                    device.call_sync("VerifyStop", None, Gio.DBusCallFlags.NONE, 10000, None)
                except GLib.Error as error:
                    if "NoActionInProgress" not in str(error):
                        raise
        finally:
            try:
                if claimed:
                    device.call_sync("Release", None, Gio.DBusCallFlags.NONE, 10000, None)
            finally:
                if GLib.MainContext.default().find_source_by_id(timer):
                    GLib.source_remove(timer)
                for signum, handler in original_handlers.items():
                    signal.signal(signum, handler)
