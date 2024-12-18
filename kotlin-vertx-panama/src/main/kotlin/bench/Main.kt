package bench

import org.sqlite.sqlite3_h.SQLITE_OPEN_READWRITE
import java.lang.foreign.Arena
import java.nio.file.Path

object Main {

    @JvmStatic
    fun main(args: Array<String>) {
        Arena.ofConfined().use {
            SQLite3Conn.open(it, Path.of("../db/db.sqlite3"), flags = SQLITE_OPEN_READWRITE()).use { conn ->
                conn.exec(
                    """
                    PRAGMA journal_mode = WAL;
                    PRAGMA synchronous = NORMAL;
                    PRAGMA strict = ON;
                    """
                )

                conn.prepare(sql = "PRAGMA strict = ON").use {

                }
            }
        }
    }
}
