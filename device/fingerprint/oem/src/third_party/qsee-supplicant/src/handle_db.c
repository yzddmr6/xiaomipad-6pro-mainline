/*
 * Copyright (c) 2014, Linaro Limited
 * All rights reserved.
 *
 * SPDX-License-Identifier: BSD-2-Clause
 *
 * Adapted from OP-TEE tee-supplicant's handle database.
 */
#include "handle_db.h"

#include <stdlib.h>
#include <string.h>

#define HANDLE_DB_INITIAL_CAPACITY 4

static void lock(struct handle_db *db)
{
	if (db->mutex)
		pthread_mutex_lock(db->mutex);
}

static void unlock(struct handle_db *db)
{
	if (db->mutex)
		pthread_mutex_unlock(db->mutex);
}

void handle_db_set_mutex(struct handle_db *db, pthread_mutex_t *mutex)
{
	db->mutex = mutex;
}

void handle_db_destroy(struct handle_db *db)
{
	if (!db)
		return;

	lock(db);
	free(db->ptrs);
	db->ptrs = NULL;
	db->max_ptrs = 0;
	unlock(db);
}

int handle_get(struct handle_db *db, void *ptr)
{
	size_t index;
	size_t new_max_ptrs;
	void **new_ptrs;
	int result = -1;

	if (!db || !ptr)
		return -1;

	lock(db);
	for (index = 0; index < db->max_ptrs; index++) {
		if (!db->ptrs[index]) {
			db->ptrs[index] = ptr;
			result = (int)index;
			goto out;
		}
	}

	new_max_ptrs = db->max_ptrs ? db->max_ptrs * 2 :
			       HANDLE_DB_INITIAL_CAPACITY;
	new_ptrs = realloc(db->ptrs, new_max_ptrs * sizeof(*new_ptrs));
	if (!new_ptrs)
		goto out;

	db->ptrs = new_ptrs;
	memset(db->ptrs + db->max_ptrs, 0,
	       (new_max_ptrs - db->max_ptrs) * sizeof(*db->ptrs));
	db->max_ptrs = new_max_ptrs;
	db->ptrs[index] = ptr;
	result = (int)index;

out:
	unlock(db);
	return result;
}

void *handle_put(struct handle_db *db, int handle)
{
	void *ptr = NULL;

	if (!db || handle < 0)
		return NULL;

	lock(db);
	if ((size_t)handle < db->max_ptrs) {
		ptr = db->ptrs[handle];
		db->ptrs[handle] = NULL;
	}
	unlock(db);
	return ptr;
}

void *handle_lookup(struct handle_db *db, int handle)
{
	void *ptr = NULL;

	if (!db || handle < 0)
		return NULL;

	lock(db);
	if ((size_t)handle < db->max_ptrs)
		ptr = db->ptrs[handle];
	unlock(db);
	return ptr;
}

void handle_foreach_put(struct handle_db *db,
			void (*callback)(int handle, void *ptr, void *arg),
			void *arg)
{
	size_t index;

	if (!db || !callback)
		return;

	lock(db);
	for (index = 0; index < db->max_ptrs; index++) {
		if (db->ptrs[index]) {
			callback((int)index, db->ptrs[index], arg);
			db->ptrs[index] = NULL;
		}
	}
	unlock(db);
}
