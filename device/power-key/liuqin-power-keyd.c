// SPDX-License-Identifier: MIT
/*
 * Android-like power-key policy for the liuqin GNOME image.
 *
 * This daemon deliberately does not EVIOCGRAB the device. Mutter must keep
 * seeing KEY_POWER so the same key can wake a blanked panel. logind and GNOME
 * Settings Daemon are configured to take no power action; this process only
 * classifies the duration and delegates a fixed, fail-closed desktop action.
 *
 * It also returns a background wake to sleep. A resume that no power-key
 * press follows was not asked for by the user; GNOME would keep the board
 * awake for its whole idle timeout again. While the window below lasts, the
 * helper's "resuspend" action suspends again as soon as the session is
 * locked, dark, idle and nothing inhibits sleep.
 */
#define _GNU_SOURCE

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/input.h>
#include <poll.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define POWER_KEY_NAME "pmic_pwrkey"
#define DEFAULT_ACTION_HELPER "/usr/local/libexec/liuqin-power-key-action"
#define LONG_PRESS_MS 1500
#define SHORT_PRESS_MIN_MS 40
#define RECONNECT_MS 1000
#define ACTION_TIMEOUT_MS 5000
/* GNOME blanks a locked session after 30 idle seconds; the first look comes
 * before that and the helper waits for the blank. */
#define RESUSPEND_FIRST_MS 20000
#define RESUSPEND_RETRY_MS 5000
/* After this the session's own idle timeout applies again. */
#define RESUSPEND_WINDOW_MS (10 * 60 * 1000)
/* CLOCK_BOOTTIME minus CLOCK_MONOTONIC grows only while suspended. */
#define RESUME_MIN_SLEEP_MS 1000
#define HELPER_NOT_YET 75
#define BITS_PER_LONG (sizeof(unsigned long) * 8U)
#define NBITS(x) (((x) + BITS_PER_LONG - 1U) / BITS_PER_LONG)

enum fsm_event {
	FSM_PRESS,
	FSM_REPEAT,
	FSM_RELEASE,
	FSM_SYNC_DROPPED,
	FSM_DISCONNECT,
	FSM_RECONNECT_UP,
	FSM_RECONNECT_DOWN,
	FSM_STOP,
};

struct power_fsm {
	bool pressed;
	bool long_fired;
	bool ignore_until_release;
	int64_t pressed_at_ms;
};

struct background_wake {
	bool armed;
	int64_t next_ms;
	int64_t deadline_ms;
};

static volatile sig_atomic_t stopping;

static int64_t monotonic_ms(void)
{
	struct timespec now;

	if (clock_gettime(CLOCK_MONOTONIC, &now) < 0)
		return -1;
	return (int64_t)now.tv_sec * 1000 + now.tv_nsec / 1000000;
}

/* Milliseconds spent suspended since boot. */
static int64_t suspended_ms(void)
{
	struct timespec boot, mono;

	if (clock_gettime(CLOCK_BOOTTIME, &boot) < 0 ||
	    clock_gettime(CLOCK_MONOTONIC, &mono) < 0)
		return -1;
	return ((int64_t)boot.tv_sec - mono.tv_sec) * 1000 +
	       ((int64_t)boot.tv_nsec - mono.tv_nsec) / 1000000;
}

static int64_t input_event_ms(const struct input_event *event, int64_t now_ms)
{
	int64_t event_ms;

	event_ms = (int64_t)event->time.tv_sec * 1000 +
		   event->time.tv_usec / 1000;
	/* EVIOCSCLOCKID should make this monotonic. Fall back safely if an old
	 * evdev implementation ignored it and supplied a different clock.
	 */
	if (event_ms <= 0 || event_ms > now_ms + 1000 || event_ms < now_ms - 60000)
		return now_ms;
	return event_ms;
}

static void handle_signal(int signal_number)
{
	(void)signal_number;
	stopping = 1;
}

