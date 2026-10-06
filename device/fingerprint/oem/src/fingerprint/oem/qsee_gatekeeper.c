// SPDX-License-Identifier: BSD-2-Clause
/* Qualcomm Gatekeeper enrollment and verification, using OEM CBOR and QCBOR.
 * New credentials use a separate native Linux UID range and are created by
 * the TA. Binary FDs carry opaque handles, credential input and signed HATs.
 * No credential or token is printed, and no token is signed in normal world.
 */
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <linux/tee.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>
#include <qcbor/qcbor_encode.h>
#include <qcbor/qcbor_spiffy_decode.h>

#define BUFFER_SIZE 0xa000u
#define CREDENTIAL_LIMIT 1024u
#define OEM_HANDLE_SIZE 58u
#define HAT_SIZE 69u
#define NATIVE_UID_MIN 0x40000000u
#define NATIVE_UID_MAX 0x7fffffffu

struct shared {
	void *address;
	size_t size;
	int id;
};

struct keymaster {
	int fd;
	uint32_t session;
	struct shared request, response;
};

/* Same ordinary TEE attach and two-memref transport as fpc_build_info v8. */
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
	memory->size = allocation.size;
	memory->id = allocation.id;
	memset(memory->address, 0, memory->size);
	return 0;
}

static void clear_shared(struct shared *memory)
{
	if (memory->address) {
		explicit_bzero(memory->address, memory->size);
		munmap(memory->address, memory->size);
		memory->address = NULL;
	}
}

static int open_keymaster(struct keymaster *km)
{
	struct tee_ioctl_version_data version;
	char path[32];
	for (int i = 0; i < 8; i++) {
		snprintf(path, sizeof(path), "/dev/tee%d", i);
		km->fd = open(path, O_RDWR | O_CLOEXEC);
		if (km->fd < 0)
			continue;
		if (!ioctl(km->fd, TEE_IOC_VERSION, &version) && version.impl_id == 5)
			break;
		close(km->fd);
		km->fd = -1;
	}
	if (km->fd < 0) {
		errno = ENODEV;
		return -1;
	}
	uint64_t storage[(sizeof(struct tee_ioctl_open_session_arg) +
			  sizeof(struct tee_ioctl_param) + 7) / 8] = {};
	struct tee_ioctl_open_session_arg *session = (void *)storage;
	struct tee_ioctl_buf_data data = {
		.buf_ptr = (uintptr_t)storage, .buf_len = sizeof(storage),
	};
	struct shared name = {};
	if (allocate_shared(km->fd, 64, &name))
		return -1;
	memcpy(name.address, "keymaster64", sizeof("keymaster64"));
	session->num_params = 1;
	session->params[0].attr = TEE_IOCTL_PARAM_ATTR_TYPE_MEMREF_INPUT;
	session->params[0].b = sizeof("keymaster64");
	session->params[0].c = name.id;
	int result = ioctl(km->fd, TEE_IOC_OPEN_SESSION, &data);
	clear_shared(&name);
	if (result || session->ret) {
		if (!result)
			errno = EPROTO;
		return -1;
	}
	km->session = session->session;
	return allocate_shared(km->fd, BUFFER_SIZE, &km->request) ||
	       allocate_shared(km->fd, BUFFER_SIZE, &km->response) ? -1 : 0;
}

