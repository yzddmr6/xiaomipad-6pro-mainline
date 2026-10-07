// SPDX-License-Identifier: BSD-2-Clause
/* UFS 3.1 RPMB region 0, SCSI BSG v4. Wire constants follow Qualcomm LK
 * rpmb_ufs.c and SanDisk ufs-utils scsi_bsg_util.c; see source provenance.
 */
#include "qs_rpmb.h"
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/bsg.h>
#include <scsi/sg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/random.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <unistd.h>

#define UFS_ROOT "/sys/bus/platform/devices/1d84000.ufshc"
#define RPMB_ROOT "/sys/class/scsi_device/0:0:0:49476/device"
#define RPMB_BSG "/dev/bsg/0:0:0:49476"

static int attribute(const char *path, uint64_t *value)
{
	char text[64], *end;
	FILE *file = fopen(path, "re");
	if (!file) return -1;
	size_t n = fread(text, 1, sizeof(text) - 1, file);
	int bad = ferror(file) || !feof(file);
	fclose(file);
	if (bad || !n || !isdigit((unsigned char)text[0])) {
		errno = EINVAL; return -1;
	}
	text[n] = 0;
	errno = 0;
	*value = strtoull(text, &end, 0);
	if (errno || end == text) return -1;
	while (isspace((unsigned char)*end)) ++end;
	if (*end) { errno = EINVAL; return -1; }
	return 0;
}

int qs_rpmb_ufs_open(struct qs_rpmb_ufs *d)
{
	uint64_t version, security, enabled, exponent, blocks, reliable, type, regions;
	struct stat node;
	char syspath[4096], expected[4096], devtext[64];
	unsigned int dev_major, dev_minor;
	d->fd = -1;
	if (attribute(UFS_ROOT "/device_descriptor/specification_version", &version) ||
	    attribute(UFS_ROOT "/device_descriptor/support_security_lun", &security) ||
	    attribute(UFS_ROOT "/geometry_descriptor/rpmb_rw_size", &reliable) ||
	    attribute(RPMB_ROOT "/unit_descriptor/lu_enable", &enabled) ||
	    attribute(RPMB_ROOT "/unit_descriptor/logical_block_size", &exponent) ||
	    attribute(RPMB_ROOT "/unit_descriptor/logical_block_count", &blocks) ||
	    attribute(RPMB_ROOT "/unit_descriptor/erase_block_size", &regions) ||
	    attribute(RPMB_ROOT "/type", &type)) return -1;
	/* On UFS >=3.0 descriptor bytes 0x13..0x16 are region sizes. The stable
	 * sysfs field is named erase_block_size after the older unit descriptor.
	 * Qualcomm's current listener uses byte 0x13 for region 0 capacity.
	 */
	uint32_t region0 = (uint32_t)(regions >> 24);
	/* This entry point is for the measured liuqin topology, not autodetection. */
	if (version != 0x0310 || security != 1 || enabled != 1 || type != 30 ||
	    exponent != 8 || !blocks || blocks > 65536 || (blocks & 1) ||
	    !reliable || reliable > 255 || !region0 || region0 > 128 ||
	    region0 * 512u > blocks) { errno = ENODEV; return -1; }
	if (!realpath("/sys/class/bsg/0:0:0:49476", syspath) ||
	    !realpath(UFS_ROOT "/host0/target0:0:0/0:0:0:49476/bsg/0:0:0:49476", expected))
		return -1;
	if (strcmp(syspath, expected)) { errno = ENODEV; return -1; }
	FILE *file = fopen("/sys/class/bsg/0:0:0:49476/dev", "re");
	if (!file) return -1;
	int ok = fgets(devtext, sizeof devtext, file) != NULL;
	fclose(file);
	if (!ok || sscanf(devtext, "%u:%u", &dev_major, &dev_minor) != 2) {
		errno = EINVAL; return -1;
	}
	d->fd = open(RPMB_BSG, O_RDWR | O_CLOEXEC | O_NOFOLLOW);
	if (d->fd < 0) return -1;
	if (fstat(d->fd, &node) || !S_ISCHR(node.st_mode) ||
	    node.st_rdev != makedev(dev_major, dev_minor) || node.st_uid != 0 ||
	    node.st_gid != 0 || (node.st_mode & 0002)) {
		qs_rpmb_ufs_close(d); errno = ENODEV; return -1;
	}
	d->sectors_512 = region0 * 256u;
	d->reliable_frames = (uint32_t)reliable;
	d->write_failed = false;
	return 0;
}

