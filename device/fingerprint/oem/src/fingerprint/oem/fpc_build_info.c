// SPDX-License-Identifier: BSD-2-Clause
/* FPC OEM command diagnostic using the HAL's QSEE layout.
 * A successful TEE invoke does not prove that the sensor returned an image.
 */
#include <errno.h>
#include <fcntl.h>
#include <linux/tee.h>
#include <stdint.h>
#include <limits.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/random.h>
#include <sys/stat.h>
#include <unistd.h>

#define QSEE_IMPL_ID 5u
#define APP_NAME "fpcliu"
#define KEYMASTER_APP_NAME "keymaster64"
#define REQUEST_SIZE 64u
#define RESPONSE_SIZE 64u
#define KEYMASTER_RESPONSE_SIZE 960u
#define PAYLOAD_SIZE 4096u
#define HAT_SIZE 69u

static volatile sig_atomic_t operation_cancelled;

static void cancel_operation(int signal_number)
{
	(void)signal_number;
	operation_cancelled = 1;
}

struct shared {
	int id;
	void *address;
	size_t size;
};

static int open_qsee_device(int privileged)
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
		    version.impl_id == QSEE_IMPL_ID)
			return fd;
		close(fd);
	}
	errno = ENODEV;
	return -1;
}

static int allocate_shared(int fd, size_t size, struct shared *memory)
{
	struct tee_ioctl_shm_alloc_data allocation = { .size = size };
	int shm_fd = ioctl(fd, TEE_IOC_SHM_ALLOC, &allocation);

	if (shm_fd < 0)
		return -1;
	memory->address = mmap(NULL, allocation.size, PROT_READ | PROT_WRITE,
			       MAP_SHARED, shm_fd, 0);
	close(shm_fd);
	if (memory->address == MAP_FAILED) {
		memory->address = NULL;
		return -1;
	}
	memory->id = allocation.id;
	memory->size = allocation.size;
	memset(memory->address, 0, memory->size);
	return 0;
}

static int open_named_session(int fd, const char *app_name, uint32_t *session_id)
{
	uint64_t storage[(sizeof(struct tee_ioctl_open_session_arg) +
			  sizeof(struct tee_ioctl_param) + 7) / 8] = {};
	struct tee_ioctl_open_session_arg *session = (void *)storage;
	struct tee_ioctl_param *param = session->params;
	struct tee_ioctl_buf_data data = {
		.buf_ptr = (uintptr_t)storage,
		.buf_len = sizeof(storage),
	};
	struct shared name = {};

	if (allocate_shared(fd, 64, &name))
		return -1;
	memcpy(name.address, app_name, strlen(app_name) + 1);
	session->num_params = 1;
	param[0].attr = TEE_IOCTL_PARAM_ATTR_TYPE_MEMREF_INPUT;
	param[0].b = strlen(app_name) + 1;
	param[0].c = name.id;
	int result = ioctl(fd, TEE_IOC_OPEN_SESSION, &data);
	int saved_errno = errno;
	munmap(name.address, name.size);
	if (result) {
		errno = saved_errno;
		return -1;
	}
	if (session->ret) {
		errno = EPROTO;
		return -1;
	}
	*session_id = session->session;
	return 0;
}

enum query_kind {
	BUILD_INFO,
	SENSOR_INIT,
	SENSOR_SLEEP,
	SENSOR_OTP,
	SENSOR_CHECK_FINGER_LOST,
	SENSOR_WAIT_FINGER_LOST,
	SENSOR_WAIT_FINGER_DOWN,
	SENSOR_CHECK_FINGER_STATE,
	BIO_INIT,
	BIO_LOAD_EMPTY_DB,
	BIO_SET_GID,
	BIO_SET_ACTIVE_GROUP,
	BIO_BEGIN_ENROL,
	BIO_ENROL,
	BIO_END_ENROL,
	BIO_IDENTIFY,
	BIO_UPDATE_TEMPLATE,
	BIO_LOAD_INSTANCE_DATA,
	CAPTURE_ONCE,
	CAPTURE_ENROL,
	SENSORTEST_SELF_TEST,
	SENSORTEST_CAPTURE_UNCALIBRATED
};

static const char *fpc_status_str(int32_t status)
{
	switch (status) {
	case 0:
		return "FPC_STATUS_OK";
	case 1:
		return "FPC_STATUS_WAIT_TIME";
	case 2:
		return "FPC_STATUS_2";
	case 3:
		return "FPC_STATUS_FINGER_LOST";
	case 4:
		return "FPC_STATUS_BAD_QUALITY";
	case 7:
		return "FPC_STATUS_ENROLL_LOW_COVERAGE";
	default:
		return "FPC_STATUS_UNKNOWN";
	}
}

static int invoke_payload(int fd, uint32_t session_id,
			  const struct shared *payload)
{
	uint64_t storage[(sizeof(struct tee_ioctl_invoke_arg) +
			  4 * sizeof(struct tee_ioctl_param) + 7) / 8] = {};
	struct tee_ioctl_invoke_arg *invoke = (void *)storage;
	struct tee_ioctl_param *param = invoke->params;
	struct tee_ioctl_buf_data data = {
		.buf_ptr = (uintptr_t)storage, .buf_len = sizeof(storage),
	};
	struct shared request = {}, response = {};
	int result = -1;

	if (allocate_shared(fd, REQUEST_SIZE, &request) ||
	    allocate_shared(fd, RESPONSE_SIZE, &response))
		goto fail;
	((uint32_t *)request.address)[0] = (uint32_t)payload->size;
	invoke->session = session_id;
	invoke->num_params = 4;
	param[0].attr = TEE_IOCTL_PARAM_ATTR_TYPE_MEMREF_INPUT;
	param[0].b = REQUEST_SIZE;
	param[0].c = request.id;
	param[1].attr = TEE_IOCTL_PARAM_ATTR_TYPE_MEMREF_OUTPUT;
	param[1].b = RESPONSE_SIZE;
	param[1].c = response.id;
	param[2].attr = TEE_IOCTL_PARAM_ATTR_TYPE_VALUE_INPUT;
	param[2].a = 4;
	param[2].b = 4;
	param[3].attr = TEE_IOCTL_PARAM_ATTR_TYPE_MEMREF_INOUT;
	param[3].b = payload->size;
	param[3].c = payload->id;
	if (ioctl(fd, TEE_IOC_INVOKE, &data))
		goto fail;
	if (invoke->ret) {
		fprintf(stderr, "tee_invoke_ret=%u origin=%u\n",
			invoke->ret, invoke->ret_origin);
		errno = EPROTO;
		goto fail;
	}
	result = (int32_t)((uint32_t *)response.address)[0];
	printf("tee_ret=%u origin=%u response_status=%d status_hex=%08x\n",
	       invoke->ret, invoke->ret_origin, result, (uint32_t)result);
	goto out;
fail:
	perror("invoke_payload");
out:
	if (response.address)
		munmap(response.address, response.size);
	if (request.address)
		munmap(request.address, request.size);
	return result;
}

