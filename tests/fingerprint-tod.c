/* SPDX-License-Identifier: MIT
 * Real libfprint asynchronous API tests with a synthetic process provider.
 * The probe alone is replaced: these tests cannot contact physical hardware.
 */
#include "../device/fingerprint/oem/src/fingerprint/libfprint-tod/fpc1264-oem.c"

typedef struct {
  GMainLoop *loop;
  FpDevice *device;
  FpPrint *print;
  GError *error;
  GCancellable *cancellable;
  guint stages;
  guint retries;
  gboolean should_cancel;
  GPtrArray *warnings;
  gint64 started;
  gint64 cancelled;
  gint64 finished;
} Fixture;

static gchar *bundle;

static void
record_warning (const gchar *domain, GLogLevelFlags level, const gchar *message, gpointer data)
{
  (void) domain;
  (void) level;
  g_ptr_array_add (data, g_strdup (message));
}

static void
fixture_probe (FpDevice *device)
{
  fpi_device_probe_complete (device, "liuqin-fpc1264-oem", "Synthetic OEM test device", NULL);
}

static void
initialized (GObject *source, GAsyncResult *result, gpointer data)
{
  Fixture *fixture = data;
  fixture->device = FP_DEVICE (g_async_initable_new_finish (G_ASYNC_INITABLE (source), result, &fixture->error));
  g_main_loop_quit (fixture->loop);
}

static void
finished (GObject *source, GAsyncResult *result, gpointer data)
{
  Fixture *fixture = data;
  fixture->finished = g_get_monotonic_time ();
  fixture->print = fp_device_enroll_finish (FP_DEVICE (source), result, &fixture->error);
  g_main_loop_quit (fixture->loop);
}

static gboolean
request_cancel (gpointer data)
{
  Fixture *fixture = data;
  fixture->cancelled = g_get_monotonic_time ();
  g_cancellable_cancel (fixture->cancellable);
  return G_SOURCE_REMOVE;
}

static void
progress (FpDevice *device, gint completed, FpPrint *print, gpointer data, GError *error)
{
  Fixture *fixture = data;
  (void) device;
  (void) print;
  if (error) {
    g_assert_cmpuint (error->domain, ==, FP_DEVICE_RETRY);
    fixture->retries++;
  } else {
    g_assert_cmpint (completed, >, fixture->stages);
    fixture->stages = completed;
    if (fixture->should_cancel && !fixture->cancelled)
      g_timeout_add (10, request_cancel, fixture);
  }
}

static gboolean
timed_out (gpointer data)
{
  (void) data;
  g_error ("Native enrollment test timed out");
  return G_SOURCE_REMOVE;
}

