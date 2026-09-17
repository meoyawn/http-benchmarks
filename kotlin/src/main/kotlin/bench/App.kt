package bench

import com.fasterxml.jackson.core.JsonParser
import com.fasterxml.jackson.module.paramnames.ParameterNamesModule
import io.vertx.core.json.jackson.DatabindCodec
import io.vertx.core.Vertx
import io.vertx.core.buffer.Buffer
import io.vertx.core.http.HttpServer
import io.vertx.core.net.SocketAddress
import io.vertx.ext.web.Router
import io.vertx.ext.web.RoutingContext
import io.vertx.ext.web.handler.BodyHandler
import io.vertx.kotlin.coroutines.CoroutineVerticle
import io.vertx.kotlin.coroutines.coAwait
import io.vertx.sqlclient.Pool
import kotlinx.coroutines.launch
import java.nio.file.Path
import java.util.concurrent.RejectedExecutionException

data class NewPost(
    val email: String,
    val content: String,
)

@Suppress("PropertyName")
data class Post(
    val id: Long,
    val user_id: Long,
    val content: String,
    val created_at: Long,
    val updated_at: Long,
)

private val emailPattern = Regex("""[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}""")
internal fun validEmail(email: String): Boolean = emailPattern.matches(email)

private fun NewPost.validate(): List<String> {
    val errs = ArrayList<String>(2)
    if (content.isEmpty()) {
        errs.add("content: must not be empty")
    }
    if (!validEmail(email)) {
        errs.add("email: invalid: $email")
    }
    return errs
}

private suspend fun RoutingContext.postPG(pg: Pool) {
    val body = requireNotNull(body().asPojo(NewPost::class.java))

    val errs = body.validate()
    if (errs.isNotEmpty()) {
        response().setStatusCode(400)
        json(errs)
        return
    }

    val post = pg.transact {
        insertUser(body.email)
        insertPost(body)
    }

    response().setStatusCode(201)
    json(post)
}

class App(private val sharedWriter: PostWriter? = null) : CoroutineVerticle() {

    private companion object {
        val logger = System.getLogger(App::class.java.name)

        init {
            DatabindCodec.mapper()
                .registerModules(ParameterNamesModule())
                .enable(JsonParser.Feature.INCLUDE_SOURCE_IN_LOCATION)
        }
    }

    private var pg: Pool? = null
    private var writer: PostWriter? = null
    private var server: HttpServer? = null

    override suspend fun start() {
        val usePostgres = config.getString("db.backend", "sqlite") == "postgres"
        if (usePostgres) pg = mkPG(vertx)
        else writer = sharedWriter ?: PostWriter(Path.of(config.getString("db.path", "../db/db.sqlite")))

        fun parse(ctx: RoutingContext): NewPost? = try {
            JsonCodec.decode(requireNotNull(ctx.body().buffer()).bytes)
        } catch (_: Exception) {
            ctx.response().setStatusCode(400).end("invalid JSON body")
            null
        }

        val router = Router.router(vertx).apply {
            route().handler(BodyHandler.create(false))
            post("/echo").handler { ctx ->
                val body = parse(ctx) ?: return@handler
                ctx.response().putHeader("content-type", "application/json")
                    .end(Buffer.buffer(JsonCodec.encode(body)))
            }
            if (usePostgres) {
                post("/posts").handler { ctx ->
                    launch {
                        try { ctx.postPG(requireNotNull(pg)) }
                        catch (e: Exception) { ctx.fail(e) }
                    }
                }
            } else {
                post("/posts").handler { ctx ->
                    val body = parse(ctx) ?: return@handler
                    val errors = body.validate()
                    if (errors.isNotEmpty()) {
                        ctx.response().setStatusCode(400).putHeader("content-type", "application/json")
                            .end(Buffer.buffer(JsonCodec.encode(errors)))
                    } else {
                        try {
                            val responseContext = requireNotNull(Vertx.currentContext())
                            requireNotNull(writer).submit(body) { result, error ->
                                responseContext.runOnContext {
                                    if (error != null) ctx.response().setStatusCode(500).end("database error")
                                    else ctx.response().setStatusCode(201).putHeader("content-type", "application/json")
                                        .end(Buffer.buffer(JsonCodec.encode(requireNotNull(result))))
                                }
                            }
                        } catch (_: RejectedExecutionException) {
                            ctx.response().setStatusCode(503).end("writer queue full")
                        }
                    }
                }
            }
        }

        val uds = config.getString("http.socket", "/tmp/benchmark.sock")

        server = vertx.createHttpServer()
            .requestHandler(router)
            .listen(SocketAddress.domainSocketAddress(uds))
            .coAwait()

        logger.log(System.Logger.Level.INFO, "Listening on $uds")
    }

    override suspend fun stop() {
        server?.close()?.coAwait()
        if (sharedWriter == null) writer?.close()
        pg?.close()?.coAwait()
    }
}
