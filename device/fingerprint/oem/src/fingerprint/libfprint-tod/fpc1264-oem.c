/* SPDX-License-Identifier: LGPL-2.1-or-later
 * Explicit TOD adapter for the OEM FPC1264 match path. No software matching.
 * Each FpPrint contains one opaque, single-finger OEM database (uay), version 1.
 * fprintd owns enrollment authorization and persistence. A root-only provider
 * supplies the OEM credential and returns one opaque enrolled database.
 */
#include <gio/gio.h>
#include <gmodule.h>
#include <glib/gstdio.h>
#include <fpi-device.h>
#include <fpi-print.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

typedef struct {
  FpDevice parent;
  GSubprocess *child;
  gchar *directory;
  gchar *database;
  gchar *updated_database;
  FpPrint *enrolled_print;
  gboolean cancelled;
  GDataInputStream *enrol_output;
  GError *enrol_error;
  gint enrol_stages;
  gint enrol_completed;
  guint enrol_remaining;
  gboolean enrol_cleaned;
  gboolean enrol_succeeded;
  gboolean enrol_existing;
} FpiDeviceFpc1264Oem;

typedef struct { FpDeviceClass parent; } FpiDeviceFpc1264OemClass;
G_DEFINE_TYPE (FpiDeviceFpc1264Oem, fpi_device_fpc1264_oem, FP_TYPE_DEVICE)

static void
clear_operation (FpiDeviceFpc1264Oem *self)
{
  g_clear_object (&self->child);
  g_clear_object (&self->enrol_output);
  g_clear_error (&self->enrol_error);
  if (self->database)
    g_unlink (self->database);
  if (self->updated_database)
    g_unlink (self->updated_database);
  if (self->directory)
    g_rmdir (self->directory);
  g_clear_pointer (&self->database, g_free);
  g_clear_pointer (&self->updated_database, g_free);
  g_clear_object (&self->enrolled_print);
  g_clear_pointer (&self->directory, g_free);
  self->enrol_stages = 0;
  self->enrol_completed = 0;
  self->enrol_remaining = 0;
  self->enrol_cleaned = FALSE;
  self->enrol_succeeded = FALSE;
  self->enrol_existing = FALSE;
}

static void
probe (FpDevice *device)
{
  struct stat info;
  if (lstat ("/dev/fpc1020", &info) || !S_ISCHR (info.st_mode))
    fpi_device_probe_complete (device, NULL, NULL,
                              fpi_device_error_new_msg (FP_DEVICE_ERROR_NOT_SUPPORTED,
                                                        "FPC1264 stable-base device is unavailable"));
  else
    fpi_device_probe_complete (device, "liuqin-fpc1264-oem", "FPC1264 OEM on liuqin", NULL);
}

static void
open_device (FpDevice *device)
{
  fpi_device_open_complete (device, geteuid () == 0 ? NULL :
                           fpi_device_error_new_msg (FP_DEVICE_ERROR_GENERAL,
                                                     "The OEM runtime requires a privileged caller"));
}

static void
close_device (FpDevice *device)
{
  clear_operation ((FpiDeviceFpc1264Oem *) device);
  fpi_device_close_complete (device, NULL);
}

/* Accept only the private database exported after the provider's final
 * cleanup marker. fprintd will serialize and publish the resulting FpPrint. */
