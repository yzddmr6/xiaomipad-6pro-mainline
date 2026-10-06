// SPDX-License-Identifier: BSD-2-Clause
#include <errno.h>
#include <fcntl.h>
#include <linux/tee.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>

#include "app_acquire.h"
#include "notify.h"

#define TEE_IMPL_ID_QSEECOM 5u
#define APP_NAME_MAX 64u

static volatile sig_atomic_t stopping;

struct loader_context {
	const char *application;
	int client_fd;
	int loader_fd;
};

static void stop_handler(int signal_number)
{
	(void)signal_number;
	stopping = 1;
}

static int open_qseecom_device(bool privileged)
{
	struct tee_ioctl_version_data version;
	char path[32];
	int fd;

	for (int i = 0; i < 8; i++) {
		snprintf(path, sizeof(path), privileged ? "/dev/teepriv%d" :
			 "/dev/tee%d", i);
		fd = open(path, O_RDWR | O_CLOEXEC);
		if (fd < 0)
			continue;
		if (!ioctl(fd, TEE_IOC_VERSION, &version) &&
		    version.impl_id == TEE_IMPL_ID_QSEECOM)
			return fd;
		close(fd);
	}
	errno = ENODEV;
	return -1;
}

static int open_named_session(int fd, const char *application,
			      uint32_t *session_id)
{
	struct tee_ioctl_shm_alloc_data allocation = { .size = APP_NAME_MAX };
	uint64_t session_storage[(sizeof(struct tee_ioctl_open_session_arg) +
				  sizeof(struct tee_ioctl_param) + 7) / 8] = {};
	struct tee_ioctl_open_session_arg *session = (void *)session_storage;
	struct tee_ioctl_param *params = session->params;
	struct tee_ioctl_buf_data data;
	void *name_memory;
	int saved_errno;
	int shm_fd;

	shm_fd = ioctl(fd, TEE_IOC_SHM_ALLOC, &allocation);
	if (shm_fd < 0)
		return -errno;
	name_memory = mmap(NULL, allocation.size, PROT_READ | PROT_WRITE,
			   MAP_SHARED, shm_fd, 0);
	close(shm_fd);
	if (name_memory == MAP_FAILED)
		return -errno;

	memset(name_memory, 0, allocation.size);
	memcpy(name_memory, application, strlen(application));
	session->num_params = 1;
	params[0].attr = TEE_IOCTL_PARAM_ATTR_TYPE_MEMREF_INPUT;
	params[0].b = strlen(application) + 1;
	params[0].c = allocation.id;
	data.buf_ptr = (uintptr_t)session_storage;
	data.buf_len = sizeof(session_storage);
	if (ioctl(fd, TEE_IOC_OPEN_SESSION, &data)) {
		saved_errno = errno;
		munmap(name_memory, allocation.size);
		return -saved_errno;
	}

	munmap(name_memory, allocation.size);
	*session_id = session->session;
	return 0;
}

static int attach_application(void *opaque, uint32_t *session)
{
	struct loader_context *context = opaque;

	if (context->client_fd < 0) {
		context->client_fd = open_qseecom_device(false);
		if (context->client_fd < 0)
			return -errno;
	}
	return open_named_session(context->client_fd, context->application,
				  session);
}

static int load_application(void *opaque, uint32_t *session)
{
	struct loader_context *context = opaque;

	if (context->loader_fd < 0) {
		context->loader_fd = open_qseecom_device(true);
		if (context->loader_fd < 0)
			return -errno;
	}
	return open_named_session(context->loader_fd, context->application,
				  session);
}

int main(int argc, char **argv)
{
	const struct qs_app_acquire_ops ops = {
		.attach = attach_application,
		.load = load_application,
	};
	struct loader_context context = {
		.client_fd = -1,
		.loader_fd = -1,
	};
	struct sigaction action = { .sa_handler = stop_handler };
	enum qs_app_acquisition acquisition;
	uint32_t session;
	int active_fd;
	int result;

	if (argc != 2 || !argv[1][0] || strlen(argv[1]) >= APP_NAME_MAX ||
	    strchr(argv[1], '/')) {
		fprintf(stderr, "usage: %s APPLICATION\n", argv[0]);
		return 2;
	}

	context.application = argv[1];
	result = qs_app_acquire(&ops, &context, &acquisition, &session);
	if (result) {
		result = -result;
		fprintf(stderr, "event=loader_error app=%s stage=acquire errno=%d message=\"%s\"\n",
			argv[1], result, strerror(result));
		if (context.client_fd >= 0)
			close(context.client_fd);
		if (context.loader_fd >= 0)
			close(context.loader_fd);
		return 1;
	}

	if (acquisition == QS_APP_LOADED) {
		active_fd = context.loader_fd;
		if (context.client_fd >= 0)
			close(context.client_fd);
		fprintf(stderr, "event=application_loaded app=%s session=%u\n",
			argv[1], session);
	} else {
		active_fd = context.client_fd;
		if (context.loader_fd >= 0)
			close(context.loader_fd);
		fprintf(stderr, "event=application_attached app=%s session=%u\n",
			argv[1], session);
	}
	if (qs_notify("READY=1\nSTATUS=Trusted application available") < 0)
		fprintf(stderr, "event=notify_error app=%s errno=%d message=\"%s\"\n",
			argv[1], errno, strerror(errno));
	sigemptyset(&action.sa_mask);
	sigaction(SIGINT, &action, NULL);
	sigaction(SIGTERM, &action, NULL);
	while (!stopping)
		pause();
	fprintf(stderr, "event=application_releasing app=%s session=%u\n",
		argv[1], session);
	close(active_fd);
	return 0;
}
