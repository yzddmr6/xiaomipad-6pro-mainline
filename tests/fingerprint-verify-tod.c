/* SPDX-License-Identifier: MIT
 * Real libfprint verify API with synthetic runtime results. No hardware access.
 */
#include "../device/fingerprint/oem/src/fingerprint/libfprint-tod/fpc1264-oem.c"

typedef struct {
  GMainLoop *loop;
  FpDevice *device;
  GError *error;
} Fixture;

static void
initialized (GObject *source, GAsyncResult *result, gpointer data)
{
  Fixture *fixture = data;
  fixture->device = FP_DEVICE (g_async_initable_new_finish (G_ASYNC_INITABLE (source), result, &fixture->error));
  g_main_loop_quit (fixture->loop);
}

static void
fixture_probe (FpDevice *device)
{
  fpi_device_probe_complete (device, "liuqin-fpc1264-oem", "Synthetic verify device", NULL);
}

static void
exercise (gconstpointer data)
{
  const gchar *mode = data;
  Fixture fixture = {0};
  FpDeviceClass *klass = g_type_class_ref (fpi_device_fpc1264_oem_get_type ());
  klass->probe = fixture_probe;
  fixture.loop = g_main_loop_new (NULL, FALSE);
  g_async_initable_new_async (fpi_device_fpc1264_oem_get_type (), G_PRIORITY_DEFAULT,
                             NULL, initialized, &fixture, "fpi-environ", "test", NULL);
  g_main_loop_run (fixture.loop);
  g_assert_no_error (fixture.error);
  g_assert_true (fp_device_open_sync (fixture.device, NULL, &fixture.error));
  g_assert_no_error (fixture.error);
  g_autoptr(FpPrint) print = g_object_ref_sink (fp_print_new (fixture.device));
  fpi_print_set_type (print, FPI_PRINT_RAW);
  GVariant *bytes = g_variant_new_fixed_array (G_VARIANT_TYPE_BYTE, "synthetic", 9, 1);
  g_object_set (print, "fpi-data", g_variant_new ("(u@ay)", 1u, bytes), NULL);
  g_setenv ("FPC_VERIFY_TEST_CASE", mode, TRUE);
  gboolean match = FALSE;
  gboolean ok = fp_device_verify_sync (fixture.device, print, NULL, NULL, NULL,
                                        &match, NULL, &fixture.error);
  if (g_str_equal (mode, "matched") || g_str_equal (mode, "not-matched")) {
    g_assert_true (ok);
    g_assert_no_error (fixture.error);
    g_assert_cmpint (match, ==, g_str_equal (mode, "matched"));
  } else {
    g_assert_false (ok);
    g_assert_false (match);
    if (g_str_equal (mode, "capture-wait") || g_str_equal (mode, "identify-retry"))
      g_assert_error (fixture.error, FP_DEVICE_RETRY, FP_DEVICE_RETRY_GENERAL);
    else
      g_assert_error (fixture.error, FP_DEVICE_ERROR, FP_DEVICE_ERROR_GENERAL);
  }
  g_clear_error (&fixture.error);
  g_assert_true (fp_device_close_sync (fixture.device, NULL, &fixture.error));
  g_assert_no_error (fixture.error);
  g_clear_object (&fixture.device);
  g_main_loop_unref (fixture.loop);
  g_type_class_unref (klass);
}

int
main (int argc, char **argv)
{
  g_autoptr(GError) error = NULL;
  g_autofree gchar *content = NULL;
  gsize length;
  g_assert_cmpuint (geteuid (), ==, 0);
  g_test_init (&argc, &argv, NULL);
  g_log_set_always_fatal (G_LOG_LEVEL_ERROR | G_LOG_LEVEL_CRITICAL);
  g_assert_true (g_file_get_contents (g_getenv ("FPC_VERIFY_FIXTURE"), &content, &length, &error));
  g_autofree gchar *bundle = g_dir_make_tmp ("fpc-verify-api-test-XXXXXX", &error);
  g_assert_no_error (error);
  g_autofree gchar *runtime = g_build_filename (bundle, "oem_runtime.py", NULL);
  g_assert_true (g_file_set_contents (runtime, content, length, &error));
  g_assert_cmpint (g_chmod (runtime, 0600), ==, 0);
  g_setenv ("LIUQIN_FPC_OEM_RUNTIME", bundle, TRUE);
  const gchar *cases[] = {"capture-wait", "transport-error", "unknown-status",
                          "missing-cleanup", "timed-out", "cancelled",
                          "identify-retry", "matched", "not-matched"};
  for (guint i = 0; i < G_N_ELEMENTS (cases); i++) {
    g_autofree gchar *name = g_strconcat ("/native-verify/", cases[i], NULL);
    g_test_add_data_func (name, cases[i], exercise);
  }
  int result = g_test_run ();
  g_unlink (runtime);
  g_rmdir (bundle);
  return result;
}
