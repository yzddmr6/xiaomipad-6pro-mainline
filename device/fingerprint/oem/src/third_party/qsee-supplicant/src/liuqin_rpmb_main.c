// SPDX-License-Identifier: BSD-2-Clause
#include "qs_rpmb.h"
#include "qsee_supplicant.h"
#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <unistd.h>

static struct qs_rpmb_ufs device = {.fd = -1};
static volatile sig_atomic_t stopping;
static void stop_handler(int signo) { (void)signo; stopping = 1; }
static void ready(void *data)
{
	(void)data;
	fprintf(stderr, "event=liuqin_rpmb_listeners_ready\n");
}
static int dispatch(struct qs_store *store, void *buffer, size_t size)
{
	(void)store;
	if (size < QS_RPMB_BUFFER_SIZE) return -1;
	return qs_rpmb_dispatch(&device, buffer, QS_RPMB_BUFFER_SIZE);
}

int main(int argc, char **argv)
{
	const struct qs_service services[] = {
		{QS_FS_SERVICE_ID, QS_FS_BUFFER_SIZE, qs_fs_dispatch, qs_fs_reset},
		{QS_GPFS_SERVICE_ID, QS_GPFS_BUFFER_SIZE, qs_gpfs_dispatch, NULL},
		{QS_RPMB_SERVICE_ID, QS_RPMB_BUFFER_SIZE, dispatch, NULL},
	};
	struct qs_store store = {.root_fd = -1};
	bool metadata = argc == 2 && !strcmp(argv[1], "--metadata");
	bool read_check = argc == 2 && !strcmp(argv[1], "--check-read-transport");
	bool readonly = argc == 4 && !strcmp(argv[1], "--serve-read-only");
	bool authenticated = argc == 4 && !strcmp(argv[1], "--serve-authenticated");
	if (geteuid() || ((!metadata && !read_check) && ((!readonly && !authenticated) ||
	    strcmp(argv[2], "--state-dir")))) {
		fputs("Usage (root): liuqin-rpmb-supplicant --metadata | "
		      "--check-read-transport | "
		      "--serve-read-only --state-dir PATH | --serve-authenticated --state-dir PATH\n", stderr);
		return 2;
	}
	if (prctl(PR_SET_DUMPABLE, 0)) return 1;
	umask(0077);
	device.allow_data_writes = authenticated;
	if (qs_rpmb_ufs_open(&device)) {
		fprintf(stderr, "event=rpmb_metadata_error errno=%d\n", errno); return 1;
	}
	printf("event=rpmb_metadata sectors_512=%u reliable_frames=%u device_type=9 "
	       "mode=%s\n", device.sectors_512, device.reliable_frames,
	       metadata ? "metadata" : read_check ? "read-transport-check" :
	       authenticated ? "authenticated" : "read-only");
	fflush(stdout);
	if (metadata) { qs_rpmb_ufs_close(&device); return 0; }
	if (read_check) {
		int rc = qs_rpmb_ufs_check_read_transport(&device);
		qs_rpmb_ufs_close(&device); return rc;
	}
	if (qs_rpmb_ufs_prepare(&device)) {
		qs_rpmb_ufs_close(&device); return 1;
	}
	struct sigaction action = {.sa_handler = stop_handler};
	sigemptyset(&action.sa_mask);
	if (sigaction(SIGINT, &action, NULL) || sigaction(SIGTERM, &action, NULL) ||
	    qs_store_open(&store, argv[3], 0600, 0700)) {
		qs_rpmb_ufs_close(&device); return 1;
	}
	/* One registration lifetime. No automatic reconnect, provision or retries. */
	int rc = qs_qseecom_transport.serve(&store, services,
		sizeof services / sizeof services[0], &stopping, ready, NULL);
	int error = rc ? errno : 0;
	qs_store_close(&store);
	qs_rpmb_ufs_close(&device);
	fprintf(stderr, "event=rpmb_shutdown status=%d errno=%d stopping=%d\n", rc, error, stopping);
	return rc ? 1 : 0;
}
