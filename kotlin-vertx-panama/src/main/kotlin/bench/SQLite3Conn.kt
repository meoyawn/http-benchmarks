package bench

import org.sqlite.sqlite3_h.C_POINTER
import org.sqlite.sqlite3_h.SQLITE_OK
import org.sqlite.sqlite3_h.sqlite3_bind_double
import org.sqlite.sqlite3_h.sqlite3_bind_int
import org.sqlite.sqlite3_h.sqlite3_bind_text
import org.sqlite.sqlite3_h.sqlite3_close
import org.sqlite.sqlite3_h.sqlite3_errmsg
import org.sqlite.sqlite3_h.sqlite3_exec
import org.sqlite.sqlite3_h.sqlite3_finalize
import org.sqlite.sqlite3_h.sqlite3_open_v2
import org.sqlite.sqlite3_h.sqlite3_prepare_v2
import org.sqlite.sqlite3_h.sqlite3_reset
import org.sqlite.sqlite3_h.sqlite3_step
import java.lang.foreign.Arena
import java.lang.foreign.MemorySegment
import java.nio.file.Path
import kotlin.io.path.absolutePathString

class SQLite3Exception(message: String) : RuntimeException(message)

private fun Int.ok(conn: MemorySegment) {
    if (this != SQLITE_OK()) {
        val str = sqlite3_errmsg(conn).getString(0)
        throw SQLite3Exception(str)
    }
}

class Stmt(
    private val arena: Arena,
    private val conn: MemorySegment,
    private val stmt: MemorySegment,
) : AutoCloseable {

    fun exec(vararg args: Any) {
        args.forEachIndexed { index, arg ->
            val sqlitePos = index + 1

            when (arg) {
                is Boolean ->
                    sqlite3_bind_int(stmt, sqlitePos, if (arg) 1 else 0)

                is Int ->
                    sqlite3_bind_int(stmt, sqlitePos, arg)

                is Double ->
                    sqlite3_bind_double(stmt, sqlitePos, arg)

                is String -> {
                    val cStr = arena.allocateFrom(arg)
                    sqlite3_bind_text(stmt, sqlitePos, cStr, cStr.byteSize().toInt(), MemorySegment.NULL)
                }

                else ->
                    throw IllegalArgumentException("Unsupported argument type: ${arg::class}")
            }.ok(conn)
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

private inline fun Arena.ptrPtr(fn: (MemorySegment) -> Unit): MemorySegment {
    val ptr = allocate(C_POINTER)
    fn(ptr)
    return ptr.get(C_POINTER, 0)
}

class SQLite3Conn(private val arena: Arena, private val conn: MemorySegment) : AutoCloseable {

    companion object {
        fun open(arena: Arena, path: Path, flags: Int): SQLite3Conn {
            val conn = arena.ptrPtr {
                sqlite3_open_v2(arena.allocateFrom(path.absolutePathString()), it, flags, MemorySegment.NULL)
                    .ok(it.get(C_POINTER, 0))
            }

            return SQLite3Conn(arena = arena, conn = conn)
        }
    }

    fun exec(sqlScript: String) {
        val cStr = arena.allocateFrom(sqlScript)
        val err = arena.ptrPtr {
            val callback = MemorySegment.NULL
            val callbackFirstArg = MemorySegment.NULL
            sqlite3_exec(conn, cStr, callback, callbackFirstArg, it)
                .ok(conn)
        }
        if (err != MemorySegment.NULL) {
            throw SQLite3Exception(err.getString(0))
        }
    }

    fun prepare(sql: String): Stmt {
        val cStr = arena.allocateFrom(sql)
        val stmt = arena.ptrPtr {
            sqlite3_prepare_v2(conn, cStr, cStr.byteSize().toInt(), it, MemorySegment.NULL)
                .ok(conn)
        }

        return Stmt(arena = arena, conn = conn, stmt = stmt)
    }

    override fun close(): Unit =
        sqlite3_close(conn)
            .ok(conn)
}
