#include <caml/mlvalues.h>
#include <sqlite3.h>
#include <stdlib.h>

/* Startup only, before connections/domains exist. SQLite retains the optional
   pool for the process lifetime, with its normal malloc fallback when full.
   This changes allocation, not SQL, transactions, durability or checkpoints. */
CAMLprim value bench_sqlite_configure(value mode_value) {
  int mode = Int_val(mode_value);
  if (mode == 0) return Val_int(sqlite3_initialize());
  int rc = sqlite3_config(SQLITE_CONFIG_MEMSTATUS, 0);
  if (rc != SQLITE_OK) return Val_int(rc);
  if (mode == 2) {
    int header_size = 0;
    rc = sqlite3_config(SQLITE_CONFIG_PCACHE_HDRSZ, &header_size);
    if (rc != SQLITE_OK) return Val_int(rc);
    int slot_size = (4096 + header_size + 7) & ~7;
    void *pool = malloc((size_t)slot_size * 1024);
    if (pool == NULL) return Val_int(SQLITE_NOMEM);
    rc = sqlite3_config(SQLITE_CONFIG_PAGECACHE, pool, slot_size, 1024);
    if (rc != SQLITE_OK) {
      free(pool);
      return Val_int(rc);
    }
  }
  return Val_int(sqlite3_initialize());
}
