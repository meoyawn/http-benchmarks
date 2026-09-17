package bench

import org.assertj.core.api.Assertions.assertThat
import org.assertj.core.api.Assertions.assertThatThrownBy
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import sqlite.SQLite3Conn
import sqlite.SQLite3Exception
import java.lang.foreign.Arena
import java.nio.file.Files
import java.nio.file.Path
import java.util.concurrent.CompletableFuture
import java.util.concurrent.RejectedExecutionException
import java.util.concurrent.TimeUnit

class PostStoreTest {
    @TempDir lateinit var directory: Path

    private fun database(): Path = directory.resolve("test.sqlite").also { path ->
        Arena.ofConfined().use { arena ->
            SQLite3Conn.open(arena, path).use { it.exec(Files.readString(Path.of("../db/migrations/001_init.up.sql"))) }
        }
    }

    @Test
    fun textAndCaseInsensitiveReuse() {
        val path = database()
        PostStore(path).use { store ->
            val content = "é🙂\u0000after NUL" + "x".repeat(32_768)
            val first = store.create(NewPost("Case@example.com", content))
            val second = store.create(NewPost("case@example.com", "short"))
            assertThat(first.content).isEqualTo(content)
            assertThat(second.user_id).isEqualTo(first.user_id)
            assertThat(second.id).isGreaterThan(first.id)
            val third = store.create(NewPost("new@example.com", "third"))
            // INSERT OR IGNORE advances AUTOINCREMENT even on a uniqueness conflict,
            // matching Go's user-ID allocation exactly.
            assertThat(third.user_id).isEqualTo(first.user_id + 2)
            Arena.ofConfined().use { arena ->
                SQLite3Conn.open(arena, path).use { db ->
                    db.prepare("SELECT content FROM posts WHERE id IS ?").use {
                        assertThat(it.queryFirst(arrayOf(first.id)) { row -> row.getString(0) }).isEqualTo(content)
                    }
                }
            }
        }
    }

    @Test
    fun rollbackAndReuse() {
        val path = database()
        PostStore(path).use { store ->
            assertThatThrownBy { store.create(NewPost("rollback@example.com", "")) }.isInstanceOf(SQLite3Exception::class.java)
            assertThat(store.create(NewPost("good@example.com", "ok")).content).isEqualTo("ok")
        }
        Arena.ofConfined().use { arena ->
            SQLite3Conn.open(arena, path).use { db ->
                db.prepare("SELECT count(*) FROM users WHERE email IS 'rollback@example.com'").use {
                    assertThat(it.queryFirst { row -> row.getLong(0) }).isZero()
                }
            }
        }
    }

    @Test
    fun queueDrainsAndRecoversAfterErrors() {
        val path = database()
        val writer = PostWriter(path)
        val replies = (0..<250).map { i ->
            CompletableFuture<Post>().also { reply ->
                writer.submit(NewPost("user@example.com", if (i == 20) "" else "post $i")) { post, error ->
                    if (error != null) reply.completeExceptionally(error) else reply.complete(requireNotNull(post))
                }
            }
        }
        writer.close()
        writer.close()
        replies.forEachIndexed { i, reply ->
            if (i == 20) assertThat(reply.isCompletedExceptionally).isTrue()
            else assertThat(reply.get(1, TimeUnit.SECONDS).content).isEqualTo("post $i")
        }
        assertThatThrownBy { writer.submit(NewPost("user@example.com", "late")) { _, _ -> } }
            .isInstanceOf(RejectedExecutionException::class.java)
    }

    @Test
    fun missingDatabaseDoesNotCreateAFile() {
        val path = directory.resolve("missing.sqlite")
        assertThatThrownBy { PostStore(path) }.isInstanceOf(SQLite3Exception::class.java)
        assertThat(Files.exists(path)).isFalse()
    }
}