/* Resident Keymaster uses function 0 and two memrefs. The caller owns the
 * response mapping, including cleanup when this function returns an error.
 */
static int keymaster_request(uint32_t command, uint32_t argument,
			     struct shared *response)
{
	uint64_t storage[(sizeof(struct tee_ioctl_invoke_arg) +
			  2 * sizeof(struct tee_ioctl_param) + 7) / 8] = {};
	struct tee_ioctl_invoke_arg *invoke = (void *)storage;
	struct tee_ioctl_param *param = invoke->params;
	struct tee_ioctl_buf_data data = {
		.buf_ptr = (uintptr_t)storage, .buf_len = sizeof(storage),
	};
	struct shared request = {};
	uint32_t km_session;
	uint32_t *km_words;
	int km_fd = -1;
	int result = -1;

	km_fd = open_qsee_device(0);
	if (km_fd < 0 || open_named_session(km_fd, KEYMASTER_APP_NAME, &km_session)) {
		fprintf(stderr, "keymaster_session_unavailable=1 errno=%d\n", errno);
		goto out;
	}
	if (allocate_shared(km_fd, REQUEST_SIZE, &request) ||
	    allocate_shared(km_fd, KEYMASTER_RESPONSE_SIZE, response)) {
		perror("keymaster_allocate");
		goto out;
	}
	km_words = request.address;
	km_words[0] = command;
	km_words[1] = argument;
	invoke->session = km_session;
	invoke->num_params = 2;
	param[0].attr = TEE_IOCTL_PARAM_ATTR_TYPE_MEMREF_INPUT;
	param[0].b = REQUEST_SIZE;
	param[0].c = request.id;
	param[1].attr = TEE_IOCTL_PARAM_ATTR_TYPE_MEMREF_OUTPUT;
	param[1].b = KEYMASTER_RESPONSE_SIZE;
	param[1].c = response->id;
	if (ioctl(km_fd, TEE_IOC_INVOKE, &data)) {
		perror("keymaster_invoke");
		goto out;
	}
	if (invoke->ret) {
		fprintf(stderr, "keymaster_tee_error=%u origin=%u\n",
			invoke->ret, invoke->ret_origin);
		goto out;
	}
	int32_t status = ((int32_t *)response->address)[0];
	printf("keymaster_command=0x%x app_status=%d\n", command, status);
	result = status ? -1 : 0;
out:
	if (request.address)
		munmap(request.address, request.size);
	if (km_fd >= 0)
		close(km_fd);
	return result;
}

/* OEM HAL fpc_tee_init_hw_auth: command (0x205, 2) then FPC (3, 5).
 * The opaque blob never belongs in a diagnostic log or a normal-world file.
 */
static int init_hw_auth(int fpc_fd, uint32_t fpc_session)
{
	struct shared response = {}, fpc_payload = {};
	uint32_t *rsp_words, *fpc_words;
	uint32_t offset, length;
	int result = -1;
	int status;

	if (keymaster_request(0x205, 2, &response))
		goto out;
	rsp_words = response.address;
	offset = rsp_words[1];
	length = rsp_words[2];
	if (!length || offset > KEYMASTER_RESPONSE_SIZE ||
	    length > KEYMASTER_RESPONSE_SIZE - offset ||
	    length > PAYLOAD_SIZE - 16u) {
		fprintf(stderr, "hw_auth=keymaster_invalid_response offset=%u length=%u\n",
			offset, length);
		goto out;
	}
	if (allocate_shared(fpc_fd, PAYLOAD_SIZE, &fpc_payload)) {
		perror("hw_auth_fpc_allocate");
		goto out;
	}
	fpc_words = fpc_payload.address;
	fpc_words[0] = 3;
	fpc_words[1] = 5; /* OEM HAL rodata VA 0x19388. */
	fpc_words[3] = length;
	memcpy((uint8_t *)fpc_payload.address + 16,
	       (uint8_t *)response.address + offset, length);
	explicit_bzero(response.address, response.size);
	status = invoke_payload(fpc_fd, fpc_session, &fpc_payload);
	if (status) {
		fprintf(stderr, "hw_auth=fpc_init_failed status=%d\n", status);
		goto out;
	}
	printf("hw_auth=OK encapsulated_bytes=%u\n", length);
	result = 0;
out:
	if (fpc_payload.address) {
		explicit_bzero(fpc_payload.address, fpc_payload.size);
		munmap(fpc_payload.address, fpc_payload.size);
	}
	if (response.address) {
		explicit_bzero(response.address, response.size);
		munmap(response.address, response.size);
	}
	return result;
}

/* Reference-platform query only; no version negotiation or sensor access. */
static int keymaster_info(void)
{
	struct shared response = {};
	int result = keymaster_request(0x200, 0, &response);

	if (!result) {
		uint32_t *words = response.address;
		printf("keymaster_api=%u.%u ta_version=%u.%u\n",
		       words[1], words[2], words[3], words[4]);
	}
	if (response.address) {
		explicit_bzero(response.address, response.size);
		munmap(response.address, response.size);
	}
	return result;
}

/* HAL fpc_tee_get_enrol_challenge reads a 64-bit value at payload +8. */
static int get_enrol_challenge(int fd, uint32_t session, uint64_t *challenge)
{
	struct shared payload = {};
	int result = -1;

	if (allocate_shared(fd, PAYLOAD_SIZE, &payload)) {
		perror("allocate_challenge_payload");
		return -1;
	}
	uint32_t *words = payload.address;
	words[0] = 3;
	words[1] = 2;
	if (invoke_payload(fd, session, &payload))
		goto out;
	memcpy(challenge, (uint8_t *)payload.address + 8, sizeof(*challenge));
	if (!*challenge) {
		fprintf(stderr, "enrol_challenge_invalid=zero\n");
		goto out;
	}
	printf("enrol_challenge_available=1\n");
	result = 0;
out:
	munmap(payload.address, payload.size);
	return result;
}

/* Receive a credential provider's packed, signed HAT for this session.
 * FPC (3,3) checks a fresh signed challenge before capture and before END_ENROL.
 * No token bytes are printed, rewritten, persisted or signed by this client.
 */
