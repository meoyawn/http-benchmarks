package bench

import io.vertx.config.ConfigRetriever
import io.vertx.config.ConfigRetrieverOptions
import io.vertx.core.DeploymentOptions
import io.vertx.core.Vertx
import io.vertx.core.VertxOptions
import io.vertx.kotlin.coroutines.coAwait
import kotlinx.coroutines.runBlocking

object Main {

    @JvmStatic
    fun main(args: Array<String>): Unit = runBlocking {
        val vertx = Vertx.vertx(VertxOptions().setPreferNativeTransport(true))
        Runtime.getRuntime().addShutdownHook(Thread {
            runBlocking {
                vertx.close().coAwait()
            }
        })

        val retriever = ConfigRetriever.create(vertx, ConfigRetrieverOptions().setIncludeDefaultStores(true))
        val config = retriever.config.coAwait()
        vertx.deployVerticle(App(), DeploymentOptions().setConfig(config)).coAwait()
    }
}