static gboolean
load_enrolled_database (FpiDeviceFpc1264Oem *self, GError **error)
{
  struct stat info;
  int fd = g_open (self->database, O_RDONLY | O_CLOEXEC | O_NOFOLLOW, 0);
  g_autofree guint8 *database = NULL;
  g_autoptr(GVariant) data = NULL;
  gsize length = 0;
  gboolean ok = FALSE;

  if (fd < 0 || fstat (fd, &info) || !S_ISREG (info.st_mode) ||
      info.st_uid != 0 || info.st_gid != 0 || (info.st_mode & 0777) != 0600 ||
      info.st_size <= 0 || info.st_size > 16u * 1024u * 1024u)
    goto out;
  length = info.st_size;
  database = g_malloc (length);
  for (gsize offset = 0; offset < length;) {
    ssize_t count = read (fd, database + offset, length - offset);
    if (count < 0 && errno == EINTR)
      continue;
    if (count <= 0)
      goto out;
    offset += count;
  }
  data = g_variant_ref_sink (g_variant_new ("(u@ay)", 1u,
             g_variant_new_fixed_array (G_VARIANT_TYPE_BYTE, database, length, 1)));
  fpi_print_set_type (self->enrolled_print, FPI_PRINT_RAW);
  g_object_set (self->enrolled_print, "fpi-data", data, NULL);
  ok = TRUE;
out:
  if (database)
    explicit_bzero (database, length);
  if (fd >= 0)
    close (fd);
  if (!ok)
    g_set_error_literal (error, FP_DEVICE_ERROR, FP_DEVICE_ERROR_DATA_INVALID,
                         "The enrolled fingerprint database is incomplete or not private");
  return ok;
}

static void
enrol_finished (GObject *source, GAsyncResult *result, gpointer user_data)
{
  FpDevice *device = user_data;
  FpiDeviceFpc1264Oem *self = (FpiDeviceFpc1264Oem *) device;
  g_autoptr(GError) error = NULL;
  FpPrint *print = NULL;
  gboolean waited = g_subprocess_wait_finish (G_SUBPROCESS (source), result, &error);
  gboolean exited = waited && g_subprocess_get_if_exited (G_SUBPROCESS (source));
  gint status = exited ? g_subprocess_get_exit_status (G_SUBPROCESS (source)) : -1;
  gboolean cancelled = self->cancelled || fpi_device_action_is_cancelled (device);
  gboolean valid = exited && status == 0 && self->enrol_cleaned &&
                   self->enrol_succeeded && !self->enrol_error;

  if (cancelled && self->enrol_cleaned) {
    g_clear_error (&error);
    error = g_error_new_literal (G_IO_ERROR, G_IO_ERROR_CANCELLED,
                                 "Fingerprint enrollment cancelled after cleanup");
  } else if (self->enrol_error) {
    g_clear_error (&error);
    error = g_steal_pointer (&self->enrol_error);
  } else if (!valid || cancelled) {
    g_clear_error (&error);
    error = fpi_device_error_new_msg (self->enrol_existing ? FP_DEVICE_ERROR_DATA_FULL : FP_DEVICE_ERROR_GENERAL,
                                      self->enrol_existing ? "Only one enrolled finger per user is supported" :
                                      "OEM enrollment did not finish with confirmed cleanup");
  } else if (load_enrolled_database (self, &error)) {
    print = g_steal_pointer (&self->enrolled_print);
  }
  if (!print && (!cancelled || !self->enrol_cleaned))
    g_warning ("OEM enroll runtime exit=%d cleanup_complete=%d enrolled=%d",
               status, self->enrol_cleaned, self->enrol_succeeded);
  clear_operation (self);
  fpi_device_report_finger_status (device, FP_FINGER_STATUS_NONE);
  fpi_device_enroll_complete (device, print, g_steal_pointer (&error));
  g_object_unref (device);
}

static void
enrol_retry (FpDevice *device, FpDeviceRetry retry)
{
  FpiDeviceFpc1264Oem *self = (FpiDeviceFpc1264Oem *) device;
  if (!self->cancelled)
    fpi_device_enroll_progress (device, self->enrol_completed, NULL,
                                fpi_device_retry_new (retry));
}