static int authorize_enrol(int fd, uint32_t session, int hat_fd, int challenge_fd,
			   uint64_t challenge)
{
	struct shared payload = {};
	uint8_t hat[HAT_SIZE] = {};
	uint64_t token_challenge;
	int result = -1;

	if (challenge_fd >= 0) {
		/* Dedicated binary pipe to the provider; keep this FPC session open. */
		for (size_t offset = 0; offset < sizeof(challenge);) {
			if (operation_cancelled)
				goto out;
			ssize_t count = write(challenge_fd, (uint8_t *)&challenge + offset,
					      sizeof(challenge) - offset);
			if (count < 0 && errno == EINTR)
				continue;
			if (count <= 0) {
				perror("write_enrol_challenge");
				goto out;
			}
			offset += (size_t)count;
		}
	}
	printf("HAT_REQUIRED bytes=%u fd=%d\n", HAT_SIZE, hat_fd);
	for (size_t offset = 0; offset < sizeof(hat);) {
		if (operation_cancelled)
			goto out;
		struct pollfd input = { .fd = hat_fd, .events = POLLIN };
		int ready = poll(&input, 1, 30000);
		if (operation_cancelled)
			goto out;
		if (ready < 0 && errno == EINTR)
			continue;
		if (ready <= 0 || !(input.revents & (POLLIN | POLLHUP))) {
			fprintf(stderr, "hat_input_unavailable poll=%d revents=0x%x errno=%d\n",
				ready, input.revents, ready < 0 ? errno : 0);
			goto out;
		}
		ssize_t count = read(hat_fd, hat + offset, sizeof(hat) - offset);
		if (count < 0 && errno == EINTR)
			continue;
		if (count <= 0) {
			fprintf(stderr, "hat_input_incomplete bytes=%zu errno=%d\n",
				offset, count < 0 ? errno : 0);
			goto out;
		}
		offset += (size_t)count;
	}
	memcpy(&token_challenge, hat + 1, sizeof(token_challenge));
	if (hat[0] || token_challenge != challenge) {
		fprintf(stderr, "hat_invalid=version_or_challenge\n");
		goto out;
	}
	if (allocate_shared(fd, PAYLOAD_SIZE, &payload)) {
		perror("allocate_hat_payload");
		goto out;
	}
	uint32_t *words = payload.address;
	words[0] = 3;
	words[1] = 3;
	words[3] = HAT_SIZE;
	memcpy((uint8_t *)payload.address + 16, hat, sizeof(hat));
	if (invoke_payload(fd, session, &payload)) {
		fprintf(stderr, "enrol_authorization=TA_rejected\n");
		goto out;
	}
	printf("enrol_authorization=OK\n");
	result = 0;
out:
	explicit_bzero(hat, sizeof(hat));
	if (payload.address) {
		explicit_bzero(payload.address, payload.size);
		munmap(payload.address, payload.size);
	}
	return result;
}

static int query_output(int fd, uint32_t session_id, enum query_kind kind,
			uint32_t *output_word2)
{
	struct shared payload = {};
	uint32_t *words;
	int result = -1;
	int32_t status;

	if (allocate_shared(fd, PAYLOAD_SIZE, &payload)) {
		perror("allocate_payload");
		goto out;
	}

	/* Exact command headers copied from the OEM HAL entry points. */
	words = payload.address;
	switch (kind) {
	case BUILD_INFO:
		words[0] = 12;
		words[1] = 1;
		words[5] = 0x400;
		break;
	case SENSOR_INIT:
		words[0] = 10;
		words[1] = 0;
		break;
	case SENSOR_SLEEP:
		words[0] = 10;
		words[1] = 5;
		break;
	case SENSOR_OTP:
		words[0] = 10;
		words[1] = 6;
		break;
	case SENSOR_CHECK_FINGER_LOST:
		words[0] = 10;
		words[1] = 1;
		break;
	case SENSOR_WAIT_FINGER_LOST:
		words[0] = 10;
		words[1] = 2;
		break;
	case SENSOR_WAIT_FINGER_DOWN:
		words[0] = 10;
		words[1] = 3;
		break;
	case SENSOR_CHECK_FINGER_STATE:
		words[0] = 10;
		words[1] = 11;
		break;
	case CAPTURE_ONCE:
		words[0] = 10;
		words[1] = 4;
		words[5] = 1; /* HAL capture_image(..., 0): words[5]=1 for no finger wait. */
		break;
	case CAPTURE_ENROL:
		words[0] = 10;
		words[1] = 4;
		words[2] = 1; /* HAL capture_image(..., 1): finger wait already completed. */
		break;
	case BIO_INIT:
		words[0] = 11;
		words[1] = 0;
		break;
	case BIO_BEGIN_ENROL:
		words[0] = 11;
		words[1] = 1;
		break;
	case BIO_ENROL:
		words[0] = 11;
		words[1] = 2;
		break;
	case BIO_END_ENROL:
		words[0] = 11;
		words[1] = 3;
		break;
	case BIO_IDENTIFY:
		words[0] = 11;
		words[1] = 4;
		break;
	case BIO_UPDATE_TEMPLATE:
		words[0] = 11;
		words[1] = 5;
		break;
	case BIO_LOAD_INSTANCE_DATA:
		words[0] = 11;
		words[1] = 19; /* HAL fpc_tee_load_instance_data, rodata VA 0x193e0. */
		break;
	case BIO_LOAD_EMPTY_DB:
		words[0] = 11;
		words[1] = 7;
		break;
	case BIO_SET_ACTIVE_GROUP:
		words[0] = 11;
		words[1] = 10;
		break;
	case BIO_SET_GID:
		words[0] = 11;
		words[1] = 20; /* HAL fpc_tee_set_gid, VA 0x3b29c. */
		words[2] = 0;
		break;
	case SENSORTEST_SELF_TEST:
		words[0] = 5;
		words[1] = 0;
		break;
	case SENSORTEST_CAPTURE_UNCALIBRATED:
		words[0] = 5;
		words[1] = 6;
		break;
	}
	printf("command=%u subcommand=%u\n", words[0], words[1]);

	status = invoke_payload(fd, session_id, &payload);
	if (kind == CAPTURE_ONCE || kind == CAPTURE_ENROL)
		printf("fpc_status=%s\n", fpc_status_str(status));
	if (kind == BUILD_INFO && !status) {
		uint32_t length = ((uint32_t *)payload.address)[5];
		if (length <= 0x400) {
			printf("build_info_length=%u\nbuild_info_json=", length);
			fwrite((uint8_t *)payload.address + 24, 1,
			       strnlen((char *)payload.address + 24, length), stdout);
			putchar('\n');
		}
	}
	if (kind == CAPTURE_ONCE || kind == CAPTURE_ENROL) {
		uint32_t res_word = ((uint32_t *)payload.address)[4];
		printf("capture_result_word4=%u (0x%08x)\n", res_word, res_word);
		if (status == 3)
			printf("capture_note=FINGER_LOST_or_mapped_lower_error\n");
		else if (status == 0)
			printf("capture_note=TA_reported_capture_OK raw_image_export=unverified\n");
	}
	if (kind == BIO_ENROL && status >= 0)
		printf("enrol_remaining_touches=%u\n", ((uint32_t *)payload.address)[2]);
	if (kind == BIO_END_ENROL && !status)
		printf("enrol_template_id_nonzero=%u\n", ((uint32_t *)payload.address)[2] != 0);
	if (output_word2)
		*output_word2 = ((uint32_t *)payload.address)[2];
	result = status;
out:
	if (payload.address) {
		explicit_bzero(payload.address, payload.size);
		munmap(payload.address, payload.size);
	}
	return result;
}

