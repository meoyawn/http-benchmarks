package bench

import io.vertx.ext.web.RoutingContext
import io.vertx.ext.web.handler.HttpException
import io.vertx.ext.web.openapi.Operation
import io.vertx.ext.web.openapi.RouterBuilder
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.sql.Connection
import java.sql.PreparedStatement

private inline fun Operation.coHandle(scope: CoroutineScope, crossinline f: suspend (ctx: RoutingContext) -> Unit) =
    handler { ctx ->
        scope.launch {
            try {
                f(ctx)
            } catch (e: Exception) {
                ctx.fail(500, e)
            }
        }
    }

suspend inline fun <T> Connection.tx(thread: CoroutineDispatcher, crossinline fn: Connection.() -> T): T =
    withContext(thread) {
        val old = autoCommit
        autoCommit = false
        try {
            fn().also {
                commit()
            }
        } catch (e: Exception) {
            rollback()
            throw e
        } finally {
            autoCommit = old
        }
    }

private data class NewPost(
    val email: String,
    val content: String,
)

private data class Post(
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

private fun PreparedStatement.execute(body: NewPost): Post {
    setString(1, body.content)
    setString(2, body.email)

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

suspend fun RouterBuilder.openApiRoutes(
    appScope: CoroutineScope,
    writeConn: Connection,
    writeThread: CoroutineDispatcher,
    closeables: MutableList<in AutoCloseable>,
): RouterBuilder {

    val (insertUser, insertPost) = withContext(writeThread) {
        Pair(
            writeConn.prepareStatement("INSERT OR IGNORE INTO users (email) VALUES (?)").also(closeables::add),
            writeConn.prepareStatement(INSERT_POST).also(closeables::add),
        )
    }

    operation("newPost").coHandle(appScope) { ctx ->
        val body = ctx.body().asPojo(NewPost::class.java) ?: throw HttpException(400)

        val post = writeConn.tx(writeThread) {
            insertUser.setString(1, body.email)
            check(!insertUser.execute())

            insertPost.execute(body)
        }

        ctx.response().statusCode = 201
        ctx.json(post)
    }

    return this
}