static void
enrol_status_line (FpDevice *device, const gchar *line)
{
  FpiDeviceFpc1264Oem *self = (FpiDeviceFpc1264Oem *) device;
  gint status;
  guint remaining;
  gchar extra;
  const gchar *failures[] = {
    "native_enrol_runtime=FAILED ", "native_enrol=FAILED ",
    "pipeline_incomplete=", "end_enrol_not_submitted=",
    "enrol_authorization=TA_rejected", NULL
  };

  /* The provider guarantees these status/phase fields contain no credential,
   * token or template payload. Do not forward other stdout/stderr lines. */
  for (guint i = 0; failures[i]; i++)
    if (g_str_has_prefix (line, failures[i])) {
      g_warning ("OEM enroll diagnostic: %s", line);
      break;
    }

  /* This provider-wide marker follows every started child's cleanup. A
   * credential-creation runtime's earlier cleanup is deliberately ignored. */
  if (g_str_equal (line, "native_enrol_cleanup=OK"))
    self->enrol_cleaned = TRUE;
  else if (g_str_equal (line, "native_enrol=OK single_finger_database=1"))
    self->enrol_succeeded = self->enrol_cleaned;
  else if (g_str_equal (line, "native_enrol=REFUSED existing_template=1"))
    self->enrol_existing = TRUE;
  else if (!self->cancelled &&
           sscanf (line, "enrol_progress_status=%d remaining=%u%c", &status, &remaining, &extra) == 2) {
    g_message ("OEM enroll sample status=%d remaining=%u", status, remaining);
    /* Positive OEM BIO_ENROL status means sampling continues. Zero is the
     * final sample and is only accepted through the final database result. */
    if (status < 0 || remaining >= 40) {
      enrol_retry (device, FP_DEVICE_RETRY_GENERAL);
      return;
    }
    if (!self->enrol_stages) {
      self->enrol_stages = remaining + 1;
      self->enrol_remaining = remaining + 1;
      fpi_device_set_nr_enroll_stages (device, self->enrol_stages);
    }
    if (remaining >= self->enrol_remaining) {
      enrol_retry (device, FP_DEVICE_RETRY_GENERAL);
      return;
    }
    self->enrol_remaining = remaining;
    /* Reserve completion for successful export/cleanup. A decreasing count
     * at this final boundary is progress, not a rejected sample. */
    gint completed = CLAMP (self->enrol_stages - (gint) remaining, 0, self->enrol_stages - 1);
    /* fprintd emits one EnrollStatus per callback, so publish every newly
     * completed stage even when the OEM remaining count drops by several. */
    while (!self->cancelled && self->enrol_completed < completed)
      fpi_device_enroll_progress (device, ++self->enrol_completed, NULL, NULL);
  } else if (!self->cancelled && g_str_has_prefix (line, "capture_rejected=")) {
    if (sscanf (line, "capture_rejected=%d", &status) == 1)
      g_message ("OEM enroll capture_rejected=%d", status);
    enrol_retry (device, FP_DEVICE_RETRY_GENERAL);
  } else if (!self->cancelled && g_str_has_prefix (line, "READY lift finger")) {
    g_message ("OEM enroll phase=lift-ready");
    fpi_device_report_finger_status (device, FP_FINGER_STATUS_PRESENT);
  } else if (!self->cancelled && g_str_has_prefix (line, "READY enrol attempt=")) {
    g_message ("OEM enroll phase=down-setup");
    fpi_device_report_finger_status (device, FP_FINGER_STATUS_NEEDED);
  } else if (!self->cancelled && g_str_has_prefix (line, "READY finger_irq_armed=down"))
    g_message ("OEM enroll phase=down-ready");
}

static void enrol_read_line (GObject *source, GAsyncResult *result, gpointer user_data);

static void
enrol_read_next (FpDevice *device)
{
  FpiDeviceFpc1264Oem *self = (FpiDeviceFpc1264Oem *) device;
  /* Never cancel the read/wait: even a cancelled enrollment owns the sensor
   * until the provider has unwound its TEE clients and exited. */
  g_data_input_stream_read_line_async (self->enrol_output, G_PRIORITY_DEFAULT, NULL,
                                      enrol_read_line, device);
}

