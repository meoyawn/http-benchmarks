package sqlite

import org.sqlite.sqlite3_h.*
import java.lang.foreign.ValueLayout
import java.lang.foreign.Arena

/** Shared benchmark settings in db/sqlite-config.json, before any connection opens. */
object SQLiteRuntime {
    init {
        NativeLibrary.load()
        val memstatus = if (java.lang.Boolean.getBoolean("sqlite.memstatus")) 1 else 0
        check(sqlite3_config.makeInvoker(ValueLayout.JAVA_INT).apply(SQLITE_CONFIG_MEMSTATUS(), memstatus) == SQLITE_OK()) {
            "SQLite must be configured before its first connection opens"
        }
        if (System.getProperty("sqlite.pagecache", "true").toBoolean()) {
            Arena.ofConfined().use { temporary ->
                val header = temporary.allocate(ValueLayout.JAVA_INT)
                check(sqlite3_config.makeInvoker(C_POINTER).apply(SQLITE_CONFIG_PCACHE_HDRSZ(), header) == SQLITE_OK())
                val slotSize = (4096 + header.get(ValueLayout.JAVA_INT, 0) + 7) and -8
                // SQLite retains this process-wide pool until shutdown. It falls
                // back to malloc when full or when a database uses larger pages.
                val pages = Arena.global().allocate(slotSize.toLong() * 1024, 8)
                check(sqlite3_config.makeInvoker(C_POINTER, ValueLayout.JAVA_INT, ValueLayout.JAVA_INT)
                    .apply(SQLITE_CONFIG_PAGECACHE(), pages, slotSize, 1024) == SQLITE_OK())
            }
        }
        check(sqlite3_initialize() == SQLITE_OK()) { "SQLite initialization failed" }
    }

    fun initialize() = Unit
}
