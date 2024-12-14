package bench

import io.vertx.core.impl.logging.LoggerFactory
import io.vertx.core.net.SocketAddress
import io.vertx.ext.web.Route
import io.vertx.ext.web.Router
import io.vertx.ext.web.RoutingContext
import io.vertx.ext.web.handler.BodyHandler
import io.vertx.ext.web.handler.HttpException
import io.vertx.json.schema.common.RegularExpressions
import io.vertx.kotlin.coroutines.CoroutineVerticle
import io.vertx.kotlin.coroutines.coAwait
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.asCoroutineDispatcher
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.channels.SendChannel
import kotlinx.coroutines.launch
import java.sql.Connection
import java.sql.PreparedStatement
import java.util.concurrent.Executors

private inline fun Route.coHandler(scope: CoroutineScope, crossinline fn: suspend (ctx: RoutingContext) -> Unit) =
    handler { ctx ->
        scope.launch {
            try {
                fn(ctx)
            } catch (e: Exception) {
                ctx.fail(500, e)
            }
        }
    }

private data class NewPost(
    val email: String,
    val content: String,
)

private data class Post(
    val id: Long,
    val user_id: Long,
    val content: String,
    val created_at: Long,
    val updated_at: Long,
)

private fun NewPost.validate(): List<String> {
    val errs = ArrayList<String>(2)
    if (content.isEmpty()) {
        errs.add("content: must not be empty")
    }
    if (!RegularExpressions.EMAIL.matcher(email).matches()) {
        errs.add("email: invalid: $email")
    }
    return errs
}

private data class ReqRes<T, R>(
    val req: T,
    val res: Channel<R>
)

private suspend fun <T, R> SendChannel<ReqRes<T, R>>.call(req: T): R {
    val res = Channel<R>()
    send(ReqRes(req, res))
    return res.receive()
}

private suspend fun httpPost(chan: SendChannel<ReqRes<NewPost, Post>>, ctx: RoutingContext) {
    val body = ctx.body().asPojo(NewPost::class.java) ?: throw HttpException(400)

    val errs = body.validate()
    if (errs.isNotEmpty()) {
        ctx.response().statusCode = 400
        ctx.json(errs)
        return
    }

    val post = chan.call(body)

    ctx.response().statusCode = 201
    ctx.json(post)
}

private class CachingConn(val conn: Connection) : AutoCloseable {

    private val cache = HashMap<String, PreparedStatement>()

    fun prepared(sql: String): PreparedStatement =
        cache.getOrPut(sql) { conn.prepareStatement(sql) }

    inline fun <T> immediateTX(fn: CachingConn.() -> T): T {
        conn.autoCommit = false

        return try {
            fn().also {
                conn.commit()
            }
        } catch (e: Exception) {
            conn.rollback()
            throw e
        }
    }

    override fun close() {
        for ((_, stmt) in cache) {
            stmt.close()
        }
        cache.clear()

        conn.createStatement().use { it.execute("PRAGMA optimize;") }
        conn.close()
    }
}

class App : CoroutineVerticle() {

    private companion object {
        val logger = LoggerFactory.getLogger(App::class.java)!!
    }

    private val chan = Channel<ReqRes<NewPost, Post>>(Channel.BUFFERED)

    override suspend fun start() {

        launch(Executors.newSingleThreadExecutor().asCoroutineDispatcher()) {

            val conn = CachingConn(SQLite.DATA_SOURCE.connection.apply {
                createStatement().use {
                    it.execute("PRAGMA strict = ON;")
                    it.execute("PRAGMA optimize = 0x10002;")
                }
            })

            for ((req, res) in chan) {

                val post = conn.immediateTX {
                    prepared("INSERT OR IGNORE INTO users (email) VALUES (?)").run {
                        setString(1, req.email)
                        executeUpdate()
                    }

                    prepared(
                        """
                        INSERT INTO posts   (content,   user_id)
                        SELECT              ?,          id
                        FROM        users
                        WHERE       email = ?
                        RETURNING   id, user_id, content, created_at, updated_at
                        """
                    ).run {
                        setString(1, req.content)
                        setString(2, req.email)
                        executeQuery().use {
                            check(it.next())

                            Post(
                                id = it.getLong(1),
                                user_id = it.getLong(2),
                                content = it.getString(3),
                                created_at = it.getLong(4),
                                updated_at = it.getLong(5),
                            )
                        }
                    }
                }

                res.send(post)
            }

            conn.close()
        }

        val router = Router.router(vertx).apply {
            route().handler(BodyHandler.create(false))

            post("/echo").handler {
                val body = it.body().asPojo(NewPost::class.java)
                it.json(body)
            }

            post("/posts").coHandler(scope = this@App) {
                httpPost(chan, it)
            }
        }

        val host = config.getString("http.host", "localhost")
        val port = config.getInteger("http.port", 8080)
        val domainSocket = config.getString("http.socket", "")

        vertx.createHttpServer()
            .requestHandler(router)
            .listen(
                if (domainSocket.isNullOrBlank()) SocketAddress.inetSocketAddress(port, host)
                else SocketAddress.domainSocketAddress(domainSocket)
            )
            .coAwait()

        logger.info(
            if (domainSocket.isNullOrBlank()) "http://$host:$port"
            else domainSocket
        )
    }

    override suspend fun stop() {
        chan.close()
    }
}
