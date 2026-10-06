// SPDX-License-Identifier: BSD-2-Clause
/* Authenticate the named Linux account, then forward a derived input on an FD.
 * This is an ordinary root executable, never setuid. Secrets are not logged.
 * Its private pam.d service uses pam_unix, avoiding fingerprint recursion.
 */
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <openssl/evp.h>
#include <poll.h>
#include <pwd.h>
#include <security/pam_appl.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <termios.h>
#include <unistd.h>

#define PASSWORD_LIMIT 4096
#define SALT_SIZE 32
#define INPUT_SIZE 32
#define KDF_ITERATIONS 600000
#define FPC_PAM_SERVICE "liuqin-fpc-enrol"

static volatile sig_atomic_t cancelled;
struct conversation_data {
	char password[PASSWORD_LIMIT];
	unsigned char derived[INPUT_SIZE];
	unsigned char desired[INPUT_SIZE];
	size_t length;
	int acquired;
};

static void cancel_input(int signal_number)
{
	(void)signal_number;
	cancelled = 1;
}

static int parse_fd(const char *text, int writing)
{
	char *end;
	errno = 0;
	long value = strtol(text, &end, 10);
	if (errno || end == text || *end || value < 3 || value > INT_MAX)
		return -1;
	int fd = (int)value, flags = fcntl(fd, F_GETFL);
	if (flags < 0 || isatty(fd) ||
	    (flags & O_ACCMODE) == (writing ? O_RDONLY : O_WRONLY))
		return -1;
	return fd;
}

static int read_password(struct conversation_data *data, const char *prompt)
{
	int tty = open("/dev/tty", O_RDWR | O_CLOEXEC | O_NOCTTY);
	struct termios previous, hidden;
	int result = -1, changed = 0;
	if (tty < 0 || tcgetattr(tty, &previous))
		goto out;
	hidden = previous;
	hidden.c_lflag &= ~(ECHO | ECHONL);
	if (tcsetattr(tty, TCSAFLUSH, &hidden))
		goto out;
	changed = 1;
	size_t prompt_size = strlen(prompt);
	if (write(tty, prompt, prompt_size) != (ssize_t)prompt_size)
		goto out;
	while (!cancelled) {
		struct pollfd input = { .fd = tty, .events = POLLIN };
		int ready = poll(&input, 1, 120000);
		if (ready < 0 && errno == EINTR)
			continue;
		if (ready <= 0 || !(input.revents & POLLIN))
			goto out;
		char value;
		ssize_t count = read(tty, &value, 1);
		if (count < 0 && errno == EINTR)
			continue;
		if (count != 1)
			goto out;
		if (value == '\n') {
			if (data->length) {
				data->acquired = 1;
				result = 0;
			}
			break;
		}
		if (!value || data->length == sizeof(data->password) - 1)
			goto out;
		data->password[data->length++] = value;
	}
out:
	if (changed) {
		if (tcsetattr(tty, TCSAFLUSH, &previous))
			result = -1;
		ssize_t written = write(tty, "\n", 1);
		(void)written;
	}
	if (tty >= 0)
		close(tty);
	return result;
}

static int converse(int count, const struct pam_message **messages,
		    struct pam_response **responses, void *context)
{
	struct conversation_data *data = context;
	struct pam_response *answer = NULL;
	if (count <= 0 || count > PAM_MAX_NUM_MSG || cancelled)
		return PAM_CONV_ERR;
	answer = calloc((size_t)count, sizeof(*answer));
	if (!answer)
		return PAM_BUF_ERR;
	for (int i = 0; i < count; i++) {
		switch (messages[i]->msg_style) {
		case PAM_PROMPT_ECHO_OFF:
			if ((!data->acquired && read_password(data, "Linux account password: ")) || cancelled)
				goto failure;
			answer[i].resp = strdup(data->password);
			if (!answer[i].resp)
				goto failure;
			break;
		case PAM_TEXT_INFO:
		case PAM_ERROR_MSG:
			/* PAM diagnostics are represented only by their return status. */
			break;
		default:
			goto failure;
		}
	}
	*responses = answer;
	return PAM_SUCCESS;
failure:
	for (int i = 0; i < count; i++)
		if (answer[i].resp) {
			explicit_bzero(answer[i].resp, strlen(answer[i].resp));
			free(answer[i].resp);
		}
	free(answer);
	return PAM_CONV_ERR;
}

static int private_service(char directory[PATH_MAX])
{
	struct stat info;
	ssize_t length = readlink("/proc/self/exe", directory, PATH_MAX - 1);
	if (length <= 0 || length == PATH_MAX - 1)
		return -1;
	directory[length] = 0;
	char *slash = strrchr(directory, '/');
	if (!slash || (size_t)(slash - directory) + sizeof("/pam.d/" FPC_PAM_SERVICE) > PATH_MAX)
		return -1;
	*slash = 0;
	if (lstat(directory, &info) || !S_ISDIR(info.st_mode) || info.st_uid || (info.st_mode & 0022))
		return -1;
	strcat(directory, "/pam.d");
	if (lstat(directory, &info) || !S_ISDIR(info.st_mode) || info.st_uid || (info.st_mode & 0022))
		return -1;
	char service[PATH_MAX];
	int count = snprintf(service, sizeof(service), "%s/%s", directory, FPC_PAM_SERVICE);
	if (count < 0 || (size_t)count >= sizeof(service) || lstat(service, &info) ||
	    !S_ISREG(info.st_mode) || info.st_uid || (info.st_mode & 0022))
		return -1;
	return 0;
}