static void
exercise (gconstpointer data)
{
  const gchar *mode = data;
  gboolean success = g_str_equal (mode, "success") || g_str_equal (mode, "zero") ||
                     g_str_equal (mode, "wait-exit") || g_str_equal (mode, "stderr") ||
                     g_str_equal (mode, "twenty-six") || g_str_equal (mode, "normal-lift");
  gboolean cancelled = g_str_has_prefix (mode, "cancel");
  Fixture fixture = {0};
  g_autoptr(FpPrint) template = NULL;
  g_autoptr(GVariant) variant = NULL;
  g_autoptr(GVariant) bytes = NULL;
  g_autofree guint8 *serialized = NULL;
  g_autoptr(FpPrint) roundtrip = NULL;
  gsize length;
  guint32 version;
  FpDeviceClass *klass = g_type_class_ref (fpi_device_fpc1264_oem_get_type ());
  klass->probe = fixture_probe;
  fixture.loop = g_main_loop_new (NULL, FALSE);
  fixture.cancellable = g_cancellable_new ();
  g_async_initable_new_async (fpi_device_fpc1264_oem_get_type (), G_PRIORITY_DEFAULT,
                             NULL, initialized, &fixture, "fpi-environ", "test", NULL);
  g_main_loop_run (fixture.loop);
  g_assert_no_error (fixture.error);
  g_assert_true (fp_device_open_sync (fixture.device, NULL, &fixture.error));
  g_assert_no_error (fixture.error);
  template = g_object_ref_sink (fp_print_new (fixture.device));
  fp_print_set_username (template, "fixture-user");
  fp_print_set_finger (template, FP_FINGER_RIGHT_INDEX);
  g_setenv ("FPC_TEST_CASE", mode, TRUE);
  fixture.should_cancel = cancelled;
  fixture.started = g_get_monotonic_time ();
  fixture.warnings = g_ptr_array_new_with_free_func (g_free);
  guint log_handler = g_log_set_handler (NULL, G_LOG_LEVEL_WARNING, record_warning, fixture.warnings);
  guint timeout = g_timeout_add_seconds (10, timed_out, NULL);
  fp_device_enroll (fixture.device, template, fixture.cancellable, progress, &fixture,
                   NULL, finished, &fixture);
  g_main_loop_run (fixture.loop);
  g_source_remove (timeout);
  g_log_remove_handler (NULL, log_handler);
  guint expected_warnings = g_str_equal (mode, "diagnostics") ? 6 :
                            (!success && (!cancelled || g_str_equal (mode, "cancel-no-cleanup"))) ? 1 : 0;
  g_assert_cmpuint (fixture.warnings->len, ==, expected_warnings);
  if (g_str_equal (mode, "diagnostics")) {
    const gchar *prefixes[] = {"native_enrol_runtime=FAILED", "native_enrol=FAILED",
                               "pipeline_incomplete=", "end_enrol_not_submitted=",
                               "enrol_authorization=TA_rejected"};
    for (guint i = 0; i < G_N_ELEMENTS (prefixes); i++)
      g_assert_nonnull (strstr (g_ptr_array_index (fixture.warnings, i), prefixes[i]));
  }
  if (expected_warnings)
    g_assert_true (g_str_has_prefix (g_ptr_array_index (fixture.warnings, expected_warnings - 1),
                                    "OEM enroll runtime exit="));
  if (success) {
    g_assert_no_error (fixture.error);
    g_assert_nonnull (fixture.print);
    g_assert_cmpstr (fp_print_get_username (fixture.print), ==, "fixture-user");
    g_assert_cmpint (fp_print_get_finger (fixture.print), ==, FP_FINGER_RIGHT_INDEX);
    g_object_get (fixture.print, "fpi-data", &variant, NULL);
    g_assert_true (g_variant_is_of_type (variant, G_VARIANT_TYPE ("(uay)")));
    g_variant_get (variant, "(u@ay)", &version, &bytes);
    g_assert_cmpuint (version, ==, 1);
    const guint8 *database = g_variant_get_fixed_array (bytes, &length, 1);
    g_assert_cmpmem (database, length, "synthetic opaque database", strlen ("synthetic opaque database"));
    g_assert_true (fp_print_serialize (fixture.print, &serialized, &length, &fixture.error));
    roundtrip = fp_print_deserialize (serialized, length, &fixture.error);
    g_assert_no_error (fixture.error);
    g_assert_true (fp_print_equal (fixture.print, roundtrip));
    if (g_str_equal (mode, "zero")) {
      g_assert_cmpuint (fixture.stages, ==, 0);
      g_assert_cmpuint (fixture.retries, ==, 0);
      g_assert_cmpint (fp_device_get_nr_enroll_stages (fixture.device), ==, 1);
    } else {
      g_assert_cmpuint (fixture.stages, ==, g_str_equal (mode, "twenty-six") ? 25 : 3);
      g_assert_cmpuint (fixture.retries, ==, g_str_equal (mode, "normal-lift") ? 0 : 2);
      g_assert_cmpint (fp_device_get_nr_enroll_stages (fixture.device), ==,
                       g_str_equal (mode, "twenty-six") ? 26 : 4);
    }
    if (g_str_equal (mode, "wait-exit"))
      g_assert_cmpint (fixture.finished - fixture.started, >=, 200000);
  } else {
    g_assert_null (fixture.print);
    g_assert_nonnull (fixture.error);
    if (cancelled) {
      g_assert_error (fixture.error, G_IO_ERROR, G_IO_ERROR_CANCELLED);
      g_assert_cmpint (fixture.finished - fixture.cancelled, >=, 190000);
    }
    if (g_str_equal (mode, "existing"))
      g_assert_error (fixture.error, FP_DEVICE_ERROR, FP_DEVICE_ERROR_DATA_FULL);
  }
  g_clear_error (&fixture.error);
  g_clear_object (&fixture.print);
  g_assert_true (fp_device_close_sync (fixture.device, NULL, &fixture.error));
  g_assert_no_error (fixture.error);
  g_clear_object (&fixture.device);
  g_clear_object (&fixture.cancellable);
  g_main_loop_unref (fixture.loop);
  g_ptr_array_unref (fixture.warnings);
  g_type_class_unref (klass);
}

int
main (int argc, char **argv)
{
  gchar *fixture = NULL;
  gsize size;
  g_autoptr(GError) error = NULL;
  g_assert_cmpuint (geteuid (), ==, 0);
  g_test_init (&argc, &argv, NULL);
  /* Capture and assert expected runtime warnings separately from new normal
   * per-sample MESSAGE logs; unexpected critical/API errors remain fatal. */
  g_log_set_always_fatal (G_LOG_LEVEL_ERROR | G_LOG_LEVEL_CRITICAL);
  g_assert_true (g_file_get_contents (g_getenv ("FPC_PROVIDER_FIXTURE"), &fixture, &size, &error));
  bundle = g_dir_make_tmp ("fpc-native-api-test-XXXXXX", &error);
  g_assert_no_error (error);
  gchar *provider = g_build_filename (bundle, "native_enrol.py", NULL);
  g_assert_true (g_file_set_contents (provider, fixture, size, &error));
  g_assert_cmpint (g_chmod (provider, 0600), ==, 0);
  g_setenv ("LIUQIN_FPC_OEM_RUNTIME", bundle, TRUE);
  const gchar *cases[] = {"success", "zero", "twenty-six", "early-cleanup", "permissions",
                          "wait-exit", "stderr", "cancel", "cancel-no-cleanup", "existing",
                          "diagnostics", "normal-lift"};
  for (guint i = 0; i < G_N_ELEMENTS (cases); i++) {
    gchar *name = g_strconcat ("/native-enrol/", cases[i], NULL);
    g_test_add_data_func (name, cases[i], exercise);
    g_free (name);
  }
  int result = g_test_run ();
  g_unlink (provider);
  g_rmdir (bundle);
  g_free (provider);
  g_free (bundle);
  g_free (fixture);
  return result;
}