static int invoke_keymaster(struct keymaster *km, size_t request_size)
{
	uint64_t storage[(sizeof(struct tee_ioctl_invoke_arg) +
			  2 * sizeof(struct tee_ioctl_param) + 7) / 8] = {};
	struct tee_ioctl_invoke_arg *invoke = (void *)storage;
	struct tee_ioctl_buf_data data = {
		.buf_ptr = (uintptr_t)storage, .buf_len = sizeof(storage),
	};
	size_t response_size = BUFFER_SIZE - ((request_size + 3) & ~(size_t)3);
	explicit_bzero(km->response.address, km->response.size);
	invoke->session = km->session;
	invoke->num_params = 2;
	invoke->params[0].attr = TEE_IOCTL_PARAM_ATTR_TYPE_MEMREF_INPUT;
	invoke->params[0].b = request_size;
	invoke->params[0].c = km->request.id;
	invoke->params[1].attr = TEE_IOCTL_PARAM_ATTR_TYPE_MEMREF_OUTPUT;
	invoke->params[1].b = response_size;
	invoke->params[1].c = km->response.id;
	if (ioctl(km->fd, TEE_IOC_INVOKE, &data)) {
		perror("keymaster_invoke");
		return -1;
	}
	if (invoke->ret) {
		fprintf(stderr, "keymaster_tee_error=%u origin=%u\n",
			invoke->ret, invoke->ret_origin);
		return -1;
	}
	int32_t status;
	memcpy(&status, km->response.address, sizeof(status));
	fprintf(stderr, "keymaster_command=0x%x app_status=%d\n",
		((uint32_t *)km->request.address)[0], status);
	return status ? -1 : 0;
}

static int negotiate_keymaster(struct keymaster *km)
{
	uint32_t *request = km->request.address;
	request[0] = 0x200;
	if (invoke_keymaster(km, 4))
		return -1;
	uint32_t *response = km->response.address;
	fprintf(stderr, "keymaster_api=%u.%u ta_version=%u.%u\n",
		response[1], response[2], response[3], response[4]);
	if (response[1] != 4 || response[2] != 1 || response[3] != 4 || response[4] < 540) {
		fprintf(stderr, "keymaster_api_uncovered=1\n");
		return -1;
	}
	const uint32_t words[] = { 0x207, 4, 5, 4, 5, 0 };
	explicit_bzero(km->request.address, km->request.size);
	memcpy(km->request.address, words, sizeof(words));
	return invoke_keymaster(km, sizeof(words));
}

/* Normal OEM HMAC-sharing API:220e returns CBOR41/42(seed,nonce), then
 * 220f accepts CBOR43(seed||nonce) and returns CBOR44(sharing check).
 * Only this instance participates. No credential, keyblob, HAT, provisioning
 * or persistent table command is submitted; all returned bytes stay opaque.
 * A successful compute updates the TA's volatile HMAC cache at52488/524e8.
 * Its secure-object route may bypass legacy type7 qsee_kdf, so success cannot
 * be reported as Gatekeeper type3/table readiness.
 */
