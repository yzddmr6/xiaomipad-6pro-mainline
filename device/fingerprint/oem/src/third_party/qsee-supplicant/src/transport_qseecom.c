// SPDX-License-Identifier: BSD-2-Clause
#include "qsee_supplicant.h"
#include "qsee_protocol.h"
#include "qs_rpmb.h"

#include <errno.h>
#include <fcntl.h>
#include <linux/tee.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>

#define TEE_IMPL_ID_QSEECOM 5u

struct shared_memory { int id; void *address; size_t size; };
struct registered_service { const struct qs_service *service; struct shared_memory memory; };

static int alloc_shm(int fd, size_t size, struct shared_memory *memory)
{
	struct tee_ioctl_shm_alloc_data data = { .size = size };
	int shmfd = ioctl(fd, TEE_IOC_SHM_ALLOC, &data);
	if (shmfd < 0) return -1;
	memory->address = mmap(NULL, data.size, PROT_READ | PROT_WRITE, MAP_SHARED, shmfd, 0);
	close(shmfd);
	if (memory->address == MAP_FAILED) return -1;
	memory->id = data.id; memory->size = data.size; memset(memory->address, 0, data.size);
	return 0;
}

static int open_qseecom_priv(void)
{
	char path[32]; struct tee_ioctl_version_data version; int i, fd;
	for (i = 0; i < 8; i++) {
		snprintf(path, sizeof(path), "/dev/teepriv%d", i);
		fd = open(path, O_RDWR | O_CLOEXEC);
		if (fd < 0) continue;
		if (!ioctl(fd, TEE_IOC_VERSION, &version) && version.impl_id == TEE_IMPL_ID_QSEECOM)
			return fd;
		close(fd);
	}
	errno = ENODEV; return -1;
}

static int register_service(int fd, struct registered_service *registered)
{
	uint64_t request_storage[(sizeof(struct tee_ioctl_open_session_arg) +
				 2 * sizeof(struct tee_ioctl_param) + 7) / 8] = {};
	struct tee_ioctl_open_session_arg *request = (void *)request_storage;
	struct tee_ioctl_param *params = request->params;
	struct tee_ioctl_buf_data data;
	if (alloc_shm(fd, registered->service->buffer_size, &registered->memory)) return -1;
	request->num_params = 2;
	params[0].attr = TEE_IOCTL_PARAM_ATTR_TYPE_VALUE_INPUT;
	params[0].a = registered->service->id;
	params[1].attr = TEE_IOCTL_PARAM_ATTR_TYPE_MEMREF_INOUT;
	params[1].b = registered->memory.size;
	params[1].c = registered->memory.id;
	data.buf_ptr = (uintptr_t)request_storage; data.buf_len = sizeof(request_storage);
	return ioctl(fd, TEE_IOC_OPEN_SESSION, &data);
}

static int serve_qseecom(struct qs_store *store, const struct qs_service *services,
			 size_t count, volatile sig_atomic_t *stop,
			 void (*ready)(void *data), void *ready_data)
{
	struct registered_service registered[16] = {}; size_t i;
	int fd, rc = -1, saved_errno;
	if (count > 16) { errno = E2BIG; return -1; }
	fd = open_qseecom_priv(); if (fd < 0) return -1;
	for (i = 0; i < count; i++) {
		registered[i].service = &services[i];
		if (register_service(fd, &registered[i])) goto out;
		fprintf(stderr, "event=listener_registered transport=qseecom id=%u size=%zu\n",
			services[i].id, services[i].buffer_size);
	}
	if (ready)
		ready(ready_data);
	while (!*stop) {
		uint64_t recv_storage[(sizeof(struct tee_iocl_supp_recv_arg) +
				  2 * sizeof(struct tee_ioctl_param) + 7) / 8] = {};
		uint64_t send_storage[(sizeof(struct tee_iocl_supp_send_arg) +
				  sizeof(struct tee_ioctl_param) + 7) / 8] = {};
		struct tee_iocl_supp_recv_arg *recv = (void *)recv_storage;
		struct tee_ioctl_param *recv_params = recv->params;
		struct tee_iocl_supp_send_arg *send = (void *)send_storage;
		struct tee_ioctl_param *send_params = send->params;
		struct tee_ioctl_buf_data data; struct registered_service *selected = NULL;
		recv->num_params = 2; data.buf_ptr = (uintptr_t)recv_storage; data.buf_len = sizeof(recv_storage);
		if (ioctl(fd, TEE_IOC_SUPPL_RECV, &data)) { if (errno == EINTR && *stop) { rc = 0; break; } goto out; }
		for (i = 0; i < count; i++) if (services[i].id == recv->func) selected = &registered[i];
		send->ret = 1; send->num_params = 1;
		if (selected) {
			uint32_t command;
			int32_t reply_status = 0;
			void *buffer = selected->memory.address;
			memcpy(&command, buffer, sizeof(command));
			int dispatched = selected->service->dispatch(store, buffer, selected->memory.size);
			if (!dispatched) {
				send->ret = 0;
				/* Only numeric protocol status is logged. Names, file bytes,
				 * credential handles and authentication tokens stay in memory.
				 */
				if (selected->service->id == QS_FS_SERVICE_ID) {
					size_t at = command == QS_FS_READ ?
						offsetof(struct qs_fs_read_response, result) :
						offsetof(struct qs_fs_response, result);
					memcpy(&reply_status, (char *)buffer + at, sizeof(reply_status));
				} else if (selected->service->id == QS_GPFS_SERVICE_ID) {
					size_t at = command == QS_GPFS_VERSION ?
						offsetof(struct qs_gpfs_version_response, error) :
						offsetof(struct qs_gpfs_error_response, error);
					memcpy(&reply_status, (char *)buffer + at, sizeof(reply_status));
				} else if (selected->service->id == QS_RPMB_SERVICE_ID) {
					/* Legacy INIT puts status after version; READ/WRITE
					 * put it immediately after the command word.
					 */
					size_t at = command == QS_RPMB_INIT ? 8 : 4;
					memcpy(&reply_status, (char *)buffer + at, sizeof(reply_status));
				}
			}
			fprintf(stderr, "event=listener_request id=%u command=0x%x dispatch_status=%d reply_status=%d\n",
				selected->service->id, command, dispatched, reply_status);
		}
		send_params[0].attr = TEE_IOCTL_PARAM_ATTR_TYPE_VALUE_OUTPUT;
		send_params[0].a = recv_params[0].a;
		data.buf_ptr = (uintptr_t)send_storage; data.buf_len = sizeof(send_storage);
		if (ioctl(fd, TEE_IOC_SUPPL_SEND, &data)) goto out;
	}
out:
	saved_errno = errno;
	for (i = 0; i < count; i++) if (services[i].reset)
		services[i].reset();
	for (i = 0; i < count; i++) if (registered[i].memory.address)
		munmap(registered[i].memory.address, registered[i].memory.size);
	close(fd);
	if (*stop)
		return 0;
	errno = saved_errno;
	return rc;
}

const struct qs_transport qs_qseecom_transport = { .name = "qseecom", .serve = serve_qseecom };
