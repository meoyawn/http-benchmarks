package bench

import org.sqlite.sqlite3_h.*
import sqlite.SQLite3Exception
import sqlite.SQLiteRuntime
import java.lang.foreign.Arena
import java.lang.foreign.MemorySegment
import java.lang.foreign.ValueLayout
import java.nio.charset.StandardCharsets.UTF_8
import java.nio.file.Path
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit
import java.util.concurrent.FutureTask
import java.util.concurrent.RejectedExecutionException

/** A single owner for SQLite and its prepared statements. No per-statement arenas. */
class PostStore(path: Path) : AutoCloseable {
    private val arena = run { SQLiteRuntime.initialize(); Arena.ofConfined() }
    private var textArena = Arena.ofConfined()
    private var text = textArena.allocate(4096)
    private var db = MemorySegment.NULL
    private val statements = ArrayList<MemorySegment>()
    private var failed: Exception? = null
    private val begin: MemorySegment
    private val user: MemorySegment
    private val post: MemorySegment
    private val commit: MemorySegment
    private val rollback: MemorySegment

    init {
        try {
            val out = arena.allocate(C_POINTER)
            val rc = sqlite3_open_v2(arena.allocateFrom(path.toAbsolutePath().toString()), out,
                SQLITE_OPEN_READWRITE() or SQLITE_OPEN_NOMUTEX(), MemorySegment.NULL)
            db = out.get(C_POINTER, 0)
            check(rc)
            script("PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA foreign_keys=ON; " +
                "PRAGMA busy_timeout=10000; PRAGMA cache_size=-2000; PRAGMA wal_autocheckpoint=1000; " +
                "PRAGMA temp_store=MEMORY; PRAGMA mmap_size=0; PRAGMA optimize=0x10002;")
            begin = prepare("BEGIN IMMEDIATE")
            user = prepare("INSERT OR IGNORE INTO users (email) VALUES (?)")
            post = prepare("INSERT INTO posts (content, user_id) SELECT ?, id FROM users WHERE email IS ? " +
                "RETURNING id, user_id, content, created_at, updated_at")
            commit = prepare("COMMIT")
            rollback = prepare("ROLLBACK")
        } catch (e: Throwable) {
            close()
            throw e
        }
    }

    private fun check(rc: Int) {
        if (rc != SQLITE_OK()) throw SQLite3Exception("SQLite $rc: " +
            if (db == MemorySegment.NULL) "cannot open database" else sqlite3_errmsg(db).getString(0))
    }

    private fun prepare(sql: String): MemorySegment {
        val out = arena.allocate(C_POINTER)
        val source = arena.allocateFrom(sql)
        check(sqlite3_prepare_v3(db, source, source.byteSize().toInt(), SQLITE_PREPARE_PERSISTENT(), out, MemorySegment.NULL))
        return out.get(C_POINTER, 0).also(statements::add)
    }

    private fun script(sql: String) {
        check(sqlite3_exec(db, arena.allocateFrom(sql), MemorySegment.NULL, MemorySegment.NULL, MemorySegment.NULL))
    }

    private fun execute(stmt: MemorySegment) {
        val rc = sqlite3_step(stmt)
        val reset = sqlite3_reset(stmt)
        if (rc != SQLITE_DONE()) check(rc)
        check(reset)
    }

