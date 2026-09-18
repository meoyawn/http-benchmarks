package bench

import io.vertx.config.ConfigRetriever
import io.vertx.config.ConfigRetrieverOptions
import io.vertx.core.DeploymentOptions
import io.vertx.core.Vertx
import io.vertx.core.VertxOptions
import io.vertx.kotlin.coroutines.coAwait
import kotlinx.coroutines.runBlocking
import java.nio.file.Files
import java.nio.file.LinkOption.NOFOLLOW_LINKS
import java.nio.file.Path

object Main {

    @JvmStatic
    fun main(args: Array<String>): Unit = runBlocking {
        // The benchmark only listens on a Unix socket; it has no DNS clients.
        System.setProperty("vertx.disableDnsResolver", "true")
        val workers = Integer.getInteger("http.workers", 4)
        require(workers > 0) { "http.workers must be positive, got $workers" }
        val vertx = Vertx.vertx(VertxOptions().setPreferNativeTransport(true).setEventLoopPoolSize(workers))
        var writer: PostWriter? = null
        try {
            val retriever = ConfigRetriever.create(vertx, ConfigRetrieverOptions().setIncludeDefaultStores(true))
            val config = try { retriever.config.coAwait() } finally { retriever.close() }
            val socket = Path.of(config.getString("http.socket", "/tmp/benchmark.sock"))
            require(!Files.exists(socket, NOFOLLOW_LINKS)) { "Socket path already exists: $socket" }
            writer = PostWriter(Path.of(config.getString("db.path", "../db/db.sqlite")))
            vertx.deployVerticle(java.util.function.Supplier { App(writer) },
                DeploymentOptions().setInstances(workers).setConfig(config)).coAwait()
        } catch (e: Exception) {
            try { vertx.close().coAwait() } finally { writer?.close() }
            throw e
        }
        Runtime.getRuntime().addShutdownHook(Thread {
            runBlocking {
                try { vertx.close().coAwait() } finally { writer?.close() }
            }
        })
    }
}
