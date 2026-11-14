// SPDX-License-Identifier: MIT
/*
 * Classify one fresh PMIC KEY_POWER press/release sequence for charger mode.
 *
 * Exit 0: ordinary press, request normal boot.
 * Exit 10: hold for two seconds, request bootloader without waiting for release.
 * Exit 2: bounded wait elapsed or a bounce was ignored.
 */
#define _GNU_SOURCE

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/input.h>
#include <poll.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <time.h>
#include <unistd.h>

#define MAX_EVENTS 32
#define BITS_PER_LONG (sizeof(unsigned long) * 8U)
#define NBITS(x) (((x) + BITS_PER_LONG - 1U) / BITS_PER_LONG)
#define ARM_AFTER_MS 3000
#define NORMAL_MIN_MS 50
#define NORMAL_MAX_MS 1999
#define BOOTLOADER_MIN_MS 2000

static int held_action(bool pressed, int64_t pressed_at, int64_t now)
{
	return pressed && now - pressed_at >= BOOTLOADER_MIN_MS ? 10 : -1;
}

static int64_t monotonic_ms(void)
{
	struct timespec now;

	if (clock_gettime(CLOCK_MONOTONIC, &now) < 0)
		return -1;
	return (int64_t)now.tv_sec * 1000 + now.tv_nsec / 1000000;
}

static bool bit_set(const unsigned long *bits, unsigned int bit)
{
	return bits[bit / BITS_PER_LONG] & (1UL << (bit % BITS_PER_LONG));
}

static bool has_power_key(int fd)
{
	unsigned long event_bits[NBITS(EV_MAX + 1)] = { 0 };
	unsigned long key_bits[NBITS(KEY_MAX + 1)] = { 0 };

	if (ioctl(fd, EVIOCGBIT(0, sizeof(event_bits)), event_bits) < 0 ||
	    !bit_set(event_bits, EV_KEY))
		return false;
	if (ioctl(fd, EVIOCGBIT(EV_KEY, sizeof(key_bits)), key_bits) < 0)
		return false;
	return bit_set(key_bits, KEY_POWER);
}