static int shared_hmac_probe(struct keymaster *km)
{
	uint8_t parameters[64] = {};
	QCBORDecodeContext decoder;
	QCBOREncodeContext encoder;
	UsefulBufC seed = NULLUsefulBufC, nonce = NULLUsefulBufC;
	UsefulBufC check = NULLUsefulBufC, encoded;
	uint32_t length;
	size_t request_size = 4;
	int result = -1;

	explicit_bzero(km->request.address, km->request.size);
	((uint32_t *)km->request.address)[0] = 0x220e;
	if (invoke_keymaster(km, request_size))
		goto out;
	memcpy(&length, (uint8_t *)km->response.address + 4, sizeof(length));
	if (!length || length > BUFFER_SIZE - request_size - 8) {
		fprintf(stderr, "shared_hmac_parameters_length_invalid=1\n");
		goto out;
	}
	QCBORDecode_Init(&decoder, (UsefulBufC){ (uint8_t *)km->response.address + 8, length },
			 QCBOR_DECODE_MODE_NORMAL);
	QCBORDecode_EnterMap(&decoder, NULL);
	QCBORDecode_GetByteStringInMapN(&decoder, 41, &seed);
	QCBORDecode_GetByteStringInMapN(&decoder, 42, &nonce);
	QCBORDecode_ExitMap(&decoder);
	if (QCBORDecode_Finish(&decoder) || seed.len != 32 || nonce.len != 32) {
		fprintf(stderr, "shared_hmac_parameters_uncovered=1\n");
		goto out;
	}
	memcpy(parameters, seed.ptr, 32);
	memcpy(parameters + 32, nonce.ptr, 32);
	fprintf(stderr, "shared_hmac_parameters_received=1 bytes=64 payload_logged=0\n");

	explicit_bzero(km->request.address, km->request.size);
	((uint32_t *)km->request.address)[0] = 0x220f;
	QCBOREncode_Init(&encoder, (UsefulBuf){ (uint8_t *)km->request.address + 4, BUFFER_SIZE - 4 });
	QCBOREncode_OpenMap(&encoder);
	QCBOREncode_AddBytesToMapN(&encoder, 43, (UsefulBufC){ parameters, sizeof(parameters) });
	QCBOREncode_CloseMap(&encoder);
	if (QCBOREncode_Finish(&encoder, &encoded)) {
		fprintf(stderr, "shared_hmac_encode_failed=1\n");
		goto out;
	}
	request_size = encoded.len + 4;
	if (invoke_keymaster(km, request_size)) {
		fprintf(stderr, "shared_hmac_compute_rejected=1 gatekeeper_not_submitted=1\n");
		goto out;
	}
	memcpy(&length, (uint8_t *)km->response.address + 4, sizeof(length));
	if (!length || length > BUFFER_SIZE - ((request_size + 3) & ~(size_t)3) - 8) {
		fprintf(stderr, "shared_hmac_result_length_invalid=1\n");
		goto out;
	}
	QCBORDecode_Init(&decoder, (UsefulBufC){ (uint8_t *)km->response.address + 8, length },
			 QCBOR_DECODE_MODE_NORMAL);
	QCBORDecode_EnterMap(&decoder, NULL);
	QCBORDecode_GetByteStringInMapN(&decoder, 44, &check);
	QCBORDecode_ExitMap(&decoder);
	if (QCBORDecode_Finish(&decoder) || check.len != 32) {
		fprintf(stderr, "shared_hmac_result_invalid=1\n");
		goto out;
	}
	fprintf(stderr, "shared_hmac_complete=1 check_bytes=32 payload_logged=0 "
		"volatile_hmac_cache_updated=1 legacy_kdf_route=not_observed gatekeeper_table=not_checked\n");
	result = 0;
out:
	explicit_bzero(parameters, sizeof(parameters));
	explicit_bzero(km->request.address, km->request.size);
	explicit_bzero(km->response.address, km->response.size);
	return result;
}

static int parse_fd(const char *text, int writing)
{
	char *end;
	errno = 0;
	long value = strtol(text, &end, 10);
	if (errno || end == text || *end || value < 0 || value > INT_MAX)
		return -1;
	int fd = (int)value;
	int flags = fcntl(fd, F_GETFL);
	if (flags < 0 || isatty(fd) ||
	    (flags & O_ACCMODE) == (writing ? O_RDONLY : O_WRONLY))
		return -1;
	return fd;
}

static int read_binary(int fd, uint8_t *buffer, size_t capacity,
		       size_t *size, int require_eof)
{
	*size = 0;
	for (;;) {
		struct pollfd input = { .fd = fd, .events = POLLIN };
		int ready = poll(&input, 1, 30000);
		if (ready < 0 && errno == EINTR)
			continue;
		if (ready <= 0 || !(input.revents & (POLLIN | POLLHUP)))
			return -1;
		uint8_t extra;
		ssize_t count = read(fd, *size < capacity ? buffer + *size : &extra,
				     *size < capacity ? capacity - *size : 1);
		if (count < 0 && errno == EINTR)
			continue;
		if (count < 0 || (count > 0 && *size == capacity))
			return -1;
		if (!count)
			return *size ? 0 : -1;
		*size += (size_t)count;
		if (!require_eof && *size == capacity)
			return 0;
	}
}

/* The second enrol challenge arrives after capture. The owning runtime bounds
 * enrolment and cancels this process; a 30-second idle timeout would expire
 * before normal multi-touch capture can finish. EOF still ends the operation.
 */
