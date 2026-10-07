// SPDX-License-Identifier: BSD-2-Clause
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define QS_RPMB_SERVICE_ID 0x2000u
#define QS_RPMB_INIT 0x101u
#define QS_RPMB_BUFFER_SIZE (25u * 1024u)
#define QS_RPMB_FRAME_SIZE 512u

struct qs_rpmb_ufs {
	int fd;
	uint32_t sectors_512;
	uint32_t reliable_frames;
	bool allow_data_writes;
	bool write_failed;
};

/* Only descriptor metadata is read here. No key/counter/data requests. */
int qs_rpmb_ufs_open(struct qs_rpmb_ufs *device);
void qs_rpmb_ufs_close(struct qs_rpmb_ufs *device);

/* Normal listener startup: REQUEST SENSE, without RPMB data/counter commands. */
int qs_rpmb_ufs_prepare(struct qs_rpmb_ufs *device);

/* Explicit read-only transport check; no counter/MAC/nonce bytes are exposed. */
int qs_rpmb_ufs_check_read_transport(struct qs_rpmb_ufs *device);

/* Request frames stay opaque except for type, count and bounds checking.
 * The TA and storage device authenticate them; no key programming or retries.
 */
int qs_rpmb_dispatch(struct qs_rpmb_ufs *device, void *buffer, size_t size);
int qs_rpmb_ufs_transfer(struct qs_rpmb_ufs *device, bool write,
	const void *request, size_t request_frames,
	void *response, size_t response_frames);
