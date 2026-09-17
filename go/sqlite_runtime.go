package main

/*
#include <stdlib.h>
// Resolve against Tailscale's embedded engine; never link a second SQLite.
extern int sqlite3_shutdown(void);
extern int sqlite3_initialize(void);
extern int sqlite3_config(int, ...);

static int configure_sqlite(void) {
    // cgosqlite's package init has initialized SQLite, but no connection exists
    // yet. Configuration must run here, before application or test connections.
    int rc = sqlite3_shutdown();
    if (rc != 0) return rc;
    rc = sqlite3_config(9, 0); // SQLITE_CONFIG_MEMSTATUS
    if (rc != 0) return rc;
    int header_size = 0;
    rc = sqlite3_config(24, &header_size); // SQLITE_CONFIG_PCACHE_HDRSZ
    if (rc != 0) return rc;
    int slot_size = (4096 + header_size + 7) & ~7;
    void *pool = malloc((size_t)slot_size * 1024);
    if (pool == NULL) return 7; // SQLITE_NOMEM
    rc = sqlite3_config(7, pool, slot_size, 1024); // SQLITE_CONFIG_PAGECACHE
    if (rc != 0) { free(pool); return rc; }
    // SQLite retains the pool for process lifetime, with malloc fallback.
    return sqlite3_initialize();
}
*/
import "C"

import "fmt"

func init() {
	if rc := C.configure_sqlite(); rc != 0 {
		panic(fmt.Sprintf("configure SQLite before opening connections: code %d", rc))
	}
}