static int read_enrol_finish_challenge(int fd, uint64_t *challenge)
{
	for (;;) {
		struct pollfd input = { .fd = fd, .events = POLLIN };
		int ready = poll(&input, 1, 30000);
		if (!ready || (ready < 0 && errno == EINTR))
			continue;
		if (ready < 0 || !(input.revents & (POLLIN | POLLHUP)))
			return -1;
		break;
	}
	size_t size;
	*challenge = 0;
	if (read_binary(fd, (uint8_t *)challenge, sizeof(*challenge), &size, 0) ||
	    size != sizeof(*challenge) || !*challenge)
		return -1;
	return 0;
}

static int write_binary(int fd, const uint8_t *buffer, size_t size)
{
	for (size_t offset = 0; offset < size;) {
		ssize_t count = write(fd, buffer + offset, size - offset);
		if (count < 0 && errno == EINTR)
			continue;
		if (count <= 0) {
			perror("write_gatekeeper_binary_output");
			return -1;
		}
		offset += (size_t)count;
	}
	return 0;
}

static int new_credential(struct keymaster *km, uint32_t uid,
			  UsefulBufC password, UsefulBufC current_handle,
			  UsefulBufC current_input, uint8_t *handle, size_t *handle_size)
{
	QCBOREncodeContext encoder;
	UsefulBufC encoded;
	uint32_t *request = km->request.address;
	explicit_bzero(request, km->request.size);
	request[0] = 0x21001;
	QCBOREncode_Init(&encoder, (UsefulBuf){ (uint8_t *)request + 4, BUFFER_SIZE - 4 });
	QCBOREncode_OpenMap(&encoder);
	QCBOREncode_AddUInt64ToMapN(&encoder, 3, uid);
	/* Both current fields must be present for an explicit update. The
	 * active-slot candidate's decoder 0x2211c and handler 0x4ae0..0x4cc0
	 * use labels 5/7 to verify the old handle and retrieve the existing SID.
	 * Never fall back from a rejected update to the new-identity path.
	 */
	if (current_handle.len) {
		QCBOREncode_AddBytesToMapN(&encoder, 5, current_handle);
		QCBOREncode_AddBytesToMapN(&encoder, 7, current_input);
	}
	QCBOREncode_AddBytesToMapN(&encoder, 8, password);
	QCBOREncode_CloseMap(&encoder);
	if (QCBOREncode_Finish(&encoder, &encoded)) {
		fprintf(stderr, "gatekeeper_enroll_encode_failed=1\n");
		return -1;
	}
	size_t request_size = encoded.len + 4;
	if (invoke_keymaster(km, request_size))
		return -1;
	uint32_t length;
	memcpy(&length, (uint8_t *)km->response.address + 4, sizeof(length));
	if (!length || length > BUFFER_SIZE - ((request_size + 3) & ~(size_t)3) - 8) {
		fprintf(stderr, "gatekeeper_enroll_response_length_invalid=1\n");
		return -1;
	}
	QCBORDecodeContext decoder;
	UsefulBufC returned_handle = NULLUsefulBufC;
	QCBORDecode_Init(&decoder, (UsefulBufC){ (uint8_t *)km->response.address + 8, length },
			 QCBOR_DECODE_MODE_NORMAL);
	QCBORDecode_EnterMap(&decoder, NULL);
	QCBORDecode_GetByteStringInMapN(&decoder, 6, &returned_handle);
	QCBORDecode_ExitMap(&decoder);
	if (QCBORDecode_Finish(&decoder) || !returned_handle.len ||
	    returned_handle.len > CREDENTIAL_LIMIT) {
		fprintf(stderr, "gatekeeper_enroll_response_invalid_or_rejected=1\n");
		return -1;
	}
	memcpy(handle, returned_handle.ptr, returned_handle.len);
	*handle_size = returned_handle.len;
	return 0;
}

