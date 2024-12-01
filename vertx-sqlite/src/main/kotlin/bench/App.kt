package bench

import io.vertx.core.impl.logging.LoggerFactory
import io.vertx.core.net.SocketAddress
import io.vertx.ext.web.openapi.RouterBuilder
import io.vertx.kotlin.coroutines.CoroutineVerticle
import io.vertx.kotlin.coroutines.coAwait
import kotlinx.coroutines.asCoroutineDispatcher
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.util.concurrent.Executors
import kotlin.time.Duration.Companion.days

class App : CoroutineVerticle() {

    private companion object {
        val logger = LoggerFactory.getLogger(App::class.java)!!
    }

    private val writeThread = Executors.newSingleThreadExecutor().asCoroutineDispatcher()
    private val closeables = mutableListOf<AutoCloseable>()

    override suspend fun start() {

        val writeConn = withContext(writeThread) {
            SQLite.DATA_SOURCE.connection.apply {
                createStatement().use { it.execute("PRAGMA optimize=0x10002;") }
            }
        }
        launch {
            while (true) {
                delay(1.days)
                withContext(writeThread) {
                    writeConn.createStatement().use { it.execute("PRAGMA optimize;") }
                }
            }
        }
        closeables.add(AutoCloseable {
            writeConn.createStatement().use { it.execute("PRAGMA optimize;") }
            writeConn.close()
        })

        val router = RouterBuilder.create(vertx, "openapi.json").coAwait()
            .openApiRoutes(
                appScope = this@App,
                writeConn = writeConn,
                writeThread = writeThread,
                closeables = closeables,
            )
            .createRouter()

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
        withContext(writeThread) {
            for (i in closeables.indices.reversed()) {
                closeables[i].close()
            }
        }
        writeThread.close()
    }
}
