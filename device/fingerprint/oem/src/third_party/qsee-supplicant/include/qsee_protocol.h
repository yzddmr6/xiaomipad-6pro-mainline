// Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
// SPDX-License-Identifier: BSD-3-Clause-Clear
//
// Implemented message-layout subset mirrored from MinkIPC fs_msg.h and
// gpfs_msg.h. Add future FS and GPFS operations here from those definitions;
// service implementations should use these structures instead of raw offsets.
#pragma once

#include <stddef.h>
#include <stdint.h>

#define QS_PROTOCOL_NAME_SIZE 256u
#define QS_FS_DATA_SIZE 20000u
#define QS_GPFS_DATA_SIZE (500u * 1024u)

enum qs_fs_command {
	QS_FS_OPEN = 0x202,
	QS_FS_CREAT = 0x206,
	QS_FS_READ = 0x207,
	QS_FS_WRITE = 0x208,
	QS_FS_CLOSE = 0x209,
	QS_FS_LSEEK = 0x20a,
	QS_FS_UNLINK = 0x20c,
	QS_FS_RMDIR = 0x20d,
	QS_FS_MKDIR = 0x210,
	QS_FS_REMOVE = 0x213,
	QS_FS_SYNC = 0x216,
	QS_FS_RENAME = 0x217,
	QS_FS_GET_ERRNO = 0x21c,
	QS_FS_END = 0x21d,
};

struct qs_fs_path_request {
	uint32_t command;
	char pathname[QS_PROTOCOL_NAME_SIZE];
} __attribute__((packed));

struct qs_fs_open_request {
	uint32_t command;
	char pathname[QS_PROTOCOL_NAME_SIZE];
	int32_t flags;
} __attribute__((packed));

struct qs_fs_create_request {
	uint32_t command;
	char pathname[QS_PROTOCOL_NAME_SIZE];
	uint32_t mode;
} __attribute__((packed));

struct qs_fs_fd_request {
	uint32_t command;
	int32_t fd;
} __attribute__((packed));

struct qs_fs_read_request {
	uint32_t command;
	int32_t fd;
	uint32_t count;
} __attribute__((packed));

struct qs_fs_read_response {
	uint32_t command;
	uint8_t data[QS_FS_DATA_SIZE];
	int32_t result;
} __attribute__((packed));

struct qs_fs_write_request {
	uint32_t command;
	int32_t fd;
	uint8_t data[QS_FS_DATA_SIZE];
	uint32_t count;
} __attribute__((packed));

struct qs_fs_lseek_request {
	uint32_t command;
	int32_t fd;
	int32_t offset;
	int32_t whence;
} __attribute__((packed));

struct qs_fs_mkdir_request {
	uint32_t command;
	char pathname[QS_PROTOCOL_NAME_SIZE];
	uint32_t mode;
} __attribute__((packed));

struct qs_fs_rename_request {
	uint32_t command;
	char old_filename[QS_PROTOCOL_NAME_SIZE];
	char new_filename[QS_PROTOCOL_NAME_SIZE];
} __attribute__((packed));

struct qs_fs_response {
	uint32_t command;
	int32_t result;
} __attribute__((packed));

enum qs_gpfs_command {
	QS_GPFS_DATA_READ = 4,
	QS_GPFS_DATA_WRITE = 5,
	QS_GPFS_DATA_REMOVE = 6,
	QS_GPFS_DATA_RENAME = 7,
	QS_GPFS_PERSIST_READ = 8,
	QS_GPFS_PERSIST_WRITE = 9,
	QS_GPFS_PERSIST_REMOVE = 10,
	QS_GPFS_PERSIST_RENAME = 11,
	QS_GPFS_VERSION = 12,
};

struct qs_gpfs_path_request {
	uint32_t command;
	char pathname[QS_PROTOCOL_NAME_SIZE];
} __attribute__((packed));

struct qs_gpfs_read_request {
	uint32_t command;
	char pathname[QS_PROTOCOL_NAME_SIZE];
	int32_t offset;
	uint32_t count;
} __attribute__((packed));

struct qs_gpfs_write_request {
	uint32_t command;
	char pathname[QS_PROTOCOL_NAME_SIZE];
	int32_t offset;
	uint32_t count;
	uint32_t backup;
	uint8_t data[QS_GPFS_DATA_SIZE];
} __attribute__((packed));

struct qs_gpfs_rename_request {
	uint32_t command;
	char from[QS_PROTOCOL_NAME_SIZE];
	char to[QS_PROTOCOL_NAME_SIZE];
} __attribute__((packed));

struct qs_gpfs_io_response {
	uint32_t command;
	int32_t error;
	uint32_t count;
	uint8_t data[QS_GPFS_DATA_SIZE];
} __attribute__((packed));

struct qs_gpfs_error_response {
	uint32_t command;
	int32_t error;
} __attribute__((packed));

struct qs_gpfs_version_response {
	uint32_t command;
	uint32_t version;
	int32_t error;
} __attribute__((packed));

_Static_assert(offsetof(struct qs_fs_open_request, flags) == 260,
	       "FS open flags offset");
_Static_assert(offsetof(struct qs_fs_read_response, result) == 20004,
	       "FS read result offset");
_Static_assert(offsetof(struct qs_fs_write_request, count) == 20008,
	       "FS write count offset");
_Static_assert(sizeof(struct qs_fs_rename_request) == 516,
	       "FS rename request size");
_Static_assert(offsetof(struct qs_gpfs_read_request, offset) == 0x104,
	       "GPFS offset field");
_Static_assert(offsetof(struct qs_gpfs_write_request, data) == 0x110,
	       "GPFS write data offset");
_Static_assert(offsetof(struct qs_gpfs_io_response, data) == 12,
	       "GPFS read data offset");
_Static_assert(sizeof(struct qs_gpfs_rename_request) == 516,
	       "GPFS rename request size");
_Static_assert(offsetof(struct qs_gpfs_version_response, version) == 4,
	       "GPFS version offset");
_Static_assert(offsetof(struct qs_gpfs_version_response, error) == 8,
	       "GPFS version error offset");