static void
enrol_read_line (GObject *source, GAsyncResult *result, gpointer user_data)
{
  FpDevice *device = user_data;
  FpiDeviceFpc1264Oem *self = (FpiDeviceFpc1264Oem *) device;
  g_autoptr(GError) error = NULL;
  gsize length;
  g_autofree gchar *line = g_data_input_stream_read_line_finish_utf8 (G_DATA_INPUT_STREAM (source),
                                                                    result, &length, &error);
  if (line) {
    if (length <= 4096)
      enrol_status_line (device, line);
    enrol_read_next (device);
    return;
  }
  if (error) {
    self->enrol_error = g_steal_pointer (&error);
    g_subprocess_send_signal (self->child, SIGTERM);
  }
  g_subprocess_wait_async (self->child, NULL, enrol_finished, device);
}

static void
enrol (FpDevice *device)
{
  FpiDeviceFpc1264Oem *self = (FpiDeviceFpc1264Oem *) device;
  FpPrint *print;
  const gchar *bundle = g_getenv ("LIUQIN_FPC_OEM_RUNTIME");
  const gchar *username;
  struct stat info;
  g_autofree gchar *provider = NULL;
  g_autoptr(GError) error = NULL;
  g_autoptr(GSubprocessLauncher) launcher = NULL;

  fpi_device_get_enroll_data (device, &print);
  username = fp_print_get_username (print);
  if (!username || !*username || !FP_FINGER_IS_VALID (fp_print_get_finger (print))) {
    error = fpi_device_error_new (FP_DEVICE_ERROR_DATA_INVALID);
    goto failed;
  }
  if (!bundle || !g_path_is_absolute (bundle) || lstat (bundle, &info) ||
      !S_ISDIR (info.st_mode) || info.st_uid != 0 || (info.st_mode & 0022)) {
    error = fpi_device_error_new_msg (FP_DEVICE_ERROR_GENERAL, "Supply a trusted OEM runtime directory");
    goto failed;
  }
  provider = g_build_filename (bundle, "native_enrol.py", NULL);
  if (lstat (provider, &info) || !S_ISREG (info.st_mode) || info.st_uid != 0 || (info.st_mode & 0022)) {
    error = fpi_device_error_new_msg (FP_DEVICE_ERROR_GENERAL, "Native enrollment provider is missing");
    goto failed;
  }
  self->directory = g_dir_make_tmp ("liuqin-fpc-enrol-XXXXXX", &error);
  if (!self->directory)
    goto failed;
  self->database = g_build_filename (self->directory, "single-finger.db", NULL);
  self->cancelled = FALSE;
  self->enrolled_print = g_object_ref (print);
  fpi_device_set_nr_enroll_stages (device, 20);
  launcher = g_subprocess_launcher_new (G_SUBPROCESS_FLAGS_STDOUT_PIPE | G_SUBPROCESS_FLAGS_STDERR_MERGE);
  g_subprocess_launcher_setenv (launcher, "PYTHONUNBUFFERED", "1", TRUE);
  self->child = g_subprocess_launcher_spawn (launcher, &error, "/usr/bin/python3", provider,
                                            "--enrol", username, self->database, NULL);
  if (!self->child)
    goto failed;
  self->enrol_output = g_data_input_stream_new (g_subprocess_get_stdout_pipe (self->child));
  fpi_device_report_finger_status (device, FP_FINGER_STATUS_NEEDED);
  enrol_read_next (g_object_ref (device));
  return;
failed:
  clear_operation (self);
  fpi_device_enroll_complete (device, NULL, g_steal_pointer (&error));
}

/* Hold the unmodified print for fprintd's compare-and-replace storage update.
 * No database or template bytes are reported outside the owning processes. */