void qs_rpmb_ufs_close(struct qs_rpmb_ufs *d)
{
	if (d->fd >= 0) close(d->fd);
	d->fd = -1;
}

int qs_rpmb_ufs_prepare(struct qs_rpmb_ufs *d)
{
	/* Qualcomm's UFS listener sends REQUEST SENSE before serving RPMB.
	 * It consumes pending device attention without reading protected sectors.
	 * No historical SCSI failure is assigned a cause by this initialization.
	 */
	unsigned char cdb[6] = {0x03, 0, 0, 0, 18, 0};
	unsigned char data[18] = {0}, completion[64] = {0};
	struct sg_io_v4 io = {
		.guard = 'Q', .protocol = BSG_PROTOCOL_SCSI,
		.subprotocol = BSG_SUB_PROTOCOL_SCSI_CMD,
		.request_len = sizeof cdb, .request = (uintptr_t)cdb,
		.max_response_len = sizeof completion, .response = (uintptr_t)completion,
		.din_xfer_len = sizeof data, .din_xferp = (uintptr_t)data,
		.timeout = 20000,
	};
	int rc = ioctl(d->fd, SG_IO, &io);
	int saved = errno;
	int bad = rc || io.device_status || io.transport_status || io.driver_status || (io.info & 1);
	unsigned char *sense = bad ? completion : data;
	unsigned int length = bad ? io.response_len :
		io.din_resid >= 0 && io.din_resid <= 18 ? 18u - io.din_resid : 0;
	unsigned int format = length ? sense[0] & 0x7f : 0, key = 0, asc = 0, ascq = 0;
	if ((format == 0x70 || format == 0x71) && length >= 14) {
		key = sense[2] & 0xf; asc = sense[12]; ascq = sense[13];
	} else if ((format == 0x72 || format == 0x73) && length >= 4) {
		key = sense[1] & 0xf; asc = sense[2]; ascq = sense[3];
	}
	fprintf(stderr, "event=rpmb_prepare_request_sense status=%d errno=%d device=%u "
		"transport=%u driver=%u sense_key=%u asc=0x%02x ascq=0x%02x\n",
		bad ? -1 : 0, rc < 0 ? saved : 0, io.device_status, io.transport_status,
		io.driver_status, key, asc, ascq);
	explicit_bzero(data, sizeof data); explicit_bzero(completion, sizeof completion);
	return rc < 0 ? -saved : bad ? -EIO : 0;
}

