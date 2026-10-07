/* SPDX-License-Identifier: LGPL-2.1-or-later
 * Opaque database to FpPrint serialization. Caller owns user authentication,
 * binary FDs and publication; this never registers a print or changes PAM.
 */
#include <fprint.h>
#include <fpi-print.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <pwd.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static int
descriptor (const char *argument)
{
  char *end;
  errno = 0;
  long value = strtol (argument, &end, 10);
  return errno || end == argument || *end || value < 3 || value > INT_MAX ? -1 : (int) value;
}

int
main (int argc, char **argv)
{
  static const char *fingers[] = {NULL, "left-thumb", "left-index-finger", "left-middle-finger",
    "left-ring-finger", "left-little-finger", "right-thumb", "right-index-finger",
    "right-middle-finger", "right-ring-finger", "right-little-finger"};
  struct stat input_info, output_info;
  int input, output, finger = 0, result = 1;
  guint8 *database = NULL, *serialized = NULL;
  gsize length = 0, serialized_length = 0;
  g_autoptr(FpContext) context = NULL;
  g_autoptr(FpPrint) print = NULL;
  g_autoptr(FpPrint) roundtrip = NULL;
  g_autoptr(GError) error = NULL;
  g_autoptr(GDateTime) now = NULL;
  FpDevice *device = NULL;

  if (argc != 6 || strcmp (argv[1], "--serialize") || geteuid () != 0) {
    g_printerr ("usage (root): fpc-oem-print --serialize LINUX_USERNAME FINGER_NAME DATABASE_FD OUTPUT_FD\n");
    return 2;
  }
  for (int i = FP_FINGER_FIRST; i <= FP_FINGER_LAST; i++)
    if (!strcmp (argv[3], fingers[i]))
      finger = i;
  input = descriptor (argv[4]);
  output = descriptor (argv[5]);
  if (!getpwnam (argv[2]) || !finger || input < 0 || output < 0 || input == output ||
      fstat (input, &input_info) || fstat (output, &output_info) ||
      !S_ISREG (input_info.st_mode) || !S_ISREG (output_info.st_mode) ||
      input_info.st_uid != 0 || output_info.st_uid != 0 ||
      (input_info.st_mode & 0777) != 0600 || (output_info.st_mode & 0777) != 0600 ||
      input_info.st_size <= 0 || input_info.st_size > 16 * 1024 * 1024 || output_info.st_size != 0 ||
      (input_info.st_dev == output_info.st_dev && input_info.st_ino == output_info.st_ino)) {
    g_printerr ("oem_print_serialization=FAILED phase=caller_metadata_or_private_binary_fds\n");
    return 2;
  }
  int input_flags = fcntl (input, F_GETFL), output_flags = fcntl (output, F_GETFL);
  if (input_flags < 0 || output_flags < 0 || (input_flags & O_ACCMODE) == O_WRONLY ||
      (output_flags & O_ACCMODE) == O_RDONLY || lseek (input, 0, SEEK_SET) < 0 || lseek (output, 0, SEEK_SET) < 0) {
    g_printerr ("oem_print_serialization=FAILED phase=binary_fd_access\n");
    return 2;
  }
  length = input_info.st_size;
  database = g_malloc (length);
  for (gsize offset = 0; offset < length;) {
    ssize_t count = read (input, database + offset, length - offset);
    if (count < 0 && errno == EINTR)
      continue;
    if (count <= 0)
      goto out;
    offset += count;
  }
  context = fp_context_new ();
  fp_context_enumerate (context);
  GPtrArray *devices = fp_context_get_devices (context);
  for (guint i = 0; i < devices->len; i++) {
    FpDevice *candidate = g_ptr_array_index (devices, i);
    if (!strcmp (fp_device_get_driver (candidate), "fpc1264_oem"))
      device = candidate;
  }
  if (!device)
    goto out;
  print = g_object_ref_sink (fp_print_new (device));
  fpi_print_set_type (print, FPI_PRINT_RAW);
  fp_print_set_username (print, argv[2]);
  fp_print_set_finger (print, (FpFinger) finger);
  now = g_date_time_new_now_local ();
  GDate date;
  g_date_clear (&date, 1);
  g_date_set_dmy (&date, g_date_time_get_day_of_month (now), g_date_time_get_month (now), g_date_time_get_year (now));
  fp_print_set_enroll_date (print, &date);
  GVariant *bytes = g_variant_new_fixed_array (G_VARIANT_TYPE_BYTE, database, length, 1);
  g_object_set (print, "fpi-data", g_variant_new ("(u@ay)", 1u, bytes), NULL);
  if (!fp_print_serialize (print, &serialized, &serialized_length, &error))
    goto out;
  roundtrip = fp_print_deserialize (serialized, serialized_length, &error);
  if (!roundtrip || !fp_print_compatible (roundtrip, device) ||
      g_strcmp0 (fp_print_get_username (roundtrip), argv[2]) ||
      fp_print_get_finger (roundtrip) != (FpFinger) finger ||
      !fp_print_equal (print, roundtrip))
    goto out;
  for (gsize offset = 0; offset < serialized_length;) {
    ssize_t count = write (output, serialized + offset, serialized_length - offset);
    if (count < 0 && errno == EINTR)
      continue;
    if (count <= 0)
      goto out;
    offset += count;
  }
  if (fsync (output))
    goto out;
  result = 0;
out:
  if (result && serialized && ftruncate (output, 0))
    g_printerr ("opaque_output_cleanup_failed=1 output_not_publishable=1\n");
  if (database) {
    explicit_bzero (database, length);
    g_free (database);
  }
  if (serialized) {
    explicit_bzero (serialized, serialized_length);
    g_free (serialized);
  }
  g_print ("oem_print_serialization=%s opaque_output_forwarded=%d roundtrip_checked=%d template_validation=TA_required desktop_authentication=disabled\n",
           result ? "FAILED" : "OK", result == 0, result == 0);
  return result;
}