static int query(int fd, uint32_t session_id, enum query_kind kind)
{
	return query_output(fd, session_id, kind, NULL);
}

/* Read current TA metadata only. No BIO_INIT, empty DB, group change or
 * acquisition is sent; a missing DB stays missing and is reported as such.
 * OEM module 11: subcmd 8 count at +8 and up to five IDs at +12;
 * subcmd 11 database/authenticator ID at +16. Do not print those identifiers.
 */
static int template_inventory(int fd, uint32_t session, uint32_t *loaded_count)
{
	struct shared payload = {};
	int result = 1;
	uint32_t count = 0;
	uint64_t authenticator_id = 0;
	if (allocate_shared(fd, 112, &payload)) {
		perror("allocate_template_metadata");
		return 1;
	}
	uint32_t *words = payload.address;
	words[0] = 11;
	words[1] = 8;
	words[2] = 5;
	int32_t enumerate_status = invoke_payload(fd, session, &payload);
	printf("template_enumerate_app_status=0x%08x\n", (uint32_t)enumerate_status);
	if (!enumerate_status) {
		if (words[0] != 11 || words[1] != 8 || words[2] > 5) {
			fprintf(stderr, "template_count_response_invalid=1\n");
			goto out;
		}
		count = words[2];
		printf("loaded_template_count=%u\n", count);
	} else {
		printf("loaded_template_count=unavailable\n");
	}
	explicit_bzero(payload.address, payload.size);
	words[0] = 11;
	words[1] = 11;
	int32_t id_status = invoke_payload(fd, session, &payload);
	printf("authenticator_id_app_status=0x%08x\n", (uint32_t)id_status);
	if (!id_status) {
		memcpy(&authenticator_id, (uint8_t *)payload.address + 16, sizeof(authenticator_id));
		printf("authenticator_id_nonzero=%u\n", authenticator_id != 0);
	}
	printf("template_inventory_scope=current_loaded_TA_DB persistent_storage_not_searched=1\n");
	result = enumerate_status || id_status ? 1 : 0;
	if (!result && loaded_count)
		*loaded_count = count;
out:
	explicit_bzero(&authenticator_id, sizeof(authenticator_id));
	explicit_bzero(payload.address, payload.size);
	munmap(payload.address, payload.size);
	return result;
}

static int wait_sensor_irq(int power_fd, const char *phase, int timeout_ms)
{
	struct pollfd event = { .fd = power_fd, .events = POLLIN };
	int result;

	do {
		result = poll(&event, 1, timeout_ms);
	} while (result < 0 && errno == EINTR && !operation_cancelled);
	if (operation_cancelled) {
		fprintf(stderr, "oem_operation_cancelled=1\n");
		return -1;
	}
	if (result == 1 && (event.revents & POLLIN)) {
		printf("finger_irq=%s\n", phase);
		return 0;
	}
	fprintf(stderr, "finger_irq_failed=%s poll_result=%d revents=0x%x errno=%d\n",
		phase, result, event.revents, result < 0 ? errno : 0);
	return -1;
}

static int clear_pending_irq(int power_fd, const char *phase)
{
	struct pollfd event = { .fd = power_fd, .events = POLLIN };
	int result = poll(&event, 1, 0);

	if (result < 0) {
		perror("clear_pending_irq");
		return -1;
	}
	if (result && !(event.revents & POLLIN)) {
		fprintf(stderr, "pending_irq_error=%s revents=0x%x\n",
			phase, event.revents);
		return -1;
	}
	if (result)
		printf("stale_irq_drained=%s\n", phase);
	return 0;
}

/* HAL capture_image(mode=1) calls wait_finger_down with qualifier=0:
 * 10/3, IRQ, then capture. Qualification stays inside the capture command;
 * an extra 10/11 here would consume the IRQ before capture can inspect it.
 */
static int wait_finger_down(int fd, uint32_t session, int power_fd)
{
	if (clear_pending_irq(power_fd, "before_down"))
		return -1;
	if (query(fd, session, SENSOR_WAIT_FINGER_DOWN))
		return -1;
	if (clear_pending_irq(power_fd, "after_down_setup"))
		return -1;
	printf("READY finger_irq_armed=down press finger now\n");
	return wait_sensor_irq(power_fd, "down", 120000);
}

/* HAL fpc_tee_wait_finger_lost: 10/2, IRQ, 10/11, 10/1. */
static int wait_finger_lost(int fd, uint32_t session, int power_fd)
{
	int status = query(fd, session, SENSOR_CHECK_FINGER_LOST);

	if (status < 0)
		return -1;
	if (status)
		return 0;
	if (clear_pending_irq(power_fd, "before_up"))
		return -1;
	for (unsigned int attempt = 0; attempt < 600; attempt++) {
		status = query(fd, session, SENSOR_WAIT_FINGER_LOST);
		if (status < 0)
			return -1;
		if (status)
			return status;
		if (wait_sensor_irq(power_fd, "up", 30000))
			return -1;
		status = query(fd, session, SENSOR_CHECK_FINGER_STATE);
		if (status < 0)
			return -1;
		if (!status) {
			usleep(50000);
			continue;
		}
		status = query(fd, session, SENSOR_CHECK_FINGER_LOST);
		if (status < 0)
			return -1;
		if (status)
			return 0;
		usleep(50000);
	}
	fprintf(stderr, "finger_lost_wait_exhausted=600\n");
	return -1;
}