static bool bit_set(const unsigned long *bits, unsigned int bit)
{
	return bits[bit / BITS_PER_LONG] & (1UL << (bit % BITS_PER_LONG));
}

static bool is_exact_power_key(int fd)
{
	unsigned long event_bits[NBITS(EV_MAX + 1)] = { 0 };
	unsigned long key_bits[NBITS(KEY_MAX + 1)] = { 0 };
	char name[128] = { 0 };

	if (ioctl(fd, EVIOCGNAME(sizeof(name)), name) < 0 ||
	    strcmp(name, POWER_KEY_NAME))
		return false;
	if (ioctl(fd, EVIOCGBIT(0, sizeof(event_bits)), event_bits) < 0 ||
	    !bit_set(event_bits, EV_KEY))
		return false;
	if (ioctl(fd, EVIOCGBIT(EV_KEY, sizeof(key_bits)), key_bits) < 0)
		return false;
	return bit_set(key_bits, KEY_POWER);
}

static int open_power_key(bool *currently_down)
{
	DIR *dir;
	struct dirent *entry;
	int result = -1;

	*currently_down = false;
	dir = opendir("/dev/input");
	if (!dir)
		return -1;

	while ((entry = readdir(dir)) != NULL) {
		unsigned long key_state[NBITS(KEY_MAX + 1)] = { 0 };
		char path[256];
		int clock_id = CLOCK_MONOTONIC;
		int fd;

		if (strncmp(entry->d_name, "event", 5))
			continue;
		if (snprintf(path, sizeof(path), "/dev/input/%s", entry->d_name) >=
		    (int)sizeof(path))
			continue;
		fd = open(path, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
		if (fd < 0)
			continue;
		if (!is_exact_power_key(fd)) {
			close(fd);
			continue;
		}

		/* Best effort: receipt time is still monotonic if this is refused. */
		(void)ioctl(fd, EVIOCSCLOCKID, &clock_id);
		if (ioctl(fd, EVIOCGKEY(sizeof(key_state)), key_state) == 0)
			*currently_down = bit_set(key_state, KEY_POWER);
		result = fd;
		fprintf(stderr, "liuqin-power-keyd: attached %s (%s)%s\n",
			path, POWER_KEY_NAME,
			*currently_down ? ", ignoring held key until release" : "");
		break;
	}
	closedir(dir);
	return result;
}

/* The helper's exit status, or -1 if it could not run to completion. */
static int run_helper(const char *helper, const char *action)
{
	int64_t deadline;
	pid_t pid;
	int status;

	pid = fork();
	if (pid < 0) {
		perror("liuqin-power-keyd: fork action helper");
		return -1;
	}
	if (pid == 0) {
		(void)setpgid(0, 0);
		execl(helper, helper, action, (char *)NULL);
		perror("liuqin-power-keyd: exec action helper");
		_exit(127);
	}
	(void)setpgid(pid, pid);

	deadline = monotonic_ms();
	if (deadline < 0)
		deadline = 0;
	deadline += ACTION_TIMEOUT_MS;
	for (;;) {
		pid_t waited = waitpid(pid, &status, WNOHANG);
		struct timespec pause = { .tv_nsec = 20 * 1000 * 1000 };

		if (waited == pid)
			break;
		if (waited < 0 && errno != EINTR) {
			perror("liuqin-power-keyd: wait action helper");
			return -1;
		}
		if (stopping || monotonic_ms() >= deadline) {
			(void)kill(-pid, SIGKILL);
			(void)kill(pid, SIGKILL);
			do {
				waited = waitpid(pid, &status, 0);
			} while (waited < 0 && errno == EINTR);
			fprintf(stderr, "liuqin-power-keyd: %s action killed (%s)\n",
				action, stopping ? "service stopping" : "timeout");
			return -1;
		}
		(void)nanosleep(&pause, NULL);
	}

	if (!WIFEXITED(status)) {
		fprintf(stderr, "liuqin-power-keyd: %s action failed status=%d\n",
			action, status);
		return -1;
	}
	return WEXITSTATUS(status);
}

static int run_action(const char *helper, const char *action)
{
	int status = run_helper(helper, action);

	if (status) {
		if (status > 0)
			fprintf(stderr, "liuqin-power-keyd: %s action failed status=%d\n",
				action, status);
		return -1;
	}
	fprintf(stderr, "liuqin-power-keyd: %s action complete\n", action);
	return 0;
}

static void background_resumed(struct background_wake *bg, int64_t now_ms)
{
	bg->armed = true;
	bg->next_ms = now_ms + RESUSPEND_FIRST_MS;
	bg->deadline_ms = now_ms + RESUSPEND_WINDOW_MS;
}

static void background_cancel(struct background_wake *bg, const char *why)
{
	if (!bg->armed)
		return;
	bg->armed = false;
	fprintf(stderr, "liuqin-power-keyd: background wake ended: %s\n", why);
}

static void background_advance(struct background_wake *bg, int64_t now_ms,
			       const char *helper)
{
	int status;

	if (!bg->armed || now_ms < bg->next_ms)
		return;
	status = run_helper(helper, "resuspend");
	if (status == HELPER_NOT_YET && now_ms + RESUSPEND_RETRY_MS <= bg->deadline_ms) {
		bg->next_ms = now_ms + RESUSPEND_RETRY_MS;
		return;
	}
	background_cancel(bg, status == 0 ? "suspend requested" :
			  status == HELPER_NOT_YET ? "session stayed visible or active" :
			  "session not eligible");
}

static int background_timeout_ms(const struct background_wake *bg, int64_t now_ms)
{
	if (!bg->armed)
		return RECONNECT_MS;
	if (bg->next_ms <= now_ms)
		return 0;
	if (bg->next_ms - now_ms > RECONNECT_MS)
		return RECONNECT_MS;
	return (int)(bg->next_ms - now_ms);
}

static void fsm_reset(struct power_fsm *fsm, bool ignore_until_release)
{
	fsm->pressed = false;
	fsm->long_fired = false;
	fsm->ignore_until_release = ignore_until_release;
	fsm->pressed_at_ms = 0;
}

static void fsm_advance(struct power_fsm *fsm, int64_t now_ms,
			const char *helper)
{
	if (!fsm->pressed || fsm->long_fired || fsm->ignore_until_release)
		return;
	if (now_ms - fsm->pressed_at_ms < LONG_PRESS_MS)
		return;

	/* Mark it first: a slow or failed desktop action must never become short. */
	fsm->long_fired = true;
	fprintf(stderr, "liuqin-power-keyd: long press >=%dms; requesting dialog\n",
		LONG_PRESS_MS);
	(void)run_action(helper, "long");
}

static bool fsm_handle(struct power_fsm *fsm, enum fsm_event event,
		       int64_t now_ms, const char *helper)
{
	int64_t held_ms;

	if (event != FSM_STOP)
		fsm_advance(fsm, now_ms, helper);

	switch (event) {
	case FSM_PRESS:
		if (fsm->ignore_until_release || fsm->pressed)
			break;
		fsm->pressed = true;
		fsm->long_fired = false;
		fsm->pressed_at_ms = now_ms;
		break;
	case FSM_REPEAT:
		/* Autorepeat and electrical duplicate presses never reset the timer. */
		break;
	case FSM_RELEASE:
		if (fsm->ignore_until_release) {
			fsm_reset(fsm, false);
			break;
		}
		if (!fsm->pressed)
			break;
		held_ms = now_ms - fsm->pressed_at_ms;
		if (fsm->long_fired) {
			fsm_reset(fsm, false);
			break;
		}
		fsm_reset(fsm, false);
		if (held_ms < SHORT_PRESS_MIN_MS) {
			fprintf(stderr, "liuqin-power-keyd: ignored %lldms bounce\n",
				(long long)held_ms);
			break;
		}
		fprintf(stderr, "liuqin-power-keyd: short press %lldms; locking\n",
			(long long)held_ms);
		(void)run_action(helper, "short");
		break;
	case FSM_SYNC_DROPPED:
	case FSM_RECONNECT_DOWN:
		fsm_reset(fsm, true);
		break;
	case FSM_DISCONNECT:
	case FSM_RECONNECT_UP:
		fsm_reset(fsm, false);
		break;
	case FSM_STOP:
		fsm_reset(fsm, false);
		return false;
	}
	return true;
}

static int poll_timeout_ms(const struct power_fsm *fsm, int64_t now_ms)
{
	int64_t remaining;

	if (!fsm->pressed || fsm->long_fired || fsm->ignore_until_release)
		return RECONNECT_MS;
	remaining = fsm->pressed_at_ms + LONG_PRESS_MS - now_ms;
	if (remaining <= 0)
		return 0;
	if (remaining > RECONNECT_MS)
		return RECONNECT_MS;
	return (int)remaining;
}

static int run_test_script(const char *path, const char *helper)
{
	struct power_fsm fsm = { 0 };
	struct background_wake bg = { 0 };
	char action[32];
	long long offset;
	long long previous = -1;
	FILE *stream;

	stream = fopen(path, "r");
	if (!stream) {
		perror("liuqin-power-keyd: open test script");
		return 1;
	}
	while (fscanf(stream, "%lld %31s", &offset, action) == 2) {
		enum fsm_event event;

		if (offset < previous || offset < 0) {
			fclose(stream);
			return 64;
		}
		previous = offset;
		if (!strcmp(action, "press"))
			event = FSM_PRESS;
		else if (!strcmp(action, "repeat"))
			event = FSM_REPEAT;
		else if (!strcmp(action, "release"))
			event = FSM_RELEASE;
		else if (!strcmp(action, "syn-dropped"))
			event = FSM_SYNC_DROPPED;
		else if (!strcmp(action, "disconnect"))
			event = FSM_DISCONNECT;
		else if (!strcmp(action, "reconnect-up"))
			event = FSM_RECONNECT_UP;
		else if (!strcmp(action, "reconnect-down"))
			event = FSM_RECONNECT_DOWN;
		else if (!strcmp(action, "signal"))
			event = FSM_STOP;
		else if (!strcmp(action, "resume")) {
			background_resumed(&bg, offset);
			continue;
		} else if (!strcmp(action, "tick")) {
			fsm_advance(&fsm, offset, helper);
			background_advance(&bg, offset, helper);
			continue;
		} else {
			fclose(stream);
			return 64;
		}
		if (event == FSM_PRESS)
			background_cancel(&bg, "power key");
		if (!fsm_handle(&fsm, event, offset, helper))
			break;
	}
	if (ferror(stream)) {
		fclose(stream);
		return 1;
	}
	fclose(stream);
	return 0;
}

static int run_daemon(const char *helper)
{
	struct power_fsm fsm = { 0 };
	struct background_wake bg = { 0 };
	int64_t asleep_ms = suspended_ms();
	int fd = -1;

	while (!stopping) {
		struct pollfd poll_fd;
		bool currently_down;
		int64_t now_ms, slept_ms;
		int rc, timeout;

		if (fd < 0) {
			fd = open_power_key(&currently_down);
			if (fd < 0) {
				struct timespec pause = { .tv_sec = 1 };

				(void)nanosleep(&pause, NULL);
				continue;
			}
			fsm_reset(&fsm, currently_down);
		}

		now_ms = monotonic_ms();
		if (now_ms < 0)
			return 1;
		poll_fd.fd = fd;
		poll_fd.events = POLLIN;
		poll_fd.revents = 0;
		timeout = poll_timeout_ms(&fsm, now_ms);
		if (background_timeout_ms(&bg, now_ms) < timeout)
			timeout = background_timeout_ms(&bg, now_ms);
		rc = poll(&poll_fd, 1, timeout);
		if (rc < 0) {
			if (errno == EINTR)
				continue;
			perror("liuqin-power-keyd: poll");
			return 1;
		}
		now_ms = monotonic_ms();
		if (now_ms < 0)
			return 1;
		/* Look for a resume before reading input: the wake key's press is
		 * delivered only after the resume, so it always cancels the window. */
		slept_ms = suspended_ms();
		if (slept_ms >= 0 && asleep_ms >= 0 &&
		    slept_ms - asleep_ms >= RESUME_MIN_SLEEP_MS) {
			fprintf(stderr, "liuqin-power-keyd: resumed after %lld ms asleep\n",
				(long long)(slept_ms - asleep_ms));
			background_resumed(&bg, now_ms);
		}
		if (slept_ms >= 0)
			asleep_ms = slept_ms;
		if (rc == 0) {
			fsm_advance(&fsm, now_ms, helper);
			background_advance(&bg, now_ms, helper);
			continue;
		}
		if (poll_fd.revents & (POLLERR | POLLHUP | POLLNVAL)) {
			fprintf(stderr, "liuqin-power-keyd: input device disappeared\n");
			close(fd);
			fd = -1;
			fsm_handle(&fsm, FSM_DISCONNECT, now_ms, helper);
			continue;
		}
		if (poll_fd.revents & POLLIN) {
			struct input_event input_event;
			ssize_t got;

			while ((got = read(fd, &input_event, sizeof(input_event))) ==
			       (ssize_t)sizeof(input_event)) {
				enum fsm_event event;
				int64_t event_ms;

				if (stopping)
					break;
				now_ms = monotonic_ms();
				if (now_ms < 0)
					return 1;
				event_ms = input_event_ms(&input_event, now_ms);

				if (input_event.type == EV_SYN &&
				    input_event.code == SYN_DROPPED) {
					fsm_handle(&fsm, FSM_SYNC_DROPPED,
						   event_ms, helper);
					continue;
				}
				if (input_event.type != EV_KEY ||
				    input_event.code != KEY_POWER)
					continue;
				if (input_event.value == 0)
					event = FSM_RELEASE;
				else if (input_event.value == 1) {
					event = FSM_PRESS;
					background_cancel(&bg, "power key");
				}
				else if (input_event.value == 2)
					event = FSM_REPEAT;
				else
					continue;
				fsm_handle(&fsm, event, event_ms, helper);
				if (stopping)
					break;
			}
			if (got == 0 || (got < 0 && errno != EAGAIN && errno != EINTR)) {
				fprintf(stderr, "liuqin-power-keyd: input read failed: %s\n",
					got == 0 ? "end of file" : strerror(errno));
				close(fd);
				fd = -1;
				fsm_handle(&fsm, FSM_DISCONNECT, now_ms, helper);
			}
		}
	}

	if (fd >= 0)
		close(fd);
	fsm_handle(&fsm, FSM_STOP, 0, helper);
	fprintf(stderr, "liuqin-power-keyd: stopped without a power action\n");
	return 0;
}

int main(int argc, char **argv)
{
	const char *action_helper = DEFAULT_ACTION_HELPER;
	const char *test_script = NULL;
	struct sigaction action = { 0 };
	int i;

	for (i = 1; i < argc; i++) {
		if (!strcmp(argv[i], "--action-helper") && i + 1 < argc)
			action_helper = argv[++i];
		else if (!strcmp(argv[i], "--test-script") && i + 1 < argc)
			test_script = argv[++i];
		else {
			fprintf(stderr,
				"usage: %s [--action-helper PATH] [--test-script PATH]\n",
				argv[0]);
			return 64;
		}
	}

	action.sa_handler = handle_signal;
	sigemptyset(&action.sa_mask);
	if (sigaction(SIGTERM, &action, NULL) < 0 ||
	    sigaction(SIGINT, &action, NULL) < 0) {
		perror("liuqin-power-keyd: sigaction");
		return 1;
	}
	if (test_script)
		return run_test_script(test_script, action_helper);
	return run_daemon(action_helper);
}