static gboolean
accept_updated_database (FpiDeviceFpc1264Oem *self, gboolean expected, GError **error)
{
  struct stat info;
  int fd = g_open (self->updated_database, O_RDONLY | O_CLOEXEC | O_NOFOLLOW, 0);
  g_autofree guint8 *database = NULL;
  g_autofree guint8 *original_bytes = NULL;
  g_autoptr(FpPrint) original = NULL;
  g_autoptr(GVariant) replacement = NULL;
  gsize original_length;

  if (fd < 0 && errno == ENOENT && !expected)
    return TRUE;
  if (fd < 0 || fstat (fd, &info) || !S_ISREG (info.st_mode) ||
      info.st_uid != 0 || (info.st_mode & 0777) != 0600 ||
      info.st_size <= 0 || info.st_size > 16u * 1024u * 1024u)
    goto failed;
  database = g_malloc (info.st_size);
  for (gsize offset = 0; offset < (gsize) info.st_size;) {
    ssize_t count = read (fd, database + offset, info.st_size - offset);
    if (count < 0 && errno == EINTR)
      continue;
    if (count <= 0)
      goto failed;
    offset += count;
  }
  close (fd);
  fd = -1;
  if (!fp_print_serialize (self->enrolled_print, &original_bytes, &original_length, error))
    return FALSE;
  original = fp_print_deserialize (original_bytes, original_length, error);
  if (!original)
    return FALSE;
  replacement = g_variant_ref_sink (g_variant_new ("(u@ay)", 1u,
                    g_variant_new_fixed_array (G_VARIANT_TYPE_BYTE, database, info.st_size, 1)));
  g_object_set_data_full (G_OBJECT (self->enrolled_print), "liuqin-fpc-oem-original",
                          g_steal_pointer (&original), g_object_unref);
  g_object_set (self->enrolled_print, "fpi-data", replacement, NULL);
  return TRUE;

failed:
  if (fd >= 0)
    close (fd);
  g_set_error_literal (error, FP_DEVICE_ERROR, FP_DEVICE_ERROR_GENERAL,
                       "OEM adaptive template export is incomplete");
  return FALSE;
}

/* Keep diagnostics to known status lines. Never forward raw runtime output,
 * template bytes, credentials or authentication tokens to the journal. */
static void
report_runtime_failure (const gchar *output, const gchar *diagnostic,
                        gint status, gboolean cleaned)
{
  const gchar *prefixes[] = {
    "finger_irq_failed=", "match_incomplete=", "match_unavailable=",
    "auth_result_invalid=", "db_import_remaining_mismatch ",
    "template_count_response_invalid=", "oem_runtime_timed_out=",
    "oem_runtime_cancelled=", "oem_runtime=BUSY",
    "capture_result_word4=", "capture_note=", "identify_app_status=",
    "match_result=inconclusive ", "auth_result_available=",
    "loaded_template_count=", "template_enumerate_app_status=",
    "authenticator_id_app_status=", "database_update_required=", NULL
  };
  g_autofree gchar *messages = g_strconcat (output ? output : "", "\n",
                                          diagnostic ? diagnostic : "", NULL);
  g_auto(GStrv) lines = g_strsplit (messages, "\n", -1);

  g_warning ("OEM verify runtime exit=%d cleanup_complete=%d irq_armed=%d irq_received=%d",
             status, cleaned,
             !!(output && strstr (output, "READY finger_irq_armed=down press finger now\n")),
             !!(output && strstr (output, "finger_irq=down\n")));
  for (gsize i = 0; lines[i]; i++)
    for (gsize j = 0; prefixes[j]; j++)
      if (g_str_has_prefix (lines[i], prefixes[j])) {
        g_warning ("OEM verify diagnostic: %s", lines[i]);
        break;
      }
}

