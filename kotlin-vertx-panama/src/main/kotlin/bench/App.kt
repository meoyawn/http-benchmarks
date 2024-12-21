package bench

import com.alibaba.fastjson2.JSON
import io.vertx.core.Future
import io.vertx.core.buffer.Buffer
import io.vertx.core.http.HttpServerResponse
import io.vertx.core.impl.logging.LoggerFactory
import io.vertx.core.net.SocketAddress
import io.vertx.ext.web.RequestBody
import io.vertx.ext.web.Route
import io.vertx.ext.web.Router
import io.vertx.ext.web.RoutingContext
import io.vertx.ext.web.handler.BodyHandler
import io.vertx.json.schema.common.RegularExpressions
import io.vertx.kotlin.coroutines.CoroutineVerticle
import io.vertx.kotlin.coroutines.coAwait
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.asCoroutineDispatcher
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.channels.ReceiveChannel
import kotlinx.coroutines.channels.SendChannel
import kotlinx.coroutines.launch
import org.jetbrains.annotations.VisibleForTesting
import sqlite.SQLite3Conn
import java.lang.foreign.Arena
import java.nio.file.Path
import java.util.concurrent.Executors

private inline fun Route.coHandler(scope: CoroutineScope, crossinline fn: suspend RoutingContext.() -> Unit): Route =
    handler { ctx ->
        scope.launch {
            try {
                fn(ctx)
            } catch (e: Exception) {
                ctx.fail(500, e)
            }
        }
    }

@VisibleForTesting
data class NewPost(
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

private data class Call<T, R>(
    val req: T,
    val res: SendChannel<R>,
)

private suspend fun <T, R> SendChannel<Call<T, R>>.call(req: T): R {
    val res = Channel<R>()
    send(Call(req, res))
    return res.receive()
}

private inline fun <reified T> RequestBody.fastJSON(): T =
    JSON.parseObject(buffer().bytes, T::class.java)

private fun <T> HttpServerResponse.fastJSON(t: T): Future<Void> =
    end(Buffer.buffer(JSON.toJSONBytes(t)))

private suspend fun RoutingContext.httpPost(db: SendChannel<Call<NewPost, Post>>) {
    val body = body().fastJSON<NewPost>()

    val errs = body.validate()
    if (errs.isNotEmpty()) {
        response()
            .setStatusCode(400)
            .fastJSON(errs)
        return
    }

    val post = db.call(body)
    response()
        .setStatusCode(201)
        .fastJSON(post)
}

private fun SQLite3Conn.insertUser(email: String): Unit =
    prepareCached("INSERT OR IGNORE INTO users (email) VALUES (?)")
        .exec(arrayOf(email))

private fun SQLite3Conn.insertPost(req: NewPost): Post =
    prepareCached(
        """
        INSERT INTO posts   (content,   user_id)
        SELECT              ?,          id
        FROM        users
        WHERE       email = ?
        RETURNING   id, user_id, content, created_at, updated_at
        """
    ).queryRow(arrayOf(req.content, req.email)) {
        Post(
            id = it.getLong(0),
            user_id = it.getLong(1),
            content = it.getString(2),
            created_at = it.getLong(3),
            updated_at = it.getLong(4),
        )
    }

private suspend fun dbWriter(dbPath: Path, chan: ReceiveChannel<Call<NewPost, Post>>): Unit =
    Arena.ofConfined().use { arena ->
        SQLite3Conn.open(arena, dbPath).use { conn ->
            conn.exec(
                """
                PRAGMA journal_mode = WAL;
                PRAGMA synchronous = NORMAL;
                PRAGMA foreign_keys = ON;
                PRAGMA busy_timeout = 10000;

                PRAGMA strict = ON;

                PRAGMA optimize = 0x10002;
                """
            )

            for ((req, res) in chan) {
                val post = conn.transact(SQLite3Conn.TxMode.IMMEDIATE) {
                    insertUser(req.email)
                    insertPost(req)
                }

                res.send(post)
            }

            conn.exec("PRAGMA optimize;")
        }
    }

class App : CoroutineVerticle() {

    private companion object {
        val logger = LoggerFactory.getLogger(App::class.java)!!
    }

    private val dbChan = Channel<Call<NewPost, Post>>(Channel.BUFFERED)

    override suspend fun start() {

        launch(Executors.newSingleThreadExecutor().asCoroutineDispatcher()) {
            dbWriter(Path.of("../db/db.sqlite"), dbChan)
        }

        val router = Router.router(vertx).apply {
            val fileUploads = false
            route().handler(BodyHandler.create(fileUploads))

            post("/echo").handler {
                val body = it.body().fastJSON<NewPost>()
                it.response().fastJSON(body)
            }

            post("/posts").coHandler(scope = this@App) {
                httpPost(dbChan)
            }
        }

        val uds = config.getString("http.socket", "/tmp/benchmark.sock")

        vertx.createHttpServer()
            .requestHandler(router)
            .listen(SocketAddress.domainSocketAddress(uds))
            .coAwait()

        logger.info(uds)
    }

    override suspend fun stop() {
        // closes database
        dbChan.close()
    }
}
