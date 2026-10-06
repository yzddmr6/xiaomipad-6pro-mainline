// SPDX-License-Identifier: MIT
/* Adapted from samcday/pocketfed's MIT-licensed rpmb-protocol.c at
 * 8a3645b39921be527923970305963da52b40f058 (Sargo listener).
 */
/* Legacy Qualcomm 0x2000 wire layout: LK rpmb_listener.c (version 2).
 * Read-length conventions also follow samcday/pocketfed's measured Sargo
 * listener. This does not imply that liuqin's resident TA has been exercised.
 */
#include "qs_rpmb.h"
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static uint32_t le32(const unsigned char *p)
{ return p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24; }
static uint16_t be16(const unsigned char *p)
{ return (uint16_t)p[0] << 8 | p[1]; }
static void put32(unsigned char *p, uint32_t value)
{ for (unsigned int i = 0; i < 4; ++i) p[i] = value >> (8 * i); }

int qs_rpmb_dispatch(struct qs_rpmb_ufs *d, void *buffer, size_t size)
{
	unsigned char *b = buffer, *request = NULL;
	uint32_t command, count, length, offset, version, reliable, response_length = 0;
	int32_t status = -1;
	int transfer_status = 0;
	unsigned int frame_type = 0, frame_blocks = 0;
	size_t request_length = 0;
	const char *stage = "envelope";
	if (!b || size < 40 || size > QS_RPMB_BUFFER_SIZE) return -1;
	command = le32(b);
	if (command == QS_RPMB_INIT) {
		version = le32(b + 4);
		memset(b, 0, size);
		put32(b, command); put32(b + 4, 2); put32(b + 8, UINT32_MAX);
		if (version == 2 && d->fd >= 0) {
			put32(b + 8, 0); put32(b + 12, d->sectors_512);
			put32(b + 16, d->reliable_frames); put32(b + 20, 9);
		}
		fprintf(stderr, "event=rpmb_init version=%u status=%d sectors_512=%u reliable=%u\n",
			version, (int32_t)le32(b + 8), d->sectors_512, d->reliable_frames);
		return 0;
	}
	count = le32(b + 4); length = le32(b + 8); offset = le32(b + 12);
	version = le32(b + 16); reliable = le32(b + 20);
	/* Validate the shared-memory envelope once, including response capacity. */
	if (d->fd < 0 || (command != 0x102 && command != 0x103) || !count ||
	    count > (size - 24) / QS_RPMB_FRAME_SIZE || offset < 24 ||
	    offset > size || length > size - offset) goto reply;
	if (length >= QS_RPMB_FRAME_SIZE && QS_RPMB_FRAME_SIZE <= size - offset) {
		frame_type = be16(b + offset + 510);
		frame_blocks = be16(b + offset + 506);
	}
	if (command == 0x102) {
		stage = "read-frame";
		if (count > d->reliable_frames || QS_RPMB_FRAME_SIZE > size - offset ||
		    (length != QS_RPMB_FRAME_SIZE && length != count * 256u)) goto reply;
		uint16_t type = be16(b + offset + 510);
		if ((type != 2 && type != 4) || (type == 2 && count != 1)) goto reply;
		uint16_t blocks = be16(b + offset + 506);
		if (type == 4 && ((blocks && blocks != count) ||
		    (uint32_t)be16(b + offset + 504) + count > d->sectors_512 * 2)) goto reply;
		request_length = QS_RPMB_FRAME_SIZE;
	} else {
		stage = "write-disabled-or-group";
		if (!d->allow_data_writes || d->write_failed || !reliable ||
		    reliable > d->reliable_frames ||
		    length != count * QS_RPMB_FRAME_SIZE) goto reply;
		for (uint32_t i = 0; i < count; ++i) {
			const unsigned char *frame = b + offset + i * QS_RPMB_FRAME_SIZE;
			uint32_t remaining = count - (i / reliable) * reliable;
			uint32_t group = remaining < reliable ? remaining : reliable;
			stage = "write-frame";
			if (be16(frame + 510) != 3 || be16(frame + 506) != group ||
			    (uint32_t)be16(frame + 504) + group > d->sectors_512 * 2) goto reply;
		}
		request_length = length;
	}
	stage = "allocation";
	request = malloc(request_length);
	if (!request) { transfer_status = -ENOMEM; goto reply; }
	memcpy(request, b + offset, request_length);
	memset(b, 0, size);
	if (command == 0x102) {
		stage = "read-transfer";
		transfer_status = qs_rpmb_ufs_transfer(d, false, request, 1, b + 20, count);
		if (!transfer_status) { status = 0; response_length = count * QS_RPMB_FRAME_SIZE; }
	} else {
		stage = "write-transfer";
		for (uint32_t i = 0; i < count; i += reliable) {
			uint32_t group = count - i < reliable ? count - i : reliable;
			memset(b + 20, 0, QS_RPMB_FRAME_SIZE);
			transfer_status = qs_rpmb_ufs_transfer(d, true,
				request + i * QS_RPMB_FRAME_SIZE, group, b + 20, 1);
			if (transfer_status) { status = -1; response_length = 0; break; }
			status = 0; response_length = QS_RPMB_FRAME_SIZE;
			/* Device failure remains an opaque response for the TA to check. */
			if (be16(b + 20 + 510) != 0x300 || be16(b + 20 + 508)) {
				d->write_failed = true; break;
			}
		}
	}
reply:
	if (request) { explicit_bzero(request, request_length); free(request); }
	if (status) memset(b, 0, size); else memset(b, 0, 20);
	put32(b, command); put32(b + 4, (uint32_t)status);
	put32(b + 8, response_length); put32(b + 12, status ? 0 : 20);
	put32(b + 16, version);
	fprintf(stderr, "event=rpmb_dispatch command=%u count=%u length=%u offset=%u "
		"version=%u reliable=%u stage=%s transport=%d reply=%d writes_disabled=%d "
		"frame_type=0x%x frame_blocks=%u\n",
		command, count, length, offset, version, reliable, stage,
		transfer_status, status, d->write_failed, frame_type, frame_blocks);
	return 0;
}