static void
verify_finished (GObject *source, GAsyncResult *result, gpointer user_data)
{
  FpDevice *device = user_data;
  FpiDeviceFpc1264Oem *self = (FpiDeviceFpc1264Oem *) device;
  g_autoptr(GError) error = NULL;
  g_autofree gchar *output = NULL;
  g_autofree gchar *diagnostic = NULL;
  gboolean communicated = g_subprocess_communicate_utf8_finish (G_SUBPROCESS (source), result,
                                                               &output, &diagnostic, &error);
  gboolean exited = communicated && g_subprocess_get_if_exited (G_SUBPROCESS (source));
  gint status = exited ? g_subprocess_get_exit_status (G_SUBPROCESS (source)) : -1;
  gboolean cleaned = output && strstr (output, "oem_runtime_cleanup firmware_path_restored=1 sensor_power_off=1 listener_stopped=1\n");
  gboolean matched = output && strstr (output, "match_result=matched desktop_authentication=disabled\n");
  gboolean missed = output && strstr (output, "match_result=not_matched desktop_authentication=disabled\n");
  gboolean cancelled = self->cancelled || fpi_device_action_is_cancelled (device);
  gboolean valid_result = cleaned && ((status == 0 && matched) || (status == 3 && missed));
  /* The OEM client only emits these inconclusive results after an ordinary
   * identify response and successful DB export. They never authorize a match.
   * Finish this cleaned attempt and let fprintd request a fresh press. */
  gboolean retry_scan = cleaned && status == 1 && !matched && !missed && output &&
    (strstr (output, "match_result=inconclusive identify_app_status=4\n") ||
     strstr (output, "match_result=inconclusive identify_app_status=12\n"));
  if (!cancelled && valid_result && status == 0 &&
      !accept_updated_database (self, output && strstr (output, "database_update_required=1\n"), &error))
    valid_result = FALSE;
  if (!cancelled && communicated && !valid_result)
    report_runtime_failure (output, diagnostic, status, cleaned);
  clear_operation (self);
  fpi_device_report_finger_status (device, FP_FINGER_STATUS_NONE);
  if (cancelled)
    fpi_device_verify_complete (device, g_error_new_literal (G_IO_ERROR, G_IO_ERROR_CANCELLED,
                                                            "Fingerprint verification cancelled"));
  else if (!communicated)
    fpi_device_verify_complete (device, g_steal_pointer (&error));
  else if (retry_scan) {
    g_message ("OEM verify result=retry_scan");
    fpi_device_verify_report (device, FPI_MATCH_ERROR, NULL,
                              fpi_device_retry_new (FP_DEVICE_RETRY_GENERAL));
    fpi_device_verify_complete (device, NULL);
  }
  else if (!valid_result)
    fpi_device_verify_complete (device, error ? g_steal_pointer (&error) :
                               fpi_device_error_new_msg (FP_DEVICE_ERROR_GENERAL,
                                                         "OEM verification did not complete"));
  else {
    g_message ("OEM verify result=%s", status == 0 ? "matched" : "not_matched");
    fpi_device_verify_report (device, status == 0 ? FPI_MATCH_SUCCESS : FPI_MATCH_FAIL, NULL, NULL);
    fpi_device_verify_complete (device, NULL);
  }
  g_object_unref (device);
}

