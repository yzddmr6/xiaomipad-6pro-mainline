// SPDX-License-Identifier: BSD-2-Clause
#pragma once

#include <stdint.h>

enum qs_app_acquisition {
	QS_APP_ATTACHED,
	QS_APP_LOADED,
};

struct qs_app_acquire_ops {
	int (*attach)(void *context, uint32_t *session);
	int (*load)(void *context, uint32_t *session);
};

int qs_app_acquire(const struct qs_app_acquire_ops *ops, void *context,
		   enum qs_app_acquisition *acquisition, uint32_t *session);
