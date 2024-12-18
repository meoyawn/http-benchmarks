package bench

import org.sqlite.sqlite3_h.C_POINTER
import org.sqlite.sqlite3_h.SQLITE_OK
import org.sqlite.sqlite3_h.sqlite3_bind_double
import org.sqlite.sqlite3_h.sqlite3_close
import org.sqlite.sqlite3_h.sqlite3_errmsg
import org.sqlite.sqlite3_h.sqlite3_finalize
import org.sqlite.sqlite3_h.sqlite3_open_v2
import org.sqlite.sqlite3_h.sqlite3_prepare_v2
import org.sqlite.sqlite3_h.sqlite3_reset
import org.sqlite.sqlite3_h.sqlite3_step
import java.lang.foreign.Arena
import java.lang.foreign.MemorySegment
import java.nio.file.Path
import kotlin.io.path.absolutePathString

private fun Int.ok(conn: MemorySegment) {
    if (this != SQLITE_OK()) {
        val str = sqlite3_errmsg(conn).getString(0)
        throw RuntimeException("sqlite3_errmsg: $str")
    }
}

private fun MemorySegment.deref(): MemorySegment =
    get(C_POINTER, 0)

class Stmt(private val arena: Arena, private val conn: MemorySegment, private val stmt: MemorySegment) : AutoCloseable {

    fun exec(vararg args: Double): Unit {
        args.forEachIndexed { index, arg ->
            sqlite3_bind_double(stmt, index + 1, arg)
                .ok(conn)
        }

        sqlite3_step(stmt)
        // TODO handle result

        sqlite3_reset(stmt)
            .ok(conn)
    }

    override fun close(): Unit =
        sqlite3_finalize(stmt)
            .ok(conn)
}


class SQLite3(private val arena: Arena, private val conn: MemorySegment) : AutoCloseable {

    companion object {
        fun open(arena: Arena, path: Path, flags: Int): SQLite3 {
            val ptr = arena.allocate(C_POINTER)

            sqlite3_open_v2(arena.allocateFrom(path.absolutePathString()), ptr, flags, MemorySegment.NULL)
                .ok(ptr.deref())

            return SQLite3(arena = arena, conn = ptr.deref())
        }
    }

    fun prepare(sql: String): Stmt {
        val ptr = arena.allocate(C_POINTER)

        sqlite3_prepare_v2(conn, arena.allocateFrom(sql), sql.length, ptr, MemorySegment.NULL)
            .ok(conn)

        return Stmt(arena = arena, conn = conn, stmt = ptr.deref())
    }

    override fun close(): Unit =
        sqlite3_close(conn)
            .ok(conn)
}