static void
verify (FpDevice *device)
{
  FpiDeviceFpc1264Oem *self = (FpiDeviceFpc1264Oem *) device;
  FpPrint *print;
  g_autoptr(GVariant) data = NULL;
  g_autoptr(GVariant) bytes = NULL;
  g_autoptr(GError) error = NULL;
  g_autofree gchar *runtime = NULL;
  const gchar *bundle = g_getenv ("LIUQIN_FPC_OEM_RUNTIME");
  struct stat info;
  guint32 version;
  gsize length;
  const guint8 *database;
  int descriptor = -1;

  fpi_device_get_verify_data (device, &print);
  g_object_get (print, "fpi-data", &data, NULL);
  if (!data || !g_variant_is_of_type (data, G_VARIANT_TYPE ("(uay)")))
    goto invalid_print;
  g_variant_get (data, "(u@ay)", &version, &bytes);
  database = g_variant_get_fixed_array (bytes, &length, 1);
  if (version != 1 || !length || length > 16u * 1024u * 1024u)
    goto invalid_print;
  if (!bundle || !g_path_is_absolute (bundle) || lstat (bundle, &info) ||
      !S_ISDIR (info.st_mode) || info.st_uid != 0 || (info.st_mode & 0022)) {
    error = fpi_device_error_new_msg (FP_DEVICE_ERROR_GENERAL, "Supply a trusted private OEM runtime directory");
    goto failed;
  }
  runtime = g_build_filename (bundle, "oem_runtime.py", NULL);
  if (lstat (runtime, &info) || !S_ISREG (info.st_mode) || info.st_uid != 0 || (info.st_mode & 0022)) {
    error = fpi_device_error_new_msg (FP_DEVICE_ERROR_GENERAL, "Trusted OEM runner is missing");
    goto failed;
  }
  self->directory = g_dir_make_tmp ("liuqin-fpc-verify-XXXXXX", &error);
  if (!self->directory)
    goto failed;
  self->database = g_build_filename (self->directory, "single-finger.db", NULL);
  self->updated_database = g_build_filename (self->directory, "updated-single-finger.db", NULL);
  descriptor = g_open (self->database, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0600);
  if (descriptor < 0)
    goto io_failure;
  for (gsize offset = 0; offset < length;) {
    ssize_t count = write (descriptor, database + offset, length - offset);
    if (count < 0 && errno == EINTR)
      continue;
    if (count <= 0)
      goto io_failure;
    offset += count;
  }
  if (close (descriptor)) {
    descriptor = -1;
    goto io_failure;
  }
  descriptor = -1;
  self->cancelled = FALSE;
  self->enrolled_print = g_object_ref (print);
  g_object_set_data (G_OBJECT (print), "liuqin-fpc-oem-original", NULL);
  self->child = g_subprocess_new (G_SUBPROCESS_FLAGS_STDOUT_PIPE | G_SUBPROCESS_FLAGS_STDERR_PIPE,
                                 &error, "/usr/bin/python3", runtime, "--ufs-rpmb-authenticated",
                                 "--match-single", self->database, self->updated_database, NULL);
  if (!self->child)
    goto failed;
  fpi_device_report_finger_status (device, FP_FINGER_STATUS_NEEDED);
  /* Wait for runtime cleanup even on cancel; GCancellable would finish too soon. */
  g_subprocess_communicate_utf8_async (self->child, NULL, NULL, verify_finished, g_object_ref (device));
  return;

invalid_print:
  error = fpi_device_error_new_msg (FP_DEVICE_ERROR_DATA_INVALID, "A versioned single-finger OEM database is required");
  goto failed;
io_failure:
  error = g_error_new (G_IO_ERROR, g_io_error_from_errno (errno), "Private database staging failed");
failed:
  if (descriptor >= 0)
    close (descriptor);
  clear_operation (self);
  fpi_device_verify_complete (device, g_steal_pointer (&error));
}

static void
cancel (FpDevice *device)
{
  FpiDeviceFpc1264Oem *self = (FpiDeviceFpc1264Oem *) device;
  self->cancelled = TRUE;
  if (self->child)
    g_subprocess_send_signal (self->child, SIGTERM);
}

static void
fpi_device_fpc1264_oem_init (FpiDeviceFpc1264Oem *self)
{
  (void) self;
}

static void
fpi_device_fpc1264_oem_class_init (FpiDeviceFpc1264OemClass *klass)
{
  static const FpIdEntry devices[] = {
    { .virtual_envvar = "FP_LIUQIN_FPC1264_OEM_ENABLE" }, { .pid = 0 }
  };
  FpDeviceClass *device = FP_DEVICE_CLASS (klass);
  device->id = "fpc1264_oem";
  device->full_name = "FPC1264 OEM on liuqin";
  device->type = FP_DEVICE_TYPE_VIRTUAL;
  device->scan_type = FP_SCAN_TYPE_PRESS;
  device->id_table = devices;
  device->temp_hot_seconds = -1;
  device->nr_enroll_stages = 20;
  device->probe = probe;
  device->open = open_device;
  device->close = close_device;
  device->enroll = enrol;
  device->verify = verify;
  device->cancel = cancel;
  fpi_device_class_auto_initialize_features (device);
}

G_MODULE_EXPORT GType
fpi_tod_shared_driver_get_type (void)
{
  return fpi_device_fpc1264_oem_get_type ();
}
