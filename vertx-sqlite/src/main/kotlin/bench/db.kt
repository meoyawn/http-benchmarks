package bench

import kotlinx.coroutines.ExecutorCoroutineDispatcher
import kotlinx.coroutines.withContext
import java.sql.Connection
import java.sql.PreparedStatement

private const val INSERT_USER = """
INSERT OR IGNORE INTO users (email) VALUES (?)
"""

private fun PreparedStatement.insertUser(email: String) {
    setString(1, email)
    check(!execute())
}

data class Post(
    val id: Long,
    val userID: Long,
    val content: String,
    val createdAt: Long,
    val updatedAt: Long,
)

private const val INSERT_POST = """
INSERT INTO posts   (content,   user_id)
SELECT              ?,          id
FROM        users
WHERE       email = ?
RETURNING   id, user_id, content, created_at, updated_at
"""

private fun PreparedStatement.insertPost(email: String, content: String): Post {
    setString(1, content)
    setString(2, email)

    return executeQuery().use {
        check(it.next())

        Post(
            id = it.getLong(1),
            userID = it.getLong(2),
            content = it.getString(3),
            createdAt = it.getLong(4),
            updatedAt = it.getLong(5),
        )
    }
}

class Db(
    val thread: ExecutorCoroutineDispatcher,
    val conn: Connection,
    private val insertUser: PreparedStatement,
    private val insertPost: PreparedStatement,
) : AutoCloseable {

    companion object {

        fun create(thread: ExecutorCoroutineDispatcher): Db {
            val conn = SQLite.DATA_SOURCE.connection

            conn.createStatement().use { it.execute("PRAGMA optimize=0x10002;") }

            val insertUser = conn.prepareStatement(INSERT_USER)
            val insertPost = conn.prepareStatement(INSERT_POST)

            return Db(thread = thread, conn = conn, insertUser = insertUser, insertPost = insertPost)
        }
    }

    fun insertUser(email: String): Unit =
        insertUser.insertUser(email)

    fun insertPost(email: String, content: String): Post =
        insertPost.insertPost(email = email, content = content)

    suspend inline fun <T> tx(crossinline fn: Db.() -> T): T =
        withContext(thread) {
            conn.autoCommit = false
            try {
                fn(this@Db).also {
                    conn.commit()
                }
            } catch (e: Exception) {
                conn.rollback()
                throw e
            } finally {
                conn.autoCommit = true
            }
        }

    override fun close() {
        insertPost.close()
        insertUser.close()
        conn.createStatement().use { it.execute("PRAGMA optimize;") }
        conn.close()
    }
}
