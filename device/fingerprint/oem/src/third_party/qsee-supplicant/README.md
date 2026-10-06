# Qualcomm Secure Execution Environment supplicant

[![Continuous integration](https://github.com/wrobelda/qsee-supplicant/actions/workflows/ci.yml/badge.svg)](https://github.com/wrobelda/qsee-supplicant/actions/workflows/ci.yml)

A Trusted Execution Environment (TEE) is an isolated execution environment
separate from the host operating system. Qualcomm Secure Execution Environment
(QSEE) is the older of Qualcomm's TEE implementations. Qualcomm also provides the
newer Qualcomm TEE (QTEE) object interface.

Programs running inside a TEE are called Trusted
Applications (TAs). A TA can perform security-sensitive work without giving the
host operating system direct access to its memory and internal state.

These Qualcomm TEE implementations are used by a range of Qualcomm
systems-on-chip (SoCs), typically in Android devices, for which Qualcomm
provides a complete userspace software stack.

A userspace stack is required because some TAs need operations that only the
host operating system can provide, such as reading and writing files. They request
those operations through listener services. These services run in the host
operating system as handlers in a userspace daemon called a supplicant. An
additional daemon is required to manage dynamic TA lifetimes by loading them
into QSEE and keeping them loaded.

As mentioned, Qualcomm provides their closed-source stack for Android. Its
proprietary nature aside, it cannot be used on native Linux because it uses
Qualcomm's proprietary QSEECOM kernel interface, not the Linux TEE subsystem.

Qualcomm *does* provide a native, open-source Linux stack for the newer QTEE
technology. No equivalent stack exists for QSEE.

This project provides that missing stack: userspace daemons that manage QSEE TA
lifetimes and provide operating-system services to TAs through listeners.

## QSEE communication interface and listener services

Older QSEE systems use the QSEE communication interface (QSEECOM) to load TAs,
send commands to them, and carry numeric listener requests. `qsee-supplicant`
implements the userspace listener side of the Linux QSEECOM TEE driver. It is a
single machine-wide service because the kernel provides one listener request
queue for each QSEECOM TEE device, not one queue for each TA.

This project requires the QSEECOM TEE driver provided by
[`goodix-fp-spi-linux`](https://github.com/wrobelda/goodix-fp-spi-linux). The
driver implements QSEECOM through the Linux TEE subsystem. It is not yet
included in mainline Linux, so the running kernel must include the driver from
that repository.

The driver exposes a pair of devices: the `/dev/teeN` client device and the
`/dev/teeprivN` privileged device. Driver probe fails and unregisters both if
either device cannot be registered. The loader requires both, because it
attaches through the client device and loads through the privileged device. A
privileged-only setup is incomplete, so the loader rejects it rather than load
a TA that ordinary clients cannot access.

As explained in the overview, some TAs need operations that only the host
operating system can provide, such as reading and writing files, and they
request those operations through listener services. Each listener has a
numeric service identifier and a defined request and response format. A
supplicant is a daemon in the normal Linux environment: it registers the
listeners, receives requests from the TEE, performs the permitted operations,
and returns the results.

The current implementation provides two filesystem listener protocols. Its
listener handlers are separate from the kernel request loop, so further
QSEECOM listener protocols can be added without rewriting device discovery,
registration, or request delivery.

## Programs and services

The project installs two programs because listener registration and TA
loading have different lifetimes and different security domains. The
supplicant handles machine-wide requests and sensitive persistent objects.
Because they are separate processes, a loader cannot reach the supplicant's
request state or its open storage descriptors.

- `qsee-supplicant` is the listener daemon. One instance serves the whole
  machine. It registers the File System (FS) and GlobalPlatform File System
  (GPFS) listener services. It answers their requests and stores their files
  under the configured state directory.
- `qsee-app-loader` keeps one named TA loaded. If the TA is already resident,
  it attaches to it; if the TA is absent, it loads it. It then keeps its
  kernel session open. Run one loader process for each TA the system needs to
  keep resident. The program takes the TA firmware name as its only argument.

On Android, one Qualcomm daemon called `qseecomd` does both jobs. Together,
these two programs provide the `qseecomd` functions required by the supported
TAs and listener services.

The supplied systemd units follow the same split:

- `qsee-supplicant.service` runs the listener daemon.
- `qsee-app-loader@.service` is a template unit. An instance such as
  `qsee-app-loader@example-ta.service` runs `qsee-app-loader example-ta`.

The template starts the supplicant and waits until it has registered all
listener services. Only then does it attach to the TA or load it. Both
programs report readiness to systemd: the supplicant after listener
registration, and a loader after its TA session is open.

OpenRC 0.62 and later uses the same readiness notifications. Create one loader
service link for each TA, using the TA firmware name as the suffix:

```sh
ln -s qsee-app-loader /etc/init.d/qsee-app-loader.example-ta
rc-update add qsee-app-loader.example-ta default
```

The loader service has a hard dependency on `qsee-supplicant`, so OpenRC waits
for listener registration before it starts the loader instance.

The kernel sessions live in separate processes on purpose:

- Restarting the supplicant re-registers its listeners; it does not close a
  loader's session or unload a resident TA.
- Restarting one loader affects only its own TA.
- The supplicant has no list of TAs and never loads one itself.

A service that needs one TA for its whole lifetime can depend on that TA's
loader instance. For example:

```ini
[Unit]
Requires=qsee-app-loader@example-ta.service
After=qsee-app-loader@example-ta.service
```

The loader acquires its TA in this order:

1. It first tries to open an unprivileged session to the named TA.
2. The QSEECOM TEE driver returns `ENOENT` when the named TA is not resident.
   Only that result makes the loader load the TA through the privileged
   device.
3. If the load fails, the loader tries the unprivileged session once more.
   Another process may have won the load race. If the retry also fails, the
   loader reports the original load error.

This order also lets a loader restart and re-attach while another client
session keeps the TA resident.

The supplied units do not name any TA consumers. Which TAs a system loads, and
which services use them, is system-specific policy. A consumer that supports
dynamic TA availability can watch that availability instead of using a fixed
unit dependency.

## Supported listener services

A QSEECOM TA can ask normal Linux software to perform an operation by
sending a request to a numbered listener service. The supplicant
registers each supported listener with the kernel through `/dev/teeprivN`. The
kernel delivers requests for all registered listeners through the Linux TEE
subsystem's `TEE_IOC_SUPPL_RECV` receive operation. The supplicant reads the
listener number from `arg.func` and picks the matching handler.

This release implements the two file services below:

| Listener | Service | Status |
|---:|---|---|
| 10 | File system (FS) | Implemented and tested |
| 28672 | GlobalPlatform file system (GPFS) | Implemented and tested |

FS and GPFS are separate protocols. FS provides conventional operations on
files and directories. GPFS stores whole objects with its own request format;
it replaces objects atomically and creates a backup copy when a request asks
for one. Supporting one service does not provide the other.

Qualcomm's Android `qseecomd` registers more numeric listener services. The
following services are known but are not implemented by this project:

| Listener | Service |
|---:|---|
| 11 | Time |
| 4352 | GlobalPlatform request cancellation |
| 8192 | Replay Protected Memory Block (RPMB) |
| 12288 | Service labelled `SSD` by `qseecomd`; its full name has not been established |
| 16384 | Secure user interface (secure UI) |
| 36864 | Interrupt |
| 45056 | Secure processor |

This is not a complete QSEECOM service registry; it lists the listener numbers
seen in the available `qseecomd` traces. Those traces held one more
registration, but its service number was not captured. Add a new service only
with its request format, response format, and error behavior documented and
tested. A TA needs only the listeners it actually uses.

Qualcomm's Mink inter-process communication (MinkIPC)
[`minkipc` listener implementations](https://github.com/qualcomm/minkipc/tree/main/listeners)
are a public reference for FS 10, time 11, RPMB 8192, and GPFS 28672. MinkIPC
is the newer and broader object interface, but its listener services still use
the numeric service identifiers and shared-buffer message formats of older
QSEECOM. The difference is delivery: legacy QSEECOM registers numeric sessions
and uses one supplicant receive queue, while MinkIPC registers the same
services as callback objects with QTEE. The protocol definitions and handlers
therefore apply to both; only the registration and request-delivery code
differs. This project implements the legacy QSEECOM side and used MinkIPC to
verify the service protocols. It does not include the MinkIPC transport or
runtime, only selected message-layout definitions derived from MinkIPC.

## Build and test

```sh
make
make check
make DESTDIR="$pkgdir" install
```

The tests use a temporary directory and do not require TEE hardware. They
cover listener request parsing, safe path handling, restrictive file modes,
atomic GPFS object replacement, and protocol-required backup files.

### Distribution packages

Release assets include ARM64 packages for Debian, Ubuntu, Fedora, and
postmarketOS. Install the package appropriate for the operating system:

```sh
sudo apt install ./qsee-supplicant_0.1.1-1_arm64.deb
sudo dnf install ./qsee-supplicant-0.1.1-1.*.aarch64.rpm

# postmarketOS with systemd
sudo apk add --allow-untrusted \
  ./qsee-supplicant-0.1.1-r0.apk \
  ./qsee-supplicant-systemd-0.1.1-r0.apk \
  ./qsee-supplicant-doc-0.1.1-r0.apk

# postmarketOS with OpenRC
sudo apk add --allow-untrusted \
  ./qsee-supplicant-0.1.1-r0.apk \
  ./qsee-supplicant-openrc-0.1.1-r0.apk \
  ./qsee-supplicant-doc-0.1.1-r0.apk
```

The APK build separates the programs from service-manager integration. Install
`qsee-supplicant-systemd` on a systemd postmarketOS installation or
`qsee-supplicant-openrc` on an OpenRC installation. Install exactly one of
these service packages alongside the base package, as shown above. The optional
`qsee-supplicant-doc` package contains the README and license texts.

The release also contains the tagged source tree and ARM64 binary archives for
glibc and musl systems. The binary archives contain both systemd and OpenRC
service files, but do not register them with the service manager. Distribution
packages are preferred when one is available.

Package installation does not start either service. The Debian and Ubuntu
package also leaves both services disabled. The Fedora package applies the
operating system's systemd preset policy. The QSEECOM TEE driver and the
trusted application (TA) selection are machine-specific; enable the services
only after the kernel and hardware integration are installed.

Debian and Ubuntu packages can be built with `dpkg-buildpackage -b -us -uc`.
Fedora packages can be built from
[`packaging/rpm/qsee-supplicant.spec`](packaging/rpm/qsee-supplicant.spec).
The postmarketOS packages are defined by
[`packaging/alpine/APKBUILD`](packaging/alpine/APKBUILD).

## Configuration and state

```sh
qsee-supplicant --state-dir /var/lib/qsee-supplicant
```

The FS and GPFS listener protocols let a TA put a pathname in a
file-operation request. Legacy QSEECOM was mostly used on Android devices, so
TAs written for it often send Android pathnames such as
`/data/vendor/secureapp/object.so`. The daemon treats such a pathname as a
name inside its own storage namespace, not as a path to a file elsewhere on
the Linux host.

`--state-dir` selects the root of that namespace. With
`--state-dir /var/lib/qsee-supplicant`, a request for
`/data/vendor/secureapp/object.so` accesses
`/var/lib/qsee-supplicant/data/vendor/secureapp/object.so`.

The daemon rejects any pathname that it cannot confine to this directory:

- a parent-directory component such as `..`,
- a symbolic link in any component of the path,
- control characters,
- a pathname field without a terminating null byte. The protocol pathname
  fields have a fixed size, so such a request is malformed.

These checks stop a trusted application from using the listener to reach
files outside the configured state directory.

Directories are created with mode 0700 and objects with mode 0600.
Do not pre-create empty objects unless the requesting TA's protocol requires
them. An empty object and a missing object have different filesystem
semantics. Treat any backup of the state directory as sensitive, device-bound
data.

The supplied systemd and OpenRC units run as root. The Linux QSEECOM TEE
driver requires the Linux `CAP_SYS_ADMIN` administrative capability and
normally creates `/dev/teeprivN` with mode 0600, owned by root, so assigning
the capability to a dedicated account is not enough on its own. The systemd
unit bounds capabilities to `CAP_SYS_ADMIN` and confines filesystem, network,
and kernel access. A downstream may use a dedicated account only when its udev
rules and Linux Security Module (LSM) policy also grant that account access to
the TEE device node and the state directory.

## Lifecycle

Start the daemon before any service that may load a dependent TA.

- Listener registration is logged as `event=listener_registered`.
- If the TEE device is lost, the daemon closes every registration and every
  outstanding listener-side file handle, then retries discovery with bounded
  exponential backoff.
- The termination and interrupt signals (`SIGTERM` and `SIGINT`) close
  `/dev/teeprivN`. This unregisters the listeners, and the daemon exits
  cleanly.
- Service managers should restart a failed daemon.

Only one legacy-QSEECOM supplicant may serve a TEE device.

For each dynamic TA that must remain loaded, enable the loader instance named
after its firmware name. For example:

```sh
systemctl enable --now qsee-supplicant.service
systemctl enable --now qsee-app-loader@example-ta.service
```

## TODO

Application acquisition and readiness reporting are functional. Lifecycle
work is tracked here:

- [x] Attach to an application that is already resident before attempting a
  privileged load. If another process wins the load race, retry the
  unprivileged attachment. This allows a loader to restart while another
  client session keeps the application resident.
- [ ] Provide an operating-system interface for observing when named QSEECOM
  applications become available or disappear. Consumers that support dynamic
  availability should not have to infer application state from loader process
  state.

## License

The original qsee-supplicant code is licensed under the BSD 2-Clause License
in [`LICENSE`](LICENSE).

[`include/qsee_protocol.h`](include/qsee_protocol.h) contains message-layout
definitions derived from Qualcomm MinkIPC. It keeps the BSD 3-Clause Clear
License. Its license text is in
[`LICENSES/BSD-3-Clause-Clear.txt`](LICENSES/BSD-3-Clause-Clear.txt).

The handle database is adapted from OP-TEE `tee-supplicant`. Its Linaro
copyright notice and BSD 2-Clause license are retained in the corresponding
source files.
