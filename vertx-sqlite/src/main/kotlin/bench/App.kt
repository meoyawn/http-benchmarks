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
import kotlinx.coroutines.launch

private inline fun Route.coHandle(scope: CoroutineScope, crossinline f: suspend (ctx: RoutingContext) -> Unit) =
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

private fun NewPost.validate(): List<String> {
    val errors = ArrayList<String>(2)
    if (content.isEmpty()) errors.add("content: must not be empty")
    if (!RegularExpressions.EMAIL.matcher(email).matches()) errors.add("email: invalid: $email")
    return errors
}

private suspend fun httpPost(db: Db, ctx: RoutingContext) {
    val body = ctx.body().asPojo(NewPost::class.java) ?: throw HttpException(400)
    val errs = body.validate()
    if (errs.isNotEmpty()) {
        ctx.response().statusCode = 400
        ctx.json(errs)
        return
    }

    val post = db.tx {
        insertUser(body.email)
        insertPost(email = body.email, content = body.content)
    }

    ctx.response().statusCode = 201
    ctx.json(post)
}

class App(private val db: Db) : CoroutineVerticle() {

    private companion object {
        val logger = LoggerFactory.getLogger(App::class.java)!!
    }

    override suspend fun start() {

        val router = Router.router(vertx).apply {
            route().handler(BodyHandler.create(false))

            post("/posts").coHandle(scope = this@App) {
                httpPost(db, it)
            }

            post("/echo").handler {
                val body = it.body().asPojo(NewPost::class.java)
                it.json(body)
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
}