static int verify_credential(struct keymaster *km, uint32_t uid,
			     uint64_t challenge, UsefulBufC handle,
			     UsefulBufC password, uint8_t hat[HAT_SIZE])
{
	QCBOREncodeContext encoder;
	UsefulBufC encoded;
	uint32_t *request = km->request.address;
	explicit_bzero(request, km->request.size);
	request[0] = 0x21002;
	QCBOREncode_Init(&encoder, (UsefulBuf){ (uint8_t *)request + 4, BUFFER_SIZE - 4 });
	QCBOREncode_OpenMap(&encoder);
	QCBOREncode_AddUInt64ToMapN(&encoder, 3, uid);
	QCBOREncode_AddUInt64ToMapN(&encoder, 4, challenge);
	QCBOREncode_AddBytesToMapN(&encoder, 6, handle);
	QCBOREncode_AddBytesToMapN(&encoder, 9, password);
	QCBOREncode_CloseMap(&encoder);
	if (QCBOREncode_Finish(&encoder, &encoded)) {
		fprintf(stderr, "gatekeeper_encode_failed=1\n");
		return -1;
	}
	size_t request_size = encoded.len + 4;
	if (invoke_keymaster(km, request_size))
		return -1;
	uint32_t length;
	memcpy(&length, (uint8_t *)km->response.address + 4, sizeof(length));
	if (!length || length > BUFFER_SIZE - ((request_size + 3) & ~(size_t)3) - 8) {
		fprintf(stderr, "gatekeeper_response_length_invalid=1\n");
		return -1;
	}
	QCBORDecodeContext decoder;
	uint64_t returned_challenge = 0, sid = 0, auth_id = 0, type = 0, timestamp = 0;
	uint64_t version = UINT64_MAX;
	UsefulBufC mac = NULLUsefulBufC;
	QCBORDecode_Init(&decoder, (UsefulBufC){ (uint8_t *)km->response.address + 8, length },
			 QCBOR_DECODE_MODE_NORMAL);
	QCBORDecode_EnterMap(&decoder, NULL);
	/* OEM label 11 is the HAT version, not the application status word. */
	QCBORDecode_GetUInt64InMapN(&decoder, 11, &version);
	QCBORDecode_GetUInt64InMapN(&decoder, 4, &returned_challenge);
	QCBORDecode_GetUInt64InMapN(&decoder, 12, &sid);
	QCBORDecode_GetUInt64InMapN(&decoder, 13, &auth_id);
	QCBORDecode_GetUInt64InMapN(&decoder, 14, &type);
	QCBORDecode_GetUInt64InMapN(&decoder, 15, &timestamp);
	QCBORDecode_GetByteStringInMapN(&decoder, 16, &mac);
	QCBORDecode_ExitMap(&decoder);
	if (QCBORDecode_Finish(&decoder) || version || returned_challenge != challenge ||
	    type > UINT32_MAX || mac.len != 32) {
		fprintf(stderr, "gatekeeper_response_invalid_or_rejected=1\n");
		return -1;
	}
	/* OEM deserializer copies the integer storage, including type/timestamp.
	 * No extra byte swap is applied to these already encoded HAT fields.
	 */
	uint32_t auth_type = (uint32_t)type;
	hat[0] = (uint8_t)version;
	memcpy(hat + 1, &returned_challenge, 8);
	memcpy(hat + 9, &sid, 8);
	memcpy(hat + 17, &auth_id, 8);
	memcpy(hat + 25, &auth_type, 4);
	memcpy(hat + 29, &timestamp, 8);
	memcpy(hat + 37, mac.ptr, 32);
	return 0;
}