static int security_io(struct qs_rpmb_ufs *d, bool send, void *buffer, size_t frames)
{
	unsigned char cdb[12] = {0}, sense[64] = {0};
	uint32_t length = (uint32_t)(frames * QS_RPMB_FRAME_SIZE);
	struct sg_io_v4 io = {
		.guard = 'Q', .protocol = BSG_PROTOCOL_SCSI,
		.subprotocol = BSG_SUB_PROTOCOL_SCSI_CMD,
		.request_len = sizeof cdb, .request = (uintptr_t)cdb,
		.max_response_len = sizeof sense, .response = (uintptr_t)sense,
		.timeout = 20000,
	};
	cdb[0] = send ? 0xb5 : 0xa2;
	cdb[1] = 0xec;
	cdb[3] = 1; /* RPMB region 0; INC_512 remains zero. */
	for (unsigned int i = 0; i < 4; ++i) cdb[6 + i] = length >> (24 - 8 * i);
	if (send) { io.dout_xferp = (uintptr_t)buffer; io.dout_xfer_len = length; }
	else { io.din_xferp = (uintptr_t)buffer; io.din_xfer_len = length; }
	int rc = ioctl(d->fd, SG_IO, &io);
	int saved = errno;
	if (rc || io.device_status || io.transport_status || io.driver_status ||
	    (io.info & 1) || (send ? io.dout_resid : io.din_resid)) {
		unsigned int format = io.response_len ? sense[0] & 0x7f : 0;
		unsigned int key = 0, asc = 0, ascq = 0;
		if ((format == 0x70 || format == 0x71) && io.response_len >= 14) {
			key = sense[2] & 0xf; asc = sense[12]; ascq = sense[13];
		} else if ((format == 0x72 || format == 0x73) && io.response_len >= 4) {
			key = sense[1] & 0xf; asc = sense[2]; ascq = sense[3];
		}
		fprintf(stderr, "event=rpmb_scsi_error direction=%s syscall=%d errno=%d "
			"device=%u transport=%u driver=%u residual=%d "
			"sense_format=0x%x sense_key=%u asc=0x%02x ascq=0x%02x "
			"sense_length=%u opcode=0x%02x bytes=%u\n",
			send ? "out" : "in", rc, rc < 0 ? saved : 0,
			io.device_status, io.transport_status, io.driver_status,
			send ? io.dout_resid : io.din_resid, format, key, asc, ascq,
			io.response_len, cdb[0], length);
		explicit_bzero(sense, sizeof sense);
		return rc < 0 ? -saved : -EIO;
	}
	explicit_bzero(sense, sizeof sense);
	return 0;
}

int qs_rpmb_ufs_transfer(struct qs_rpmb_ufs *d, bool write,
	const void *request, size_t request_frames, void *response, size_t response_frames)
{
	/* The codec validates frames before this transport boundary. */
	int rc = security_io(d, true, (void *)request, request_frames);
	if (!rc && write) {
		unsigned char result_request[QS_RPMB_FRAME_SIZE] = {0};
		result_request[511] = 5;
		rc = security_io(d, true, result_request, 1);
		explicit_bzero(result_request, sizeof result_request);
	}
	if (!rc) rc = security_io(d, false, response, response_frames);
	/* An uncertain write is never replayed, including EINTR or UNIT ATTENTION. */
	if (write && rc) d->write_failed = true;
	return rc;
}

int qs_rpmb_ufs_check_read_transport(struct qs_rpmb_ufs *d)
{
	/* A counter-read frame reaches the same SECURITY PROTOCOL OUT/IN path.
	 * No data-sector address, key programming or authenticated write is sent.
	 * The response counter, MAC and nonce are never printed or persisted.
	 */
	unsigned char request[QS_RPMB_FRAME_SIZE] = {0};
	unsigned char response[QS_RPMB_FRAME_SIZE] = {0};
	if (getrandom(request + 484, 16, 0) != 16) return 1;
	request[511] = 2;
	int rc = qs_rpmb_ufs_transfer(d, false, request, 1, response, 1);
	unsigned int type = rc ? 0 : (unsigned int)response[510] << 8 | response[511];
	unsigned int result = rc ? 0 : (unsigned int)response[508] << 8 | response[509];
	int nonce_matches = !rc && !memcmp(request + 484, response + 484, 16);
	fprintf(stderr, "event=rpmb_read_transport_check transport=%d response_type=0x%x "
		"device_result=%u nonce_matches=%d\n", rc, type, result, nonce_matches);
	explicit_bzero(request, sizeof request);
	explicit_bzero(response, sizeof response);
	return rc || type != 0x200 || result || !nonce_matches ? 1 : 0;
}