/* OEM HAL store_template_db_host: 4/6 size, 4/14 begin, 4/16 read,
 * 4/15 finish. The TA returns remaining bytes as positive application status.
 * Database contents are opaque; never include them in diagnostic logs.
 */
static int db_command(int fd, uint32_t session, uint32_t command,
		      uint32_t word3, uint32_t word4, void *chunk,
		      int input_chunk,
		      uint32_t *size_output)
{
	struct shared payload = {};
	uint32_t *words;
	int status;

	if (allocate_shared(fd, PAYLOAD_SIZE, &payload)) {
		perror("allocate_db_payload");
		return -1;
	}
	words = payload.address;
	words[0] = 4;
	words[1] = command;
	words[3] = word3;
	words[4] = word4;
	if (input_chunk && chunk)
		memcpy((uint8_t *)payload.address + 16, chunk, word3);
	printf("command=4 subcommand=%u db_word3=%u db_word4=%u\n",
	       command, word3, word4);
	status = invoke_payload(fd, session, &payload);
	if (size_output)
		*size_output = words[3];
	if (status >= 0 && chunk && !input_chunk)
		memcpy(chunk, (uint8_t *)payload.address + 16, word3);
	munmap(payload.address, payload.size);
	return status;
}

static int store_database(int fd, uint32_t session, const char *path)
{
	uint8_t chunk[PAYLOAD_SIZE - 16];
	uint32_t total = 0, remaining;
	int file_fd = -1, status, result = -1, file_complete = 0;

	if (db_command(fd, session, 6, 0, 0, NULL, 0, &total))
		return -1;
	if (!total || total > INT32_MAX) {
		fprintf(stderr, "db_export_invalid_size=%u\n", total);
		return -1;
	}
	if (db_command(fd, session, 14, 0, total, NULL, 0, NULL))
		return -1;
	file_fd = open(path, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
	if (file_fd < 0) {
		perror("create_database");
		goto out;
	}
	remaining = total;
	while (remaining) {
		uint32_t count = remaining < sizeof(chunk) ? remaining : sizeof(chunk);
		status = db_command(fd, session, 16, count, 0, chunk, 0, NULL);
		if (status < 0 || (uint32_t)status != remaining - count) {
			fprintf(stderr, "db_export_remaining_mismatch expected=%u actual=%d\n",
				remaining - count, status);
			goto out;
		}
		for (uint32_t offset = 0; offset < count;) {
			ssize_t written = write(file_fd, chunk + offset, count - offset);
			if (written < 0 && errno == EINTR)
				continue;
			if (written <= 0) {
				perror("write_database");
				goto out;
			}
			offset += (uint32_t)written;
		}
		remaining = (uint32_t)status;
	}
	if (fsync(file_fd)) {
		perror("fsync_database");
		goto out;
	}
	file_complete = 1;
	result = 0;
out:
	if (db_command(fd, session, 15, 0, 0, NULL, 0, NULL))
		result = -1;
	if (file_fd >= 0) {
		if (close(file_fd)) {
			perror("close_database");
			result = -1;
		}
		if (result && !file_complete)
			unlink(path);
	}
	if (result && file_complete)
		fprintf(stderr, "db_file_preserved=%s cleanup_or_close_failed=1\n", path);
	if (!result)
		printf("db_export=OK bytes=%u path=%s biometric_validation=unverified\n",
		       total, path);
	return result;
}

/* OEM HAL load_template_db_host sends 4/14 mode=1 then 4/17 chunks.
 * The last chunk makes the TA validate and install the opaque database.
 */
static int load_database(int fd, uint32_t session, const char *path)
{
	uint8_t chunk[PAYLOAD_SIZE - 16];
	struct stat info;
	uint32_t remaining;
	int file_fd = -1, status, result = -1, started = 0;

	file_fd = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
	if (file_fd < 0) {
		perror("open_database");
		return -1;
	}
	if (fstat(file_fd, &info) || !S_ISREG(info.st_mode) ||
	    info.st_size <= 0 || info.st_size > INT32_MAX) {
		fprintf(stderr, "db_import_invalid_file=%s\n", path);
		goto out;
	}
	remaining = (uint32_t)info.st_size;
	if (db_command(fd, session, 14, 1, remaining, NULL, 0, NULL))
		goto out;
	started = 1;
	while (remaining) {
		uint32_t count = remaining < sizeof(chunk) ? remaining : sizeof(chunk);
		for (uint32_t offset = 0; offset < count;) {
			ssize_t received = read(file_fd, chunk + offset, count - offset);
			if (received < 0 && errno == EINTR)
				continue;
			if (received <= 0) {
				fprintf(stderr, "db_import_short_read=%s\n", path);
				goto out;
			}
			offset += (uint32_t)received;
		}
		status = db_command(fd, session, 17, count, 0, chunk, 1, NULL);
		if (status < 0 || (uint32_t)status != remaining - count) {
			fprintf(stderr, "db_import_remaining_mismatch expected=%u actual=%d\n",
				remaining - count, status);
			goto out;
		}
		remaining = (uint32_t)status;
	}
	result = 0;
out:
	if (started && db_command(fd, session, 15, 0, 0, NULL, 0, NULL))
		result = -1;
	if (close(file_fd))
		result = -1;
	if (!result)
		printf("db_import=OK path=%s biometric_validation=unverified\n", path);
	return result;
}

/* Original HAL set_auth_challenge is 3/1, with the 64-bit value at +8.
 * This is a verification operation ID, separate from the enrol 3/2 challenge.
 */
static int set_auth_challenge(int fd, uint32_t session, uint64_t *challenge)
{
	struct shared payload = {};
	int result = -1;

	for (size_t offset = 0; offset < sizeof(*challenge);) {
		ssize_t count = getrandom((uint8_t *)challenge + offset,
					 sizeof(*challenge) - offset, 0);
		if (count < 0 && errno == EINTR)
			continue;
		if (count <= 0) {
			perror("authentication_operation_id");
			return -1;
		}
		offset += (size_t)count;
	}
	if (!*challenge || allocate_shared(fd, PAYLOAD_SIZE, &payload))
		return -1;
	uint32_t *words = payload.address;
	words[0] = 3;
	words[1] = 1;
	memcpy((uint8_t *)payload.address + 8, challenge, sizeof(*challenge));
	if (!invoke_payload(fd, session, &payload))
		result = 0;
	explicit_bzero(payload.address, payload.size);
	munmap(payload.address, payload.size);
	return result;
}

/* Match output follows fpc_tee_identify: primary ID at +8, auxiliary
 * statistics at +12. Neither identifiers nor auxiliary data enter logs.
 * The TA's auxiliary template index at +44 is not the HAL callback ID.
 */
static int identify_finger(int fd, uint32_t session, int *matched)
{
	struct shared payload = {};
	int status = -1;

	*matched = 0;
	if (allocate_shared(fd, PAYLOAD_SIZE, &payload))
		return -1;
	uint32_t *words = payload.address;
	words[0] = 11;
	words[1] = 4;
	status = invoke_payload(fd, session, &payload);
	if (!status)
		*matched = words[2] != 0;
	printf("identify_app_status=%d candidate_present=%u\n", status, *matched);
	explicit_bzero(payload.address, payload.size);
	munmap(payload.address, payload.size);
	return status;
}

/* OEM get_auth_result sends 3/4, length69 at +12 and receives an opaque HAT
 * at +16. Check its envelope against this session's fresh operation ID;
 * this does not verify the MAC or authorize desktop login.
 */
static int get_auth_result(int fd, uint32_t session, uint64_t challenge)
{
	struct shared payload = {};
	uint64_t returned_challenge;
	int result = -1;

	if (allocate_shared(fd, HAT_SIZE + 16u, &payload))
		return -1;
	uint32_t *words = payload.address;
	words[0] = 3;
	words[1] = 4;
	words[3] = HAT_SIZE;
	if (invoke_payload(fd, session, &payload))
		goto out;
	const uint8_t *hat = (uint8_t *)payload.address + 16;
	memcpy(&returned_challenge, hat + 1, sizeof(returned_challenge));
	if (hat[0] || returned_challenge != challenge) {
		fprintf(stderr, "auth_result_invalid=version_or_operation_id\n");
		goto out;
	}
	printf("auth_result_available=1 auth_result_bytes=%u signature_validation=not_performed\n",
	       HAT_SIZE);
	result = 0;
out:
	explicit_bzero(payload.address, payload.size);
	munmap(payload.address, payload.size);
	return result;
}

/* One real attempt against an explicitly supplied existing DB. The original
 * HAL sets GID before import, then activates indices and loads instance data.
 * Update11/5 runs for status0, including a miss; status4/12 can also require
 * DB export. A supplied output path is exclusive and never replaces input.
 */
static int match_existing(int fd, uint32_t session, int power_fd,
			  const char *database, const char *updated_database,
			  int single_template)
{
	uint32_t count = 0, changed = 0;
	uint64_t challenge;
	int status, matched, auth_status = 0;

	if (query(fd, session, BIO_INIT) || init_hw_auth(fd, session) ||
	    query(fd, session, BIO_SET_GID) || load_database(fd, session, database) ||
	    query(fd, session, BIO_SET_ACTIVE_GROUP) ||
	    query(fd, session, BIO_LOAD_INSTANCE_DATA) ||
	    template_inventory(fd, session, &count))
		return 1;
	if (!count) {
		fprintf(stderr, "match_unavailable=database_has_no_templates touch_not_collected=1\n");
		return 1;
	}
	if (single_template && count != 1) {
		fprintf(stderr, "match_unavailable=single_template_database_required touch_not_collected=1\n");
		return 1;
	}
	if (set_auth_challenge(fd, session, &challenge) ||
	    wait_finger_down(fd, session, power_fd))
		return 1;
	status = query(fd, session, CAPTURE_ENROL);
	if (status) {
		fprintf(stderr, "match_incomplete=capture_rejected app_status=%d\n", status);
		return 1;
	}
	status = identify_finger(fd, session, &matched);
	if (status < 0)
		return 1;
	if (!status) {
		if (matched)
			auth_status = get_auth_result(fd, session, challenge);
		if (query_output(fd, session, BIO_UPDATE_TEMPLATE, &changed))
			return 1;
	} else if (status == 4 || status == 12) {
		changed = 1;
	} else {
		fprintf(stderr, "match_incomplete=identify_status app_status=%d\n", status);
		return 1;
	}
	if (changed) {
		printf("database_update_required=1\n");
		if (updated_database) {
			if (store_database(fd, session, updated_database))
				return 1;
		} else {
			printf("database_update_pending=1 updated_output_not_requested=1\n");
		}
	}
	if (status) {
		printf("match_result=inconclusive identify_app_status=%d\n", status);
		return 1;
	}
	if (auth_status) {
		fprintf(stderr, "match_result=candidate_without_auth_result desktop_authentication=disabled\n");
		return 1;
	}
	printf("match_result=%s desktop_authentication=disabled\n", matched ? "matched" : "not_matched");
	return matched ? 0 : 3;
}

int main(int argc, char **argv)
{
	uint32_t loader_session, client_session;
	int init_sensor = argc == 2 && !strcmp(argv[1], "--init-sensor");
	int init_bio = argc == 2 && !strcmp(argv[1], "--init-bio");
	int capture_once = argc == 2 && !strcmp(argv[1], "--capture-once");
	int capture_after_enter = argc == 2 && !strcmp(argv[1], "--capture-after-enter");
	int capture_wait = argc == 2 && !strcmp(argv[1], "--capture-wait");
	int sensortest = argc == 2 && !strcmp(argv[1], "--sensortest");
	int sensor_otp = argc == 2 && !strcmp(argv[1], "--sensor-otp");
	int finger_lost_check = argc == 2 && !strcmp(argv[1], "--finger-lost-check");
	int hw_auth_probe = argc == 2 && !strcmp(argv[1], "--hw-auth-probe");
	int keymaster_probe = argc == 2 && !strcmp(argv[1], "--keymaster-info");
	int auth_renew_preflight = argc == 4 && !strcmp(argv[1], "--auth-preflight-renew");
	int auth_preflight = auth_renew_preflight || ((argc == 3 || argc == 4) && !strcmp(argv[1], "--auth-preflight"));
	int bio_existing = argc == 6 && !strcmp(argv[1], "--bio-pipeline-existing");
	int bio_pipeline = bio_existing || (argc >= 2 && !strcmp(argv[1], "--bio-pipeline"));
	int bio_prepare = argc == 2 && !strcmp(argv[1], "--bio-prepare");
	int db_export_empty = argc == 3 && !strcmp(argv[1], "--db-export-empty");
	int db_import_export = argc == 4 && !strcmp(argv[1], "--db-import-export");
	int inventory = argc == 2 && !strcmp(argv[1], "--template-inventory");
	int match_single = (argc == 3 || argc == 4) && !strcmp(argv[1], "--match-single");
	int match = match_single || ((argc == 3 || argc == 4) && !strcmp(argv[1], "--match-existing"));
	const char *db_path = bio_existing ? argv[3] : argc >= 3 ? argv[2] : "template.db";
	int hat_fd = -1;
	int challenge_fd = -1;
	int loader_fd = -1;
	int client_fd = -1;
	int power_fd = -1;
	int result = 1;
	int sensor_initialized = 0;

	if (argc > 6 || (argc > 1 && !init_sensor && !init_bio && !capture_once &&
			 !capture_after_enter && !capture_wait && !sensortest && !sensor_otp &&
			 !finger_lost_check && !hw_auth_probe && !keymaster_probe &&
			 !auth_preflight && !bio_pipeline &&
			 !bio_prepare && !db_export_empty && !db_import_export && !inventory && !match)) {
		fprintf(stderr,
			"usage: %s [--init-sensor|--init-bio|--capture-once|--capture-after-enter|--capture-wait|"
			"--sensortest|--sensor-otp|--finger-lost-check|--keymaster-info|--hw-auth-probe|"
			"--auth-preflight HAT_FD [CHALLENGE_FD]|--auth-preflight-renew HAT_FD CHALLENGE_FD|--bio-prepare|--bio-pipeline DATABASE HAT_FD CHALLENGE_FD|"
			"--bio-pipeline-existing INPUT_DATABASE OUTPUT_DATABASE HAT_FD CHALLENGE_FD|"
			"--db-export-empty DATABASE|--db-import-export INPUT OUTPUT|--template-inventory|"
			"--match-existing DATABASE [UPDATED_DATABASE]|--match-single DATABASE [UPDATED_DATABASE]]\n",
			argv[0]);
		return 2;
	}
	setvbuf(stdout, NULL, _IONBF, 0);
	if (bio_pipeline && !bio_existing && argc != 5) {
		fprintf(stderr, "bidirectional_signed_HAT_provider_required=1 usage: %s --bio-pipeline DATABASE HAT_FD CHALLENGE_FD\n",
			argv[0]);
		return 2;
	}
	if (bio_pipeline) {
		struct stat output_info;
		/* Reject an existing destination before power, authorization or capture.
		 * Export still uses O_EXCL, including if the path appears later.
		 */
		if (!lstat(db_path, &output_info) || errno != ENOENT) {
			fprintf(stderr, "enrol_output_invalid=exclusive_new_path_required existing_database_preserved=1\n");
			return 2;
		}
	}
	struct sigaction cancellation = { .sa_handler = cancel_operation };
	sigemptyset(&cancellation.sa_mask);
	if (sigaction(SIGTERM, &cancellation, NULL) || sigaction(SIGINT, &cancellation, NULL)) {
		perror("install_cancellation_handler");
		return 1;
	}
	if (bio_pipeline || auth_preflight) {
		const char *descriptor = argv[bio_existing ? 4 : bio_pipeline ? 3 : 2];
		char *end;
		errno = 0;
		long value = strtol(descriptor, &end, 10);
		if (errno || end == descriptor || *end || value < 0 || value > INT_MAX) {
			fprintf(stderr, "hat_fd_invalid=number\n");
			return 2;
		}
		hat_fd = (int)value;
		int flags = fcntl(hat_fd, F_GETFL);
		if (flags < 0 || (flags & O_ACCMODE) == O_WRONLY || isatty(hat_fd)) {
			fprintf(stderr, "hat_fd_invalid=readable_binary_provider_required\n");
			return 2;
		}
		int challenge_arg = bio_existing ? 5 : bio_pipeline ? 4 : 3;
		if (argc > challenge_arg) {
			descriptor = argv[challenge_arg];
			errno = 0;
			value = strtol(descriptor, &end, 10);
			if (errno || end == descriptor || *end || value < 0 || value > INT_MAX) {
				fprintf(stderr, "challenge_fd_invalid=number\n");
				return 2;
			}
			challenge_fd = (int)value;
			flags = fcntl(challenge_fd, F_GETFL);
			if (flags < 0 || (flags & O_ACCMODE) == O_RDONLY ||
			    isatty(challenge_fd) || challenge_fd == hat_fd) {
				fprintf(stderr, "challenge_fd_invalid=writable_binary_pipe_required\n");
				return 2;
			}
			signal(SIGPIPE, SIG_IGN);
		}
	}
	if (keymaster_probe)
		return keymaster_info() ? 1 : 0;
	if (init_sensor || init_bio || capture_once || capture_after_enter || capture_wait ||
	    sensortest || sensor_otp || finger_lost_check || hw_auth_probe || auth_preflight ||
	    bio_pipeline || bio_prepare ||
	    db_export_empty || db_import_export || match) {
		power_fd = open("/dev/fpc1020", O_RDWR | O_CLOEXEC);
		if (power_fd < 0) {
			perror("power/reset fpc1020");
			goto out;
		}
		printf("normal_world_power=held\n");
	}
	loader_fd = open_qsee_device(1);

	if (loader_fd < 0 || open_named_session(loader_fd, APP_NAME, &loader_session)) {
		perror("load fpcliu");
		goto out;
	}
	printf("loaded_session=%u\n", loader_session);
	client_fd = open_qsee_device(0);
	if (client_fd < 0 || open_named_session(client_fd, APP_NAME, &client_session)) {
		perror("attach fpcliu");
		goto out;
	}
	printf("client_session=%u\n", client_session);
	if (inventory) {
		result = template_inventory(client_fd, client_session, NULL);
		printf("template_inventory_no_capture=1 database_not_initialized_by_client=1\n");
		goto out;
	}
	if (power_fd >= 0) {
		if (query(client_fd, client_session, SENSOR_INIT))
			goto out;
		sensor_initialized = 1;
	}

	if (init_sensor) {
		result = 0;
		goto out;
	}
	if (match) {
		result = match_existing(client_fd, client_session, power_fd, argv[2],
					argc == 4 ? argv[3] : NULL, match_single);
		goto out;
	}

	if (sensor_otp) {
		if (query(client_fd, client_session, SENSOR_OTP))
			goto out;
		result = 0;
		goto out;
	}
	if (finger_lost_check) {
		int status = query(client_fd, client_session, SENSOR_CHECK_FINGER_LOST);
		printf("finger_lost_check_status=%d\n", status);
		result = status < 0 ? 1 : 0;
		goto out;
	}

	if (sensortest) {
		if (query(client_fd, client_session, SENSORTEST_SELF_TEST))
			goto out;
		result = 0;
		goto out;
	}

	if (init_bio) {
		if (query(client_fd, client_session, BIO_INIT) ||
		    query(client_fd, client_session, BIO_LOAD_EMPTY_DB))
			goto out;
		result = 0;
		goto out;
	}
	if (hw_auth_probe || auth_preflight) {
		uint64_t challenge;
		if (query(client_fd, client_session, BIO_INIT) ||
		    init_hw_auth(client_fd, client_session) ||
		    get_enrol_challenge(client_fd, client_session, &challenge))
			goto out;
		if (auth_preflight) {
			if (authorize_enrol(client_fd, client_session, hat_fd, challenge_fd, challenge))
				goto out;
			if (auth_renew_preflight) {
				if (get_enrol_challenge(client_fd, client_session, &challenge) ||
				    authorize_enrol(client_fd, client_session, hat_fd, challenge_fd, challenge))
					goto out;
				printf("auth_renewal_preflight=OK same_session=1 signed_challenges=2 touch_not_collected=1\n");
			}
			printf("auth_preflight=OK touch_not_collected=1 template_not_created=1\n");
		} else {
			printf("hw_auth_probe=OK enrol_authorization=not_checked\n");
		}
		result = 0;
		goto out;
	}

	if (db_export_empty) {
		if (query(client_fd, client_session, BIO_INIT) ||
		    query(client_fd, client_session, BIO_LOAD_EMPTY_DB))
			goto out;
		result = store_database(client_fd, client_session, db_path) ? 1 : 0;
		printf("db_test_kind=empty_database no_fingerprint_template=1\n");
		goto out;
	}

	if (db_import_export) {
		if (query(client_fd, client_session, BIO_INIT) ||
		    load_database(client_fd, client_session, argv[2]))
			goto out;
		result = store_database(client_fd, client_session, argv[3]) ? 1 : 0;
		printf("db_test_kind=import_export biometric_match_not_tested=1\n");
		goto out;
	}

	if (bio_pipeline || bio_prepare) {
		uint32_t remaining = UINT32_MAX;
		uint64_t challenge;
		int status;
		if (query(client_fd, client_session, BIO_INIT) ||
		    init_hw_auth(client_fd, client_session) ||
		    query(client_fd, client_session, BIO_SET_GID) ||
		    (bio_existing ? load_database(client_fd, client_session, argv[2]) :
				    query(client_fd, client_session, BIO_LOAD_EMPTY_DB)) ||
		    query(client_fd, client_session, BIO_SET_ACTIVE_GROUP))
			goto out;
		if (bio_existing && query(client_fd, client_session, BIO_LOAD_INSTANCE_DATA))
			goto out;
		if (bio_pipeline &&
		    (get_enrol_challenge(client_fd, client_session, &challenge) ||
		     authorize_enrol(client_fd, client_session, hat_fd, challenge_fd, challenge)))
			goto out;
		if (query(client_fd, client_session, BIO_BEGIN_ENROL))
			goto out;
		if (bio_prepare) {
			printf("bio_prepare=OK enrol_authorization=not_checked touch_not_collected template_not_created\n");
			result = 0;
			goto out;
		}
		for (unsigned int attempt = 1; attempt <= 40; attempt++) {
			if (operation_cancelled)
				goto out;
			printf("READY enrol attempt=%u: press same finger and hold\n", attempt);
			if (wait_finger_down(client_fd, client_session, power_fd)) {
				fprintf(stderr, "pipeline_incomplete=finger_down_wait\n");
				goto out;
			}
			status = query(client_fd, client_session, CAPTURE_ENROL);
			if (status < 0)
				goto out;
			if (!status) {
				status = query_output(client_fd, client_session, BIO_ENROL, &remaining);
				if (status < 0)
					goto out;
				if (!status) {
					/* The TA finalizes the enrol algorithm before its final
					 * authorization gate. Renew in this same session first;
					 * never reset BIO state or replay already collected input.
					 */
					printf("enrol_authorization_phase=end_enrol\n");
					if (get_enrol_challenge(client_fd, client_session, &challenge) ||
					    authorize_enrol(client_fd, client_session, hat_fd, challenge_fd, challenge)) {
						fprintf(stderr, "end_enrol_not_submitted=1 fresh_authorization_failed=1\n");
						goto out;
					}
					if (query(client_fd, client_session, BIO_END_ENROL))
						goto out;
					/* Fresh databases published as one FpPrint must contain
					 * exactly one template; do not turn a 1:N gallery into a
					 * single-finger desktop credential.
					 */
					if (!bio_existing) {
						uint32_t count;
						if (template_inventory(client_fd, client_session, &count) || count != 1) {
							fprintf(stderr, "enrol_output_rejected=single_template_database_required\n");
							goto out;
						}
					}
					result = store_database(client_fd, client_session, db_path) ? 1 : 0;
					goto out;
				}
				printf("enrol_progress_status=%d remaining=%u\n", status, remaining);
			} else {
				printf("capture_rejected=%d enrol_not_sent=1\n", status);
			}
			printf("READY lift finger and reposition for next sample\n");
			status = wait_finger_lost(client_fd, client_session, power_fd);
			if (status < 0) {
				fprintf(stderr, "pipeline_incomplete=finger_lost_wait\n");
				goto out;
			}
			if (status)
				printf("finger_lost_wait_retry_status=%d\n", status);
		}
		fprintf(stderr, "pipeline_incomplete=attempt_limit remaining=%u\n", remaining);
		goto out;
	}

	if (capture_once || capture_after_enter || capture_wait) {
		if (query(client_fd, client_session, BIO_INIT))
			goto out;
		if (capture_after_enter || capture_wait) {
			printf(capture_wait ? "READY send newline to arm finger IRQ capture\n" :
			       "READY press finger, then send newline to capture\n");
			if (getchar() == EOF) {
				fprintf(stderr, "capture cancelled: no trigger\n");
				goto out;
			}
		}
		if (capture_wait && wait_finger_down(client_fd, client_session, power_fd))
			goto out;
		result = query(client_fd, client_session,
			       capture_wait ? CAPTURE_ENROL : CAPTURE_ONCE) ? 1 : 0;
		goto out;
	}
	result = query(client_fd, client_session, BUILD_INFO) ? 1 : 0;
out:
	if (operation_cancelled)
		result = 1;
	if (sensor_initialized && query(client_fd, client_session, SENSOR_SLEEP))
		result = 1;
	if (client_fd >= 0)
		close(client_fd);
	if (loader_fd >= 0)
		close(loader_fd);
	if (power_fd >= 0)
		close(power_fd);
	return result;
}
