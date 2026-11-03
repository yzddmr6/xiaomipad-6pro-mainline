/* SPDX-License-Identifier: LGPL-2.1-or-later
 * Extract the opaque OEM database from an fprintd FpPrint file.
 *
 * fprintd stores an FP3 container; the OEM TA client needs the raw database
 * that libfprint keeps in the print's fpi-data. This tool never prints,
 * rewrites or persists template bytes outside the requested output file.
 */
#include <fprint.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define MAX_CONTAINER (16u * 1024u * 1024u)
#define MAX_DATABASE  (16u * 1024u * 1024u)
#define DATABASE_HEADER 84u

static const char *finger_names[] = {
  NULL, "left-thumb", "left-index-finger", "left-middle-finger",
  "left-ring-finger", "left-little-finger", "right-thumb", "right-index-finger",
  "right-middle-finger", "right-ring-finger", "right-little-finger"
};

static int
parse_finger (const char *text)
{
  char *end;
  long value;

  if (!text || !*text)
    return 0;
  for (int i = FP_FINGER_FIRST; i <= FP_FINGER_LAST; i++)
    if (!strcmp (text, finger_names[i]))
      return i;
  errno = 0;
  value = strtol (text, &end, 10);
  if (errno || end == text || *end || value < FP_FINGER_FIRST || value > FP_FINGER_LAST)
    return 0;
  return (int) value;
}

static int
fail (const char *phase, int code)
{
  fprintf (stderr, "oem_extract=FAILED phase=%s errno=%d\n", phase, errno);
  return code;
}

int
main (int argc, char **argv)
{
  struct stat info;
  guchar *container = NULL;
  gsize length = 0, database_length = 0;
  FpPrint *print = NULL;
  GError *error = NULL;
  GVariant *data = NULL, *bytes = NULL;
  const guint8 *database = NULL;
  guint32 version = 0, header_payload = 0;
  int finger, input = -1, output = -1, result = 1;
  gboolean created = FALSE;

  if (argc < 6 || argc > 7 || strcmp (argv[1], "--extract") || geteuid () != 0)
    {
      fprintf (stderr, "usage (root): fpc-oem-extract --extract LINUX_USERNAME FINGER "
                       "PRINT_FILE OUTPUT_DATABASE [EXPECTED_BYTES]\n");
      return 2;
    }
  finger = parse_finger (argv[3]);
  if (!finger)
    return fail ("finger_name", 2);

  input = open (argv[4], O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
  if (input < 0)
    return fail ("open_print", 3);
  if (fstat (input, &info) || !S_ISREG (info.st_mode) || info.st_uid != 0 || info.st_gid != 0 ||
      (info.st_mode & 0777) != 0600 || info.st_size <= 0 || info.st_size > (off_t) MAX_CONTAINER)
    return fail ("print_metadata", 3);
  length = info.st_size;
  container = g_malloc (length);
  for (gsize offset = 0; offset < length;)
    {
      ssize_t count = read (input, container + offset, length - offset);
      if (count < 0 && errno == EINTR)
        continue;
      if (count <= 0)
        return fail ("read_print", 3);
      offset += count;
    }
  close (input);
  input = -1;

  print = fp_print_deserialize (container, length, &error);
  if (!print)
    return fail ("deserialize", 4);
  if (g_strcmp0 (fp_print_get_driver (print), "fpc1264_oem") ||
      g_strcmp0 (fp_print_get_username (print), argv[2]) ||
      (int) fp_print_get_finger (print) != finger)
    return fail ("print_metadata_mismatch", 5);

  g_object_get (print, "fpi-data", &data, NULL);
  if (!data || !g_variant_is_of_type (data, G_VARIANT_TYPE ("(uay)")))
    return fail ("fpi_data", 6);
  g_variant_get (data, "(u@ay)", &version, &bytes);
  if (version != 1)
    return fail ("fpi_data_version", 6);
  database = g_variant_get_fixed_array (bytes, &database_length, 1);
  if (database_length <= DATABASE_HEADER || database_length > MAX_DATABASE)
    return fail ("database_size", 7);
  memcpy (&header_payload, database, sizeof (header_payload));
  if ((gsize) GUINT32_FROM_LE (header_payload) + DATABASE_HEADER != database_length)
    return fail ("database_header", 7);
  if (argc == 7)
    {
      char *end;
      unsigned long long expected;
      errno = 0;
      expected = strtoull (argv[6], &end, 10);
      if (errno || end == argv[6] || *end || expected != database_length)
        return fail ("expected_size", 8);
    }

  output = open (argv[5], O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0600);
  if (output < 0)
    return fail ("create_output", 9);
  created = TRUE;
  for (gsize offset = 0; offset < database_length;)
    {
      ssize_t count = write (output, database + offset, database_length - offset);
      if (count < 0 && errno == EINTR)
        continue;
      if (count <= 0)
        goto write_failed;
      offset += count;
    }
  if (fsync (output))
    goto write_failed;
  if (close (output))
    {
      output = -1;
      goto write_failed;
    }
  output = -1;
  printf ("oem_extract=OK container_bytes=%zu raw_bytes=%zu header_bytes=%u payload_bytes=%u "
          "fpi_version=%u finger=%d\n", length, database_length, DATABASE_HEADER,
          GUINT32_FROM_LE (header_payload), version, finger);
  result = 0;
  goto out;

write_failed:
  result = 9;
  fprintf (stderr, "oem_extract=FAILED phase=write_output errno=%d\n", errno);
out:
  if (output >= 0)
    close (output);
  if (result && created)
    unlink (argv[5]);
  if (bytes)
    g_variant_unref (bytes);
  if (data)
    g_variant_unref (data);
  if (print)
    g_object_unref (print);
  if (error)
    g_error_free (error);
  if (container)
    g_free (container);
  return result;
}
