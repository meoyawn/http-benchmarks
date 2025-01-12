package bench

import io.vertx.core.DeploymentOptions
import io.vertx.core.Vertx
import io.vertx.core.VertxOptions
import io.vertx.core.http.HttpMethod
import io.vertx.core.http.HttpResponseExpectation
import io.vertx.core.json.JsonObject
import io.vertx.core.net.SocketAddress
import io.vertx.ext.web.client.WebClient
import io.vertx.ext.web.codec.BodyCodec
import io.vertx.kotlin.coroutines.coAwait
import io.vertx.kotlin.coroutines.dispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.runBlocking
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.AfterAll
import org.junit.jupiter.api.BeforeAll
import org.junit.jupiter.api.Test

private fun Vertx.test(f: suspend CoroutineScope.(Vertx) -> Unit): Unit =
    runBlocking(dispatcher()) { f(this@test) }

class AppTest {

    private companion object {
        val vertx = Vertx.vertx(VertxOptions().setPreferNativeTransport(true))!!
        val addr = SocketAddress.domainSocketAddress("/tmp/${System.currentTimeMillis().toString(radix = 36)}.sock")!!
        val client = WebClient.create(vertx)!!

        @JvmStatic
        @BeforeAll
        fun beforeAll(): Unit = runBlocking {
            vertx.deployVerticle(
                App(),
                DeploymentOptions()
                    .setConfig(JsonObject(mapOf("http.socket" to addr.path())))
            ).coAwait()
        }

        @JvmStatic
        @AfterAll
        fun afterAll() {
            vertx.close()
        }
    }

    @Test
    fun post() = vertx.test {
        val np = NewPost(email = "foo@gmail.com", content = "bar")

        val res = client.request(HttpMethod.POST, addr, "/posts")
            .`as`(BodyCodec.json(Post::class.java))
            .sendJson(np)
            .expecting(HttpResponseExpectation.SC_CREATED)
            .expecting(HttpResponseExpectation.JSON)
            .coAwait()

        assertThat(res.body().content).isEqualTo(np.content)
    }

    @Test
    fun echo() = vertx.test {
        val np = NewPost(email = "foo@gmail.com", content = "bar")

        val res = client.request(HttpMethod.POST, addr, "/echo")
            .`as`(BodyCodec.json(NewPost::class.java))
            .sendJson(np)
            .expecting(HttpResponseExpectation.SC_OK)
            .expecting(HttpResponseExpectation.JSON)
            .coAwait()

        assertThat(res.body()).isEqualTo(np)
    }
}
