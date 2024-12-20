package sqlite

import org.sqlite.sqlite3_h.C_POINTER
import org.sqlite.sqlite3_h.SQLITE_DONE
import org.sqlite.sqlite3_h.SQLITE_OK
import org.sqlite.sqlite3_h.SQLITE_PREPARE_PERSISTENT
import org.sqlite.sqlite3_h.SQLITE_ROW
import org.sqlite.sqlite3_h.SQLITE_STATIC
import org.sqlite.sqlite3_h.sqlite3_bind_double
import org.sqlite.sqlite3_h.sqlite3_bind_int
import org.sqlite.sqlite3_h.sqlite3_bind_int64
import org.sqlite.sqlite3_h.sqlite3_bind_null
import org.sqlite.sqlite3_h.sqlite3_bind_parameter_count
import org.sqlite.sqlite3_h.sqlite3_bind_text
import org.sqlite.sqlite3_h.sqlite3_close
import org.sqlite.sqlite3_h.sqlite3_column_count
import org.sqlite.sqlite3_h.sqlite3_column_double
import org.sqlite.sqlite3_h.sqlite3_column_int
import org.sqlite.sqlite3_h.sqlite3_column_int64
import org.sqlite.sqlite3_h.sqlite3_column_text
import org.sqlite.sqlite3_h.sqlite3_errmsg
import org.sqlite.sqlite3_h.sqlite3_exec
import org.sqlite.sqlite3_h.sqlite3_finalize
import org.sqlite.sqlite3_h.sqlite3_free
import org.sqlite.sqlite3_h.sqlite3_open
import org.sqlite.sqlite3_h.sqlite3_prepare_v3
import org.sqlite.sqlite3_h.sqlite3_reset
import org.sqlite.sqlite3_h.sqlite3_step
import java.lang.foreign.Arena
import java.lang.foreign.MemorySegment
import java.nio.file.Path
import kotlin.io.path.absolutePathString

class SQLite3Exception(message: String) : RuntimeException(message)

fun errMsg(conn: MemorySegment): String =
    sqlite3_errmsg(conn).getString(0)

fun Int.ok(conn: MemorySegment) {
    if (this != SQLITE_OK()) {
        throw SQLite3Exception(errMsg(conn))
    }
}

class Statement(
    val conn: MemorySegment,
    val stmt: MemorySegment,
) : AutoCloseable {

    fun bind(arena: Arena, index: Int, arg: Any?) {
        val sqlitePos = index + 1

        when (arg) {
            null ->
                sqlite3_bind_null(stmt, sqlitePos)

            is Boolean ->
                sqlite3_bind_int(stmt, sqlitePos, if (arg) 1 else 0)

            is Int ->
                sqlite3_bind_int(stmt, sqlitePos, arg)

            is Long ->
                sqlite3_bind_int64(stmt, sqlitePos, arg)

            is Double ->
                sqlite3_bind_double(stmt, sqlitePos, arg)

            is String -> {
                val cStr = arena.allocateFrom(arg)
                sqlite3_bind_text(stmt, sqlitePos, cStr, cStr.byteSize().toInt(), SQLITE_STATIC())
            }

            else ->
                throw IllegalArgumentException("Unsupported argument type: ${arg::class.java}")
        }.ok(conn)
    }

    inline fun <T> bindReset(args: Array<Any?>, fn: (Statement) -> T): T {
        val count = sqlite3_bind_parameter_count(stmt)
        require(args.size == count) { "Expected $count arguments, got ${args.size}" }

        return Arena.ofConfined().use { arena ->
            args.forEachIndexed { index, arg -> bind(arena, index, arg) }

            try {
                fn(this)
            } finally {
                sqlite3_reset(stmt)
                    .ok(conn)
            }
        }
    }

    fun exec(args: Array<Any?> = emptyArray()): Unit =
        bindReset(args) {
            when (sqlite3_step(stmt)) {
                SQLITE_ROW(), SQLITE_DONE() -> {}
                else -> throw SQLite3Exception(errMsg(conn))
            }
        }

    private fun requireCol(i: Int): Int {
        val count = sqlite3_column_count(stmt)
        require(i < count) { "$i must be less than $count. Columns start from 0" }
        return i
    }

    fun getLong(i: Int): Long =
        sqlite3_column_int64(stmt, requireCol(i))

    fun getString(i: Int): String =
        sqlite3_column_text(stmt, requireCol(i)).getString(0)

    fun getDouble(i: Int): Double =
        sqlite3_column_double(stmt, requireCol(i))

    fun getInt(i: Int): Int =
        sqlite3_column_int(stmt, requireCol(i))

    fun getBool(i: Int): Boolean =
        getInt(i) != 0

    inline fun <T> queryRow(args: Array<Any?> = emptyArray(), fn: (Statement) -> T): T =
        bindReset(args) {
            if (sqlite3_step(stmt) != SQLITE_ROW()) {
                throw SQLite3Exception(errMsg(conn))
            }
            fn(this)
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

    private val cache = HashMap<String, Statement>()

    companion object {
        fun open(arena: Arena, path: Path): SQLite3Conn {
            val conn = arena.ptrPtr {
                sqlite3_open(arena.allocateFrom(path.absolutePathString()), it)
                    .ok(it.get(C_POINTER, 0))
            }

            return SQLite3Conn(arena = arena, conn = conn)
        }

        fun openMemory(arena: Arena): SQLite3Conn {
            val conn = arena.ptrPtr {
                sqlite3_open(arena.allocateFrom(":memory:"), it)
                    .ok(it.get(C_POINTER, 0))
            }

            return SQLite3Conn(arena = arena, conn = conn)
        }
    }

    fun exec(sqlScript: String): Unit =
        Arena.ofConfined().use { arena ->
            val err = arena.ptrPtr {
                val cStr = arena.allocateFrom(sqlScript)
                val callback = MemorySegment.NULL
                val callbackFirstArg = MemorySegment.NULL
                sqlite3_exec(conn, cStr, callback, callbackFirstArg, it)
                    .ok(conn)
            }
            if (err != MemorySegment.NULL) {
                val msg = err.getString(0).also { sqlite3_free(err) }
                throw SQLite3Exception(msg)
            }
        }

    fun prepare(sql: String): Statement {
        val stmt = arena.ptrPtr {
            val cStr = arena.allocateFrom(sql)
            /**
             * > If the caller knows that the supplied string is nul-terminated, then there is a small performance advantage to passing an nByte parameter
             *
             * https://www.sqlite.org/c3ref/prepare.html
             */
            sqlite3_prepare_v3(conn, cStr, cStr.byteSize().toInt(), SQLITE_PREPARE_PERSISTENT(), it, MemorySegment.NULL)
                .ok(conn)
        }

        return Statement(conn = conn, stmt = stmt)
    }

    fun prepareCached(sql: String): Statement =
        cache.getOrPut(sql) { prepare(sql) }

    enum class TxMode {
        IMMEDIATE,
        DEFERRED,
        EXCLUSIVE,
    }

    inline fun <T> transact(mode: TxMode, fn: SQLite3Conn.() -> T): T {
        prepareCached("BEGIN $mode").exec()
        return try {
            fn(this).also {
                prepareCached("COMMIT").exec()
            }
        } catch (e: Exception) {
            prepareCached("ROLLBACK").exec()
            throw e
        }
    }

    override fun close() {
        for (stmt in cache.values) {
            stmt.close()
        }
        cache.clear()

        sqlite3_close(conn)
            .ok(conn)
    }
}