static int add_real_events(struct pollfd *fds, int count)
{
	DIR *dir;
	struct dirent *entry;

	dir = opendir("/dev/input");
	if (!dir)
		return count;
	while ((entry = readdir(dir)) != NULL && count < MAX_EVENTS) {
		char path[256];
		int fd;

		if (strncmp(entry->d_name, "event", 5) != 0)
			continue;
		if (snprintf(path, sizeof(path), "/dev/input/%s", entry->d_name) >=
		    (int)sizeof(path))
			continue;
		fd = open(path, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
		if (fd < 0)
			continue;
		if (!has_power_key(fd)) {
			close(fd);
			continue;
		}
		fds[count].fd = fd;
		fds[count].events = POLLIN;
		count++;
	}
	closedir(dir);
	return count;
}

static int classify_event(bool *armed, bool *pressed, int64_t *pressed_at,
			  int64_t now, int64_t started, int64_t arm_after,
			  const struct input_event *event)
{
	int64_t held;

	if (!*armed && now - started >= arm_after) {
		*armed = true;
		fprintf(stderr, "liuqin-charger-power-key: armed\n");
	}
	if (event->type != EV_KEY || event->code != KEY_POWER)
		return -1;
	if (event->value == 1 && *armed) {
		*pressed = true;
		*pressed_at = now;
		return -1;
	}
	if (event->value != 0)
		return -1;
	if (!*armed || !*pressed) {
		*armed = true;
		*pressed = false;
		return -1;
	}
	held = now - *pressed_at;
	*pressed = false;
	if (held >= NORMAL_MIN_MS && held <= NORMAL_MAX_MS) {
		fprintf(stderr, "liuqin-charger-power-key: normal %lldms\n",
			(long long)held);
		return 0;
	}
	if (held >= BOOTLOADER_MIN_MS) {
		fprintf(stderr, "liuqin-charger-power-key: bootloader %lldms\n",
			(long long)held);
		return 10;
	}
	fprintf(stderr, "liuqin-charger-power-key: ignored %lldms\n",
		(long long)held);
	return -1;
}

static int run_test_script(const char *path, int64_t arm_after, int64_t timeout)
{
	FILE *stream;
	char action[16];
	long long offset;
	long long last_offset = -1;
	bool armed = false;
	bool pressed = false;
	int64_t pressed_at = 0;
	int64_t started = 1000;

	stream = fopen(path, "r");
	if (!stream) {
		perror("open test script");
		return 1;
	}
	while (fscanf(stream, "%15s %lld", action, &offset) == 2) {
		struct input_event event = { 0 };
		int rc;

		if (offset < last_offset || (offset > timeout && !pressed) ||
		    (strcmp(action, "press") && strcmp(action, "release") && strcmp(action, "wait"))) {
			fclose(stream);
			return 64;
		}
		last_offset = offset;
		rc = held_action(pressed, pressed_at, started + offset);
		if (rc >= 0) { fclose(stream); return rc; }
		if (!strcmp(action, "wait")) continue;
		event.type = EV_KEY;
		event.code = KEY_POWER;
		event.value = !strcmp(action, "press") ? 1 : 0;
		rc = classify_event(&armed, &pressed, &pressed_at,
				    started + offset, started, arm_after, &event);
		if (rc >= 0) {
			fclose(stream);
			return rc;
		}
	}
	fclose(stream);
	return 2;
}

int main(int argc, char **argv)
{
	struct pollfd fds[MAX_EVENTS] = { 0 };
	const char *test_event = NULL;
	const char *test_script = NULL;
	int64_t timeout = 30000;
	int64_t arm_after = ARM_AFTER_MS;
	int64_t started;
	bool armed = false;
	bool pressed = false;
	int64_t pressed_at = 0;
	int count = 0;
	int i;

	for (i = 1; i < argc; i++) {
		char *end;
		long value;

		if ((!strcmp(argv[i], "--test-event") || !strcmp(argv[i], "--test-script")) &&
		    i + 1 < argc) {
			if (!strcmp(argv[i], "--test-event"))
				test_event = argv[++i];
			else
				test_script = argv[++i];
			continue;
		}
		if ((!strcmp(argv[i], "--timeout-ms") || !strcmp(argv[i], "--arm-after-ms")) &&
		    i + 1 < argc) {
			value = strtol(argv[++i], &end, 10);
			if (*end || value < 0 || value > 600000)
				return 64;
			if (!strcmp(argv[i - 1], "--timeout-ms")) {
				if (value < 1)
					return 64;
				timeout = value;
			} else {
				arm_after = value;
			}
			continue;
		}
		fprintf(stderr, "usage: %s [--timeout-ms N] [--arm-after-ms N] [--test-event FILE|--test-script FILE]\n", argv[0]);
		return 64;
	}
	if (test_event && test_script)
		return 64;
	if (test_script)
		return run_test_script(test_script, arm_after, timeout);

	started = monotonic_ms();
	if (started < 0)
		return 1;
	if (test_event) {
		fds[0].fd = open(test_event, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
		if (fds[0].fd < 0) {
			perror("open test event");
			return 1;
		}
		fds[0].events = POLLIN;
		count = 1;
	} else {
		count = add_real_events(fds, count);
	}

	for (;;) {
		int64_t now = monotonic_ms();
		int rc;

		if (now < 0)
			return 1;
		if (held_action(pressed, pressed_at, now) == 10)
			return 10;
		if (now - started >= timeout && !pressed)
			return 2;
		if (!test_event && count == 0)
			count = add_real_events(fds, count);
		rc = poll(fds, count, 250);
		if (rc < 0) {
			if (errno == EINTR)
				continue;
			perror("poll");
			return 1;
		}
		for (i = 0; i < count; i++) {
			struct input_event event;
			ssize_t got;

			if (!(fds[i].revents & (POLLIN | POLLHUP)))
				continue;
			while ((got = read(fds[i].fd, &event, sizeof(event))) == sizeof(event)) {
				int result = classify_event(&armed, &pressed, &pressed_at,
							    monotonic_ms(), started, arm_after, &event);
				if (result >= 0)
					return result;
			}
			if (got < 0 && errno != EAGAIN && errno != EINTR) {
				perror("read input event");
				return 1;
			}
		}
	}
}
