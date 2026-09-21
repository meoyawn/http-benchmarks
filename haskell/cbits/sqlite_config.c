#include <sqlite3.h>
#include <stdlib.h>

/* Only adapts SQLite's variadic startup configuration ABI on Apple ARM64.
   All queries, bindings, row extraction and transactions live in Haskell. */
int bench_sqlite_configure(void) {
    int rc = sqlite3_config(SQLITE_CONFIG_MEMSTATUS, 0);
    if (rc != SQLITE_OK) return rc;
    int header = 0;
    rc = sqlite3_config(SQLITE_CONFIG_PCACHE_HDRSZ, &header);
    if (rc != SQLITE_OK) return rc;
    int size = (4096 + header + 7) & ~7;
    void *pool = malloc((size_t)size * 1024);
    if (!pool) return SQLITE_NOMEM;
    rc = sqlite3_config(SQLITE_CONFIG_PAGECACHE, pool, size, 1024);
    if (rc != SQLITE_OK) { free(pool); return rc; }
    return sqlite3_initialize();
}

/* direct-sqlite links these optional APIs even though this application never
   calls them. The shared engine deliberately omits all three features. Keep
   them disabled without changing the benchmark's SQLite compiler options. */
int sqlite3_enable_load_extension(sqlite3 *db, int enabled) {
    (void)db; (void)enabled;
    return SQLITE_MISUSE;
}
int sqlite3_enable_shared_cache(int enabled) {
    return enabled ? SQLITE_MISUSE : SQLITE_OK;
}
void *sqlite3_trace(sqlite3 *db, void (*callback)(void *, const char *), void *context) {
    (void)db; (void)callback; (void)context;
    return NULL;
}
