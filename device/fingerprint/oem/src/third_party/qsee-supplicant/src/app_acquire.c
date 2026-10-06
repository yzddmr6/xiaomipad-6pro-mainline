// SPDX-License-Identifier: BSD-2-Clause
#include "app_acquire.h"

#include <errno.h>

int qs_app_acquire(const struct qs_app_acquire_ops *ops, void *context,
		   enum qs_app_acquisition *acquisition, uint32_t *session)
{
	int load_error;
	int result;

	if (!ops || !ops->attach || !ops->load || !acquisition || !session)
		return -EINVAL;

	result = ops->attach(context, session);
	if (!result) {
		*acquisition = QS_APP_ATTACHED;
		return 0;
	}
	if (result != -ENOENT)
		return result;

	result = ops->load(context, session);
	if (!result) {
		*acquisition = QS_APP_LOADED;
		return 0;
	}

	load_error = result;

	/* A failed load may have raced with another successful loader. */
	result = ops->attach(context, session);
	if (!result) {
		*acquisition = QS_APP_ATTACHED;
		return 0;
	}

	return load_error;
}
