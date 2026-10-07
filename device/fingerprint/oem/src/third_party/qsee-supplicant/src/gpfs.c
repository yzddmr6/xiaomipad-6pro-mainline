// SPDX-License-Identifier: BSD-2-Clause
#include "qsee_supplicant.h"
#include "qsee_protocol.h"

#include <errno.h>
#include <fcntl.h>
#include <linux/openat2.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>

static int open_beneath(int dirfd, const char *path, int flags, mode_t mode)
{
	struct open_how how = {
		.flags = (uint64_t)flags | O_CLOEXEC,
		.mode = mode,
		.resolve = RESOLVE_BENEATH | RESOLVE_NO_MAGICLINKS |
			   RESOLVE_NO_SYMLINKS,
	};
	return syscall(SYS_openat2, dirfd, path, &how, sizeof(how));
}

static int copy_file_at(int dirfd, const char *source, const char *dest,
			mode_t mode)
{
	char tmp[320];
	uint8_t buffer[16384];
	ssize_t got, done;
	int in = -1, out = -1, rc = -1;

	in = openat(dirfd, source, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
	if (in < 0)
		return errno == ENOENT ? 0 : -1;
	if (snprintf(tmp, sizeof(tmp), ".%s.bak.tmp.%ld", source,
		     (long)getpid()) >= (int)sizeof(tmp)) {
		errno = ENAMETOOLONG;
		goto out;
	}
	out = openat(dirfd, tmp, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC,
		     mode);
	if (out < 0)
		goto out;
	while ((got = read(in, buffer, sizeof(buffer))) > 0) {
		for (done = 0; done < got;) {
			ssize_t n = write(out, buffer + done, (size_t)(got - done));
			if (n < 0)
				goto out;
			done += n;
		}
	}
	if (got < 0 || fsync(out))
		goto out;
	if (renameat(dirfd, tmp, dirfd, dest))
		goto out;
	if (fsync(dirfd))
		goto out;
	rc = 0;
out:
	if (rc && out >= 0)
		unlinkat(dirfd, tmp, 0);
	if (out >= 0)
		close(out);
	if (in >= 0)
		close(in);
	return rc;
}

static int atomic_write(struct qs_store *store, const char *path,
			const void *data, size_t length, bool backup)
{
	char leaf[256], tmp[320] = "", bak[320];
	int parent = -1, fd = -1, rc = -1;
	size_t done = 0;

	if (qs_open_parent(store, path, true, &parent, leaf, sizeof(leaf)))
		return -1;
	if (backup) {
		if (snprintf(bak, sizeof(bak), "%s.bak", leaf) >= (int)sizeof(bak)) {
			errno = ENAMETOOLONG;
			goto out;
		}
		if (copy_file_at(parent, leaf, bak, store->file_mode))
			goto out;
	}
	if (snprintf(tmp, sizeof(tmp), ".%s.tmp.%ld", leaf,
		     (long)getpid()) >= (int)sizeof(tmp)) {
		errno = ENAMETOOLONG;
		goto out;
	}
	unlinkat(parent, tmp, 0);
	fd = openat(parent, tmp, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC,
		    store->file_mode);
	if (fd < 0)
		goto out;
	while (done < length) {
		ssize_t n = write(fd, (const uint8_t *)data + done, length - done);
		if (n < 0)
			goto out;
		done += (size_t)n;
	}
	if (fsync(fd) || renameat(parent, tmp, parent, leaf) || fsync(parent))
		goto out;
	rc = 0;
out:
	if (rc && parent >= 0 && tmp[0])
		unlinkat(parent, tmp, 0);
	if (fd >= 0)
		close(fd);
	if (parent >= 0)
		close(parent);
	return rc;
}

static void gp_reply(void *buffer, uint32_t command, int error, uint32_t count)
{
	struct qs_gpfs_io_response *response = buffer;

	response->command = command;
	response->error = error;
	response->count = count;
}

int qs_gpfs_dispatch(struct qs_store *store, void *buffer, size_t size)
{
	struct qs_gpfs_path_request *path_request = buffer;
	struct qs_gpfs_read_request *read_request = buffer;
	struct qs_gpfs_write_request *write_request = buffer;
	struct qs_gpfs_rename_request *rename_request = buffer;
	struct qs_gpfs_io_response *io_response = buffer;
	struct qs_gpfs_version_response *version_response = buffer;
	char path[512], path2[512];
	uint32_t command, length;
	int32_t offset;
	int fd = -1, parent = -1, rc = 0, saved = 0;
	ssize_t n;
	char leaf[256], leaf2[256];

	if (!store || !buffer ||
	    size < offsetof(struct qs_gpfs_write_request, data))
		return -EINVAL;
	command = path_request->command;
	if (command > QS_GPFS_VERSION) {
		gp_reply(buffer, command, EINVAL, 0);
		return 0;
	}
	if (command == QS_GPFS_VERSION) {
		version_response->command = command;
		version_response->version = 2;
		version_response->error = 0;
		return 0;
	}
	if (command % 4 == 3 && size < sizeof(*rename_request)) {
		gp_reply(buffer, command, EINVAL, 0);
		return 0;
	}
	if (qs_normalize_path(path_request->pathname, sizeof(path_request->pathname),
			      path, sizeof(path))) {
		gp_reply(buffer, command, errno, 0);
		return 0;
	}
	offset = 0;
	length = 0;
	if (command % 4 == 0 || command % 4 == 1) {
		offset = read_request->offset;
		length = read_request->count;
		if (offset < 0 || length > QS_GPFS_DATA_SIZE ||
		    (command % 4 == 0 &&
		     (size_t)length > size - offsetof(struct qs_gpfs_io_response, data)) ||
		    (command % 4 == 1 &&
		     (size_t)length > size - offsetof(struct qs_gpfs_write_request, data))) {
			gp_reply(buffer, command, EINVAL, 0);
			return 0;
		}
	}
	switch (command % 4) {
	case 0:
		fd = open_beneath(store->root_fd, path, O_RDONLY, 0);
		if (fd < 0) {
			saved = errno;
			break;
		}
		n = pread(fd, io_response->data, length, offset);
		if (n < 0)
			saved = errno;
		else
			length = (uint32_t)n;
		break;
	case 1:
		if (offset == 0) {
			rc = atomic_write(store, path, write_request->data, length,
					  write_request->backup != 0);
		} else {
			fd = open_beneath(store->root_fd, path, O_WRONLY, 0);
			if (fd >= 0) {
				n = pwrite(fd, write_request->data, length, offset);
				rc = n == (ssize_t)length && !fsync(fd) ? 0 : -1;
			} else {
				rc = -1;
			}
		}
		if (rc)
			saved = errno ? errno : EIO;
		break;
	case 2:
		if (qs_open_parent(store, path, false, &parent, leaf, sizeof(leaf))) {
			saved = errno;
			break;
		}
		if (unlinkat(parent, leaf, 0) && unlinkat(parent, leaf, AT_REMOVEDIR))
			saved = errno;
		else if (fsync(parent))
			saved = errno;
		break;
	case 3:
		if (qs_normalize_path(rename_request->to, sizeof(rename_request->to),
				      path2, sizeof(path2))) {
			saved = errno;
			break;
		}
		if (qs_open_parent(store, path, false, &parent, leaf, sizeof(leaf))) {
			saved = errno;
			break;
		}
		if (qs_open_parent(store, path2, true, &fd, leaf2, sizeof(leaf2))) {
			saved = errno;
			break;
		}
		if (renameat(parent, leaf, fd, leaf2))
			saved = errno;
		else if (fsync(parent) || fsync(fd))
			saved = errno;
		break;
	}
	if (fd >= 0)
		close(fd);
	if (parent >= 0)
		close(parent);
	gp_reply(buffer, command, saved, saved ? 0 : length);
	return 0;
}
