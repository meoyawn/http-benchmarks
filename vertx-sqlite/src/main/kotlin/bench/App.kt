package bench

import io.vertx.core.impl.logging.LoggerFactory
import io.vertx.core.net.SocketAddress
import io.vertx.ext.web.RoutingContext
import io.vertx.ext.web.handler.HttpException
import io.vertx.ext.web.openapi.Operation
import io.vertx.ext.web.openapi.RouterBuilder
import io.vertx.kotlin.coroutines.CoroutineVerticle
import io.vertx.kotlin.coroutines.coAwait
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch

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

private data class NewPost(
    val email: String,
    val content: String,
)

class App(private val db: Db) : CoroutineVerticle() {

    private companion object {
        val logger = LoggerFactory.getLogger(App::class.java)!!
    }

    override suspend fun start() {

        val router = RouterBuilder.create(vertx, "openapi.json").coAwait().apply {
            operation("newPost").coHandle(scope = this@App) { ctx ->
                val body = ctx.body().asPojo(NewPost::class.java) ?: throw HttpException(400)

                val post = db.tx {
                    insertUser(body.email)
                    insertPost(email = body.email, content = body.content)
                }

                ctx.response().statusCode = 201
                ctx.json(post)
            }

            operation("echo").handler {
                val body = it.body().asPojo(NewPost::class.java)
                it.json(body)
            }
        }.createRouter()

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
}
