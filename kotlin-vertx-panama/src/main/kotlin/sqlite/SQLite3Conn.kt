package sqlite

import org.jetbrains.annotations.Range
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
import org.sqlite.sqlite3_h.sqlite3_open
import org.sqlite.sqlite3_h.sqlite3_prepare_v3
import org.sqlite.sqlite3_h.sqlite3_reset
import org.sqlite.sqlite3_h.sqlite3_step
import java.lang.foreign.Arena
import java.lang.foreign.MemorySegment
import java.nio.file.Path
import kotlin.contracts.ExperimentalContracts
import kotlin.contracts.InvocationKind
import kotlin.contracts.contract
import kotlin.io.path.absolutePathString

class SQLite3Exception(message: String) : RuntimeException(message)

fun errMsg(conn: MemorySegment): String =
    sqlite3_errmsg(conn).getString(0)

fun Int.ok(conn: MemorySegment) {
    if (this != SQLITE_OK()) {
        throw SQLite3Exception(errMsg(conn))
    }
}

@OptIn(ExperimentalContracts::class)
class Statement(val conn: MemorySegment, val stmt: MemorySegment) : AutoCloseable {

    val parameterCount = sqlite3_bind_parameter_count(stmt)

    /**
     * > The leftmost SQL parameter has an index of 1
     *
     * [Binding values](https://www.sqlite.org/c3ref/bind_blob.html)
     */
    fun bind(arena: Arena, index: @Range(from = 1, to = 32766) Int, arg: Any?): Unit =
        when (arg) {
            null ->
                sqlite3_bind_null(stmt, index)

            is Boolean ->
                sqlite3_bind_int(stmt, index, if (arg) 1 else 0)

            is Int ->
                sqlite3_bind_int(stmt, index, arg)

            is Long ->
                sqlite3_bind_int64(stmt, index, arg)

            is Double ->
                sqlite3_bind_double(stmt, index, arg)

            is Float ->
                sqlite3_bind_double(stmt, index, arg.toDouble())

            is String -> {
                val cStr = arena.allocateFrom(arg)
                sqlite3_bind_text(stmt, index, cStr, cStr.byteSize().toInt(), SQLITE_STATIC())
            }

            else ->
                throw IllegalArgumentException("Unsupported argument type: ${arg::class.java}")
        }.ok(conn)

    inline fun <T> bindAndReset(args: Array<Any?>, stepFn: (Statement) -> T): T {
        contract {
            callsInPlace(stepFn, InvocationKind.EXACTLY_ONCE)
        }

        require(args.size == parameterCount) { "Expected $parameterCount arguments, got ${args.size}" }

        return Arena.ofConfined().use { arena ->
            args.forEachIndexed { index, arg -> bind(arena, index = index + 1, arg = arg) }

            try {
                stepFn(this)
            } finally {
                sqlite3_reset(stmt)
                    .ok(conn)
            }
        }
    }

    fun exec(args: Array<Any?> = emptyArray()): Unit =
        bindAndReset(args) {
            when (sqlite3_step(stmt)) {
                SQLITE_ROW(), SQLITE_DONE() -> {}
                else -> throw SQLite3Exception(errMsg(conn))
            }
        }

    /**
     * Binds arguments and maps the first row of the result set to a value
     *
     * @param args arguments to bind
     * @param mapFn function to map the first row of the result set
     * @return the value returned by [mapFn]
     */
    inline fun <T> queryRow(args: Array<Any?> = emptyArray(), mapFn: (Statement) -> T): T {
        contract {
            callsInPlace(mapFn, InvocationKind.EXACTLY_ONCE)
        }

        return bindAndReset(args) {
            when (sqlite3_step(stmt)) {
                SQLITE_ROW() -> mapFn(this)
                else -> throw SQLite3Exception(errMsg(conn))
            }
        }
    }

    private val columnCount = sqlite3_column_count(stmt)

    /**
     * > The leftmost column of the result set has the index 0
     *
     * [Result values](https://www.sqlite.org/c3ref/column_blob.html)
     */
    private fun colIndex(i: @Range(from = 0, to = 32768) Int): Int {
        if (i !in 0..<columnCount) {
            throw IndexOutOfBoundsException("Column: $i, count: $columnCount. Note that columns start at 0")
        }

        return i
    }

    fun getLong(i: @Range(from = 0, to = 32768) Int): Long =
        sqlite3_column_int64(stmt, colIndex(i))

    fun getString(i: @Range(from = 0, to = 32768) Int): String =
        sqlite3_column_text(stmt, colIndex(i)).getString(0)

    fun getDouble(i: @Range(from = 0, to = 32768) Int): Double =
        sqlite3_column_double(stmt, colIndex(i))

    fun getInt(i: @Range(from = 0, to = 32768) Int): Int =
        sqlite3_column_int(stmt, colIndex(i))

    fun getBool(i: @Range(from = 0, to = 32768) Int): Boolean =
        getInt(i) != 0

    override fun close(): Unit =
        sqlite3_finalize(stmt)
            .ok(conn)
}

/**
 * @param arena arena where connection and statements live
 * @param conn SQLite3 connection pointer
 */
@OptIn(ExperimentalContracts::class)
class SQLite3Conn private constructor(private val arena: Arena, private val conn: MemorySegment) : AutoCloseable {

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

        private inline fun Arena.ptrPtr(fn: (MemorySegment) -> Unit): MemorySegment {
            contract {
                callsInPlace(fn, InvocationKind.EXACTLY_ONCE)
            }

            val ptr = allocate(C_POINTER)
            fn(ptr)
            return ptr.get(C_POINTER, 0)
        }
    }

    /**
     * Executes SQL script
     *
     * @param sqlScript SQL script to execute
     */
    fun exec(sqlScript: String): Unit =
        Arena.ofConfined().use { arena ->
            val cStr = arena.allocateFrom(sqlScript)
            val callback = MemorySegment.NULL
            val cbFirstArg = MemorySegment.NULL
            val errMsg = MemorySegment.NULL
            sqlite3_exec(conn, cStr, callback, cbFirstArg, errMsg)
                .ok(conn)
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
        contract {
            callsInPlace(fn, InvocationKind.EXACTLY_ONCE)
        }

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