int main(int argc, char **argv)
{
	struct conversation_data data = {};
	struct pam_conv conversation = { .conv = converse, .appdata_ptr = &data };
	pam_handle_t *pam = NULL;
	unsigned char salt[SALT_SIZE] = {};
	char configuration[PATH_MAX];
	int result = 1, status = PAM_SYSTEM_ERR, salt_fd = -1, output_fd = -1, current_fd = -1;
	int account_checked = 0;
	int sync_inputs = argc == 6 && !strcmp(argv[1], "--sync-inputs");
	const void *authenticated_user = NULL;
	struct rlimit core = {0, 0};
	struct passwd *account;
	if (geteuid() != 0 || (!sync_inputs && (argc != 5 || strcmp(argv[1], "--authenticate")))) {
		fprintf(stderr, "usage (root): pam-input --authenticate LINUX_USERNAME SALT_FD DERIVED_OUTPUT_FD | "
			"--sync-inputs LINUX_USERNAME SALT_FD CURRENT_INPUT_OUTPUT_FD DESIRED_INPUT_OUTPUT_FD\n");
		return 2;
	}
	account = getpwnam(argv[2]);
	salt_fd = parse_fd(argv[3], 0);
	output_fd = parse_fd(argv[sync_inputs ? 5 : 4], 1);
	if (sync_inputs)
		current_fd = parse_fd(argv[4], 1);
	if (!account || account->pw_uid == 0 || account->pw_uid >= 0x40000000u ||
	    salt_fd < 0 || output_fd < 0 || salt_fd == output_fd ||
	    (sync_inputs && (current_fd < 0 || current_fd == salt_fd || current_fd == output_fd)) ||
	    private_service(configuration)) {
		fprintf(stderr, "linux_user_input=FAILED phase=account_or_binary_fds_or_private_service\n");
		return 2;
	}
	uid_t expected_uid = account->pw_uid;
	if (setrlimit(RLIMIT_CORE, &core) || mlock(&data, sizeof(data))) {
		fprintf(stderr, "linux_user_input=FAILED phase=private_memory\n");
		return 1;
	}
	struct sigaction action = { .sa_handler = cancel_input };
	sigemptyset(&action.sa_mask);
	sigaction(SIGINT, &action, NULL);
	sigaction(SIGTERM, &action, NULL);
	sigaction(SIGHUP, &action, NULL);
	sigaction(SIGQUIT, &action, NULL);
	signal(SIGPIPE, SIG_IGN);
	struct stat salt_info;
	if (fstat(salt_fd, &salt_info) || !S_ISREG(salt_info.st_mode) ||
	    salt_info.st_size != SALT_SIZE || lseek(salt_fd, 0, SEEK_SET) < 0 ||
	    read(salt_fd, salt, sizeof(salt)) != (ssize_t)sizeof(salt))
		goto out;
	status = pam_start_confdir(FPC_PAM_SERVICE, argv[2], &conversation, configuration, &pam);
	if (status != PAM_SUCCESS) {
		pam = NULL;
		goto out;
	}
	status = pam_authenticate(pam, PAM_DISALLOW_NULL_AUTHTOK);
	if (status == PAM_SUCCESS && !cancelled)
		status = pam_acct_mgmt(pam, PAM_DISALLOW_NULL_AUTHTOK);
	if (status == PAM_SUCCESS)
		status = pam_get_item(pam, PAM_USER, &authenticated_user);
	if (status != PAM_SUCCESS || cancelled || !data.acquired || !authenticated_user ||
	    strcmp(authenticated_user, argv[2]))
		goto out;
	account = getpwnam(authenticated_user);
	if (!account || account->pw_uid != expected_uid)
		goto out;
	account_checked = 1;
	/* Schema 1: PBKDF2-HMAC-SHA256, 600000 iterations, 32-byte random salt.
	 * The public parameters are persisted by the caller; the result is not.
	 */
	if (PKCS5_PBKDF2_HMAC(data.password, (int)data.length, salt, sizeof(salt),
			      KDF_ITERATIONS, EVP_sha256(), sizeof(data.derived), data.derived) != 1 || cancelled)
		goto out;
	if (sync_inputs) {
		/* PAM authenticated the desired current Linux password. Gatekeeper
		 * validates the previous input against the existing handle; it is
		 * intentionally not authenticated against the changed Linux password.
		 * Keep both results in locked memory and publish only after both prompts.
		 */
		memcpy(data.desired, data.derived, sizeof(data.desired));
		explicit_bzero(data.password, sizeof(data.password));
		data.length = 0;
		data.acquired = 0;
		if (read_password(&data, "Previous fingerprint authorization password: ") || cancelled ||
		    PKCS5_PBKDF2_HMAC(data.password, (int)data.length, salt, sizeof(salt),
				 KDF_ITERATIONS, EVP_sha256(), sizeof(data.derived), data.derived) != 1)
			goto out;
	}
	int outputs[2] = {sync_inputs ? current_fd : output_fd, output_fd};
	const unsigned char *inputs[2] = {data.derived, data.desired};
	for (unsigned int input = 0; input < (sync_inputs ? 2u : 1u); input++) {
		for (size_t offset = 0; offset < INPUT_SIZE;) {
			if (cancelled)
				goto out;
			ssize_t count = write(outputs[input], inputs[input] + offset, INPUT_SIZE - offset);
			if (count < 0 && errno == EINTR && !cancelled)
				continue;
			if (count <= 0 || cancelled)
				goto out;
			offset += (size_t)count;
		}
	}
	result = 0;
out:
	if (pam)
		pam_end(pam, status);
	explicit_bzero(&data, sizeof(data));
	munlock(&data, sizeof(data));
	fprintf(stderr, "linux_user_input=%s pam_status=%d account_checked=%d derived_input_forwarded=%d "
		"credential_sync=%d previous_input_validation=TA_owned secret_logged=0\n",
		result ? "FAILED" : "OK", status, account_checked, result == 0, sync_inputs);
	return result;
}