    fun create(request: NewPost): Post {
        failed?.let { throw it }
        val email = request.email.toByteArray(UTF_8)
        val content = request.content.toByteArray(UTF_8)
        val required = email.size.toLong() + content.size + 2
        if (required > text.byteSize()) {
            // Bindings were cleared before the previous call returned.
            textArena.close()
            textArena = Arena.ofConfined()
            text = textArena.allocate(maxOf(required, text.byteSize() * 2))
        }
        val emailPtr = text.asSlice(0, email.size.toLong() + 1)
        val contentPtr = text.asSlice(email.size.toLong() + 1, content.size.toLong() + 1)
        MemorySegment.copy(email, 0, emailPtr, ValueLayout.JAVA_BYTE, 0, email.size)
        MemorySegment.copy(content, 0, contentPtr, ValueLayout.JAVA_BYTE, 0, content.size)
        emailPtr.set(ValueLayout.JAVA_BYTE, email.size.toLong(), 0)
        contentPtr.set(ValueLayout.JAVA_BYTE, content.size.toLong(), 0)
        execute(begin)
        try {
            check(sqlite3_bind_text(user, 1, emailPtr, email.size, SQLITE_STATIC()))
            execute(user)
            check(sqlite3_bind_text(post, 1, contentPtr, content.size, SQLITE_STATIC()))
            check(sqlite3_bind_text(post, 2, emailPtr, email.size, SQLITE_STATIC()))
            val rc = sqlite3_step(post)
            if (rc != SQLITE_ROW()) throw SQLite3Exception("INSERT RETURNING: ${sqlite3_errmsg(db).getString(0)} ($rc)")
            val resultContent = sqlite3_column_text(post, 2)
            val result = Post(
                id = sqlite3_column_int64(post, 0),
                user_id = sqlite3_column_int64(post, 1),
                content = String(resultContent.reinterpret(sqlite3_column_bytes(post, 2).toLong()).toArray(ValueLayout.JAVA_BYTE), UTF_8),
                created_at = sqlite3_column_int64(post, 3),
                updated_at = sqlite3_column_int64(post, 4),
            )
            execute(post) // Finish RETURNING and reset before committing.
            execute(commit)
            return result
        } catch (e: Exception) {
            sqlite3_reset(user)
            sqlite3_reset(post)
            try {
                execute(rollback)
            } catch (rollbackError: Exception) {
                e.addSuppressed(rollbackError)
                failed = e
            }
            throw e
        } finally {
            sqlite3_clear_bindings(user)
            sqlite3_clear_bindings(post)
        }
    }

    override fun close() {
        statements.forEach { sqlite3_finalize(it) }
        statements.clear()
        if (db != MemorySegment.NULL) sqlite3_close(db)
        db = MemorySegment.NULL
        textArena.close()
        arena.close()
    }
}

/** Keep the writer busy without coroutine rendezvous or an allocation per reply channel. */
class PostWriter(path: Path) : AutoCloseable {
    private val lifecycle = Any()
    private var closing: FutureTask<Unit>? = null
    private val executor = ThreadPoolExecutor(1, 1, 0, TimeUnit.MILLISECONDS,
        ArrayBlockingQueue(1024), { task -> Thread(task, "sqlite-writer") })
    private val store: PostStore = try {
        executor.submit<PostStore> { PostStore(path) }.get()
    } catch (e: Exception) {
        executor.shutdown()
        throw e
    }

    fun submit(request: NewPost, done: (Post?, Exception?) -> Unit) {
        synchronized(lifecycle) {
            if (closing != null) throw RejectedExecutionException("SQLite writer is closed")
            executor.execute {
                var failure: Exception? = null
                val result = try { store.create(request) } catch (e: Exception) {
                    failure = e
                    null
                }
                try {
                    done(result, failure)
                } catch (e: Exception) {
                    // A disconnected client must not kill the confined arena's owner.
                    System.getLogger(PostWriter::class.java.name).log(System.Logger.Level.WARNING, "write response failed", e)
                }
            }
        }
    }

    override fun close() {
        val task = synchronized(lifecycle) {
            closing ?: FutureTask<Unit> { store.close() }.also {
                closing = it
                // Unlike submit(), put() also drains a full queue during shutdown.
                executor.queue.put(it)
                executor.shutdown()
            }
        }
        try {
            task.get()
        } finally {
            check(executor.awaitTermination(30, TimeUnit.SECONDS)) { "SQLite writer did not stop" }
        }
    }
}
