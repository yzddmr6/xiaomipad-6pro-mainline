// SPDX-License-Identifier: BSD-2-Clause
#include "notify.h"

#include <errno.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

int qs_notify(const char *state)
{
	const char *path = getenv("NOTIFY_SOCKET");
	struct sockaddr_un address = { .sun_family = AF_UNIX };
	socklen_t address_length;
	int fd;
	int rc;

	if (!path || !path[0])
		return 0;
	if (strlen(path) >= sizeof(address.sun_path)) {
		errno = ENAMETOOLONG;
		return -1;
	}
	strcpy(address.sun_path, path);
	if (address.sun_path[0] == '@') {
		address.sun_path[0] = '\0';
		address_length = offsetof(struct sockaddr_un, sun_path) + strlen(path);
	} else {
		address_length = offsetof(struct sockaddr_un, sun_path) + strlen(path) + 1;
	}
	fd = socket(AF_UNIX, SOCK_DGRAM | SOCK_CLOEXEC, 0);
	if (fd < 0)
		return -1;
	rc = sendto(fd, state, strlen(state), MSG_NOSIGNAL,
		    (struct sockaddr *)&address, address_length);
	close(fd);
	return rc < 0 ? -1 : 0;
}
