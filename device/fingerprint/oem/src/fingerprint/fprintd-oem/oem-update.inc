/* SPDX-License-Identifier: GPL-2.0-or-later
 * Included by fprintd file_storage.c after its private path helpers.
 * Commit OEM adaptation before VerifyStatus can authorize a caller.
 */
#include <fcntl.h>
#include <sys/file.h>

static gboolean
oem_print_metadata_equal (FpPrint *a, FpPrint *b)
{
  return fp_print_get_finger (a) == fp_print_get_finger (b) &&
         g_strcmp0 (fp_print_get_username (a), fp_print_get_username (b)) == 0 &&
         g_strcmp0 (fp_print_get_driver (a), fp_print_get_driver (b)) == 0 &&
         g_strcmp0 (fp_print_get_device_id (a), fp_print_get_device_id (b)) == 0;
}

gboolean
file_storage_print_data_update (FpPrint *original, FpPrint *updated,
                               GCancellable *cancellable, GError **error)
{
  g_autofree char *base = NULL;
  g_autofree char *path = NULL;
  g_autofree char *directory = NULL;
  g_autofree char *name = NULL;
  g_autofree guint8 *old_bytes = NULL;
  g_autofree guint8 *new_bytes = NULL;
  g_autoptr(FpPrint) current = NULL;
  struct stat info;
  char temporary[48] = {}, previous[48] = {};
  int dirfd = -1, lockfd = -1, oldfd = -1, newfd = -1;
  gboolean created = FALSE, committed = FALSE, saved = FALSE, backup = FALSE;
  gsize length;

  if (geteuid () != 0 || !oem_print_metadata_equal (original, updated) ||
      g_strcmp0 (fp_print_get_driver (updated), "fpc1264_oem") != 0 ||
      !FP_FINGER_IS_VALID (fp_print_get_finger (updated)))
    goto invalid;
  base = file_storage_get_basestore_for_username (fp_print_get_username (updated));
  path = __get_path_to_print (fp_print_get_driver (updated), fp_print_get_device_id (updated),
                              fp_print_get_finger (updated), base);
  directory = g_path_get_dirname (path);
  name = g_path_get_basename (path);
  dirfd = open (directory, O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
  if (dirfd < 0 || fstat (dirfd, &info) || info.st_uid != 0 || (info.st_mode & 0022))
    goto invalid;
  lockfd = openat (dirfd, ".operation.lock", O_RDWR | O_CREAT | O_CLOEXEC | O_NOFOLLOW, 0600);
  if (lockfd < 0 || fstat (lockfd, &info) || !S_ISREG (info.st_mode) ||
      info.st_uid != 0 || (info.st_mode & 0777) != 0600 ||
      flock (lockfd, LOCK_EX | LOCK_NB))
    goto io_error;
  oldfd = openat (dirfd, name, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
  if (oldfd < 0 || fstat (oldfd, &info) || !S_ISREG (info.st_mode) ||
      info.st_uid != 0 || (info.st_mode & 0777) != 0600 ||
      info.st_size <= 0 || info.st_size > 17u * 1024u * 1024u)
    goto invalid;
  old_bytes = g_malloc (info.st_size);
  for (gsize offset = 0; offset < (gsize) info.st_size;) {
    ssize_t count = read (oldfd, old_bytes + offset, info.st_size - offset);
    if (count < 0 && errno == EINTR)
      continue;
    if (count <= 0)
      goto io_error;
    offset += count;
  }
  current = fp_print_deserialize (old_bytes, info.st_size, error);
  if (!current)
    goto out;
  if (!oem_print_metadata_equal (current, original) || !fp_print_equal (current, original))
    goto invalid;
  if (!fp_print_serialize (updated, &new_bytes, &length, error))
    goto out;
  if (!length || length > 17u * 1024u * 1024u)
    goto invalid;
  g_snprintf (temporary, sizeof temporary, ".update-%08x-%08x", g_random_int (), g_random_int ());
  newfd = openat (dirfd, temporary, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0600);
  if (newfd < 0)
    goto io_error;
  created = TRUE;
  for (gsize offset = 0; offset < length;) {
    ssize_t count = write (newfd, new_bytes + offset, length - offset);
    if (count < 0 && errno == EINTR)
      continue;
    if (count <= 0)
      goto io_error;
    offset += count;
  }
  if (fsync (newfd))
    goto io_error;
  /* Preserve a durable link to the exact old file before the atomic commit.
   * Discovery ignores these multi-character names. An uncertain commit keeps
   * the old link for recovery instead of claiming authentication succeeded. */
  g_snprintf (previous, sizeof previous, ".previous-%08x-%08x", g_random_int (), g_random_int ());
  if (linkat (dirfd, name, dirfd, previous, 0))
    goto io_error;
  backup = TRUE;
  if (fsync (dirfd))
    goto io_error;
  if (g_cancellable_set_error_if_cancelled (cancellable, error))
    goto out;
  if (renameat (dirfd, temporary, dirfd, name))
    goto io_error;
  committed = TRUE;
  if (fsync (dirfd))
    goto io_error;
  saved = TRUE;
  /* A failure removing the backup does not undo an already durable update. */
  if (!unlinkat (dirfd, previous, 0)) {
    backup = FALSE;
    (void) fsync (dirfd);
  }
  goto out;

invalid:
  g_set_error_literal (error, G_IO_ERROR, G_IO_ERROR_INVALID_DATA,
                       "The stored OEM template changed or has invalid private metadata");
  goto out;
io_error:
  g_set_error_literal (error, G_IO_ERROR, G_IO_ERROR_FAILED,
                       "The OEM template update was not committed durably; previous template retained");
out:
  if (newfd >= 0)
    close (newfd);
  if (oldfd >= 0)
    close (oldfd);
  if (dirfd >= 0) {
    if (committed && !saved && backup && !renameat (dirfd, previous, dirfd, name)) {
      committed = FALSE;
      backup = FALSE;
      (void) fsync (dirfd);
    }
    if (!committed && created)
      (void) unlinkat (dirfd, temporary, 0);
    if (!committed && backup)
      (void) unlinkat (dirfd, previous, 0);
  }
  if (lockfd >= 0)
    close (lockfd);
  if (dirfd >= 0)
    close (dirfd);
  return saved;
}
