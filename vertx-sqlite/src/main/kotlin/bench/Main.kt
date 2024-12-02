package bench

import io.vertx.config.ConfigRetriever
import io.vertx.config.ConfigRetrieverOptions
import io.vertx.core.DeploymentOptions
import io.vertx.core.ThreadingModel
import io.vertx.core.Vertx
import io.vertx.core.VertxOptions
import io.vertx.core.json.jackson.DatabindCodec
import io.vertx.kotlin.coroutines.coAwait
import kotlinx.coroutines.asCoroutineDispatcher
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withContext
import java.util.concurrent.Executors

object Main {

    @JvmStatic
    fun main(args: Array<String>): Unit = runBlocking {
        val vertx = Vertx.vertx(
            VertxOptions().setPreferNativeTransport(true)
        )
        val retriever = ConfigRetriever.create(vertx, ConfigRetrieverOptions().setIncludeDefaultStores(true))

//        System.setProperty("java.util.logging.SimpleFormatter.format", "[%1\$tF %1\$tT] [%4\$s] %5\$s %n")
        DatabindCodec.mapper().configKotlin()

        val config = retriever.config.coAwait()

        val writerThread = Executors.newSingleThreadExecutor { Thread(it, "DB writer") }.asCoroutineDispatcher()

        val db = withContext(writerThread) { Db.create(writerThread) }

        val id = vertx.deployVerticle(
            { App(db) },
            DeploymentOptions()
                .setConfig(config)
        ).coAwait()

        Runtime.getRuntime().addShutdownHook(Thread {
            runBlocking {
                vertx.undeploy(id).coAwait()
                vertx.close().coAwait()

                withContext(writerThread) {
                    db.close()
                }
            }

            writerThread.close()
        })
    }
}
