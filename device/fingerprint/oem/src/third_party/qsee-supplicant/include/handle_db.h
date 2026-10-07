/*
 * Copyright (c) 2014, Linaro Limited
 * All rights reserved.
 *
 * SPDX-License-Identifier: BSD-2-Clause
 *
 * Adapted from OP-TEE tee-supplicant's handle database.
 */
#pragma once

#include <pthread.h>
#include <stddef.h>

struct handle_db {
	void **ptrs;
	size_t max_ptrs;
	pthread_mutex_t *mutex;
};

#define HANDLE_DB_INITIALIZER { NULL, 0, NULL }
#define HANDLE_DB_INITIALIZER_WITH_MUTEX(mutex_) { NULL, 0, (mutex_) }

void handle_db_set_mutex(struct handle_db *db, pthread_mutex_t *mutex);
void handle_db_destroy(struct handle_db *db);
int handle_get(struct handle_db *db, void *ptr);
void *handle_put(struct handle_db *db, int handle);
void *handle_lookup(struct handle_db *db, int handle);
void handle_foreach_put(struct handle_db *db,
			void (*callback)(int handle, void *ptr, void *arg),
			void *arg);