int main(int argc, char **argv)
{
	struct keymaster km = { .fd = -1 };
	uint8_t handle[CREDENTIAL_LIMIT] = {}, password[CREDENTIAL_LIMIT] = {};
	uint8_t previous_input[CREDENTIAL_LIMIT] = {};
	uint8_t hat[HAT_SIZE] = {};
	uint8_t previous_sid[8] = {};
	uint64_t challenge = 0;
	size_t handle_size, password_size, challenge_size, previous_size = 0;
	int result = 1;
	int verify_enrol = argc == 7 && !strcmp(argv[1], "--verify-enrol");
	int verify = verify_enrol || (argc == 7 && !strcmp(argv[1], "--verify"));
	int enroll = argc == 5 && !strcmp(argv[1], "--new-credential");
	int change = argc == 7 && !strcmp(argv[1], "--change-credential");
	int negotiate = argc == 2 && !strcmp(argv[1], "--negotiate");
	int hmac_probe = argc == 2 && !strcmp(argv[1], "--shared-hmac-probe");
	int challenge_fd = -1, handle_fd = -1, password_fd = -1, hat_fd = -1;
	int previous_fd = -1, output_fd = -1;
	uint32_t uid = 0;
	if (!verify && !enroll && !change && !negotiate && !hmac_probe) {
		fprintf(stderr, "usage: %s --negotiate | --shared-hmac-probe | --new-credential NATIVE_UID DERIVED_INPUT_FD HANDLE_OUTPUT_FD | --change-credential NATIVE_UID HANDLE_FD CURRENT_INPUT_FD DESIRED_INPUT_FD HANDLE_OUTPUT_FD | --verify UID CHALLENGE_FD HANDLE_FD DERIVED_INPUT_FD HAT_FD | --verify-enrol UID CHALLENGE_FD HANDLE_FD DERIVED_INPUT_FD HAT_FD\n",
			argv[0]);
		return 2;
	}
	if (verify || enroll || change) {
		char *end;
		errno = 0;
		unsigned long long value = strtoull(argv[2], &end, 10);
		if (errno || end == argv[2] || *end || argv[2][0] == '-' || value > UINT32_MAX)
			return 2;
		uid = (uint32_t)value;
	}
	if (enroll || change) {
		if (uid < NATIVE_UID_MIN || uid > NATIVE_UID_MAX) {
			fprintf(stderr, "gatekeeper_new_credential_requires_native_uid=1\n");
			return 2;
		}
		password_fd = parse_fd(argv[change ? 5 : 3], 0);
		output_fd = parse_fd(argv[change ? 6 : 4], 1);
		if (password_fd < 3 || output_fd < 3 || password_fd == output_fd) {
			fprintf(stderr, "gatekeeper_requires_distinct_binary_fds_above_stderr=1\n");
			return 2;
		}
		if (read_binary(password_fd, password, sizeof(password), &password_size, 1)) {
			fprintf(stderr, "gatekeeper_input_incomplete=1\n");
			goto out;
		}
		if (change) {
			handle_fd = parse_fd(argv[3], 0);
			previous_fd = parse_fd(argv[4], 0);
			if (handle_fd < 3 || previous_fd < 3 || handle_fd == previous_fd ||
			    handle_fd == password_fd || handle_fd == output_fd ||
			    previous_fd == password_fd || previous_fd == output_fd) {
				fprintf(stderr, "gatekeeper_update_requires_distinct_binary_fds=1\n");
				goto out;
			}
			if (read_binary(handle_fd, handle, sizeof(handle), &handle_size, 1) ||
			    handle_size != OEM_HANDLE_SIZE ||
			    read_binary(previous_fd, previous_input, sizeof(previous_input), &previous_size, 1)) {
				fprintf(stderr, "gatekeeper_update_input_incomplete_or_handle_format_uncovered=1 new_credential_not_requested=1\n");
				goto out;
			}
			/* The covered OEM handle is the packed 58-byte Android
			 * password_handle_t: version at 0, secure_id at 1..8.
			 * Compare internally; never emit the identity or handle bytes.
			 */
			if (handle[0] > 2) {
				fprintf(stderr, "gatekeeper_update_handle_version_uncovered=1\n");
				goto out;
			}
			memcpy(previous_sid, handle + 1, sizeof(previous_sid));
		}
	}
	if (verify) {
		challenge_fd = parse_fd(argv[3], 0);
		handle_fd = parse_fd(argv[4], 0);
		password_fd = parse_fd(argv[5], 0);
		hat_fd = parse_fd(argv[6], 1);
		if (challenge_fd < 3 || handle_fd < 3 || password_fd < 3 || hat_fd < 3 ||
		    challenge_fd == handle_fd || challenge_fd == password_fd ||
		    handle_fd == password_fd || hat_fd == challenge_fd ||
		    hat_fd == handle_fd || hat_fd == password_fd) {
			fprintf(stderr, "gatekeeper_requires_distinct_binary_fds=1\n");
			return 2;
		}
		if (read_binary(handle_fd, handle, sizeof(handle), &handle_size, 1) ||
		    read_binary(password_fd, password, sizeof(password), &password_size, 1) ||
		    read_binary(challenge_fd, (uint8_t *)&challenge, sizeof(challenge), &challenge_size, 0) ||
		    challenge_size != sizeof(challenge) || !challenge) {
			fprintf(stderr, "gatekeeper_input_incomplete=1\n");
			goto out;
		}
	}
	signal(SIGPIPE, SIG_IGN);
	if (open_keymaster(&km)) {
		perror("attach_keymaster64");
		goto out;
	}
	if (negotiate_keymaster(&km))
		goto out;
	if (hmac_probe) {
		if (shared_hmac_probe(&km))
			goto out;
	} else if (enroll || change) {
		if (new_credential(&km, uid, (UsefulBufC){password, password_size},
				   change ? (UsefulBufC){handle, handle_size} : NULLUsefulBufC,
				   change ? (UsefulBufC){previous_input, previous_size} : NULLUsefulBufC,
				   handle, &handle_size) ||
		    write_binary(output_fd, handle, handle_size))
			goto out;
		if (change && (handle_size != OEM_HANDLE_SIZE || handle[0] > 2 ||
		    memcmp(previous_sid, handle + 1, sizeof(previous_sid)))) {
			/* The output FD retains the uncertain TA result for recovery;
			 * caller must keep the old installed handle and report failure. */
			fprintf(stderr, "gatekeeper_update_sid_preserved=0 previous_handle_file_must_be_preserved=1\n");
			goto out;
		}
		if (change)
			fprintf(stderr, "gatekeeper_update_sid_preserved=1\n");
		fprintf(stderr, "%s=1 handle_forwarded=1\n", change ?
			"gatekeeper_existing_credential_updated" : "gatekeeper_native_credential_created");
	} else if (verify) {
		/* Read the credential only once and keep this Keymaster session open.
		 * Enrolment needs one signed challenge before capture and a fresh one
		 * before END_ENROL; this never requests a new credential or SID.
		 */
		unsigned int rounds = verify_enrol ? 2 : 1;
		for (unsigned int round = 1; round <= rounds; round++) {
			if (round > 1 && read_enrol_finish_challenge(challenge_fd, &challenge)) {
				fprintf(stderr, "gatekeeper_finish_challenge_incomplete=1\n");
				goto out;
			}
			if (verify_credential(&km, uid, challenge,
					      (UsefulBufC){handle, handle_size},
					      (UsefulBufC){password, password_size}, hat) ||
			    write_binary(hat_fd, hat, sizeof(hat)))
				goto out;
			explicit_bzero(hat, sizeof(hat));
			explicit_bzero(km.request.address, km.request.size);
			explicit_bzero(km.response.address, km.response.size);
			fprintf(stderr, "gatekeeper_verified_hat_forwarded=1 round=%u rounds=%u bytes=%u fpc_acceptance=not_checked\n",
				round, rounds, HAT_SIZE);
		}
	} else {
		fprintf(stderr, "keymaster_negotiation=OK credential_not_submitted=1\n");
	}
	result = 0;
out:
	explicit_bzero(handle, sizeof(handle));
	explicit_bzero(password, sizeof(password));
	explicit_bzero(previous_input, sizeof(previous_input));
	explicit_bzero(hat, sizeof(hat));
	explicit_bzero(previous_sid, sizeof(previous_sid));
	clear_shared(&km.request);
	clear_shared(&km.response);
	if (km.fd >= 0)
		close(km.fd);
	return result;
}
