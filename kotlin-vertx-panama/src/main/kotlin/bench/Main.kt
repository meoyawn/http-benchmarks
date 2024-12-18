package bench

import org.sqlite.sqlite3_h.C_POINTER
import org.sqlite.sqlite3_h.SQLITE_OK
import org.sqlite.sqlite3_h.SQLITE_OPEN_READWRITE
import org.sqlite.sqlite3_h.sqlite3_close
import org.sqlite.sqlite3_h.sqlite3_errmsg
import org.sqlite.sqlite3_h.sqlite3_finalize
import org.sqlite.sqlite3_h.sqlite3_open_v2
import org.sqlite.sqlite3_h.sqlite3_prepare
import java.lang.foreign.Arena
import java.lang.foreign.MemorySegment

object Main {

    private fun MemorySegment.get() = get(C_POINTER, 0)

    private fun Int.ok(conn: MemorySegment) {
        if (this != SQLITE_OK()) {
            val str = sqlite3_errmsg(conn).getString(0)
            throw RuntimeException("sqlite3_errmsg: $str")
        }
    }

    private fun Arena.prepare(conn: MemorySegment, sql: String): MemorySegment {
        val stmtPtr = allocate(C_POINTER)

        sqlite3_prepare(
            conn,
            allocateFrom(sql),
            sql.length,
            stmtPtr,
            MemorySegment.NULL,
        ).ok(conn)

        return stmtPtr.get()
    }

    @JvmStatic
    fun main(args: Array<String>) {
        Arena.ofConfined().use {
            val connPtr = it.allocate(C_POINTER)
            sqlite3_open_v2(it.allocateFrom("../db/db.sqlite3"), connPtr, SQLITE_OPEN_READWRITE(), MemorySegment.NULL)
                .ok(connPtr.get())
            val conn = connPtr.get()

            val stmt = it.prepare(conn, sql = "PRAGMA strict = ON")
            sqlite3_finalize(stmt).ok(conn)
            sqlite3_close(conn).ok(conn)
        }
    }
}
