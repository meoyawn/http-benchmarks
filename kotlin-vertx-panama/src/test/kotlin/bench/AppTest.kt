package bench

import com.alibaba.fastjson2.JSON
import io.vertx.core.DeploymentOptions
import io.vertx.core.Vertx
import io.vertx.core.VertxOptions
import io.vertx.core.buffer.Buffer
import io.vertx.core.http.HttpClientOptions
import io.vertx.core.http.HttpClientResponse
import io.vertx.core.http.HttpHeaders
import io.vertx.core.http.HttpMethod
import io.vertx.core.http.RequestOptions
import io.vertx.core.json.JsonObject
import io.vertx.core.net.SocketAddress
import io.vertx.kotlin.coroutines.coAwait
import io.vertx.kotlin.coroutines.dispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.runBlocking
import org.junit.jupiter.api.AfterAll
import org.junit.jupiter.api.BeforeAll
import org.junit.jupiter.api.Test
import kotlin.test.assertEquals

private fun Vertx.test(f: suspend CoroutineScope.(Vertx) -> Unit): Unit =
    runBlocking(dispatcher()) { f(this@test) }

class AppTest {

    private companion object {
        val vertx = Vertx.vertx(VertxOptions().setPreferNativeTransport(true))!!
        val addr = SocketAddress.domainSocketAddress("/tmp/${System.currentTimeMillis().toString(36)}.sock")!!
        val client = vertx.createHttpClient(HttpClientOptions())!!

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

        suspend fun postJSON(path: String, body: Any): HttpClientResponse {
            require(path.startsWith('/'))

            val req = RequestOptions()
                .setServer(addr)
                .setMethod(HttpMethod.POST)
                .setURI(path)
                .putHeader(HttpHeaders.CONTENT_TYPE, "application/json; charset=utf-8")

            return client.request(req)
                .coAwait()
                .send(Buffer.buffer(JSON.toJSONBytes(body)))
                .coAwait()
        }
    }

    @Test
    fun echo() = vertx.test {
        val np = NewPost(email = "foo", content = "bar")
        val res = postJSON(path = "/echo", body = np)
        assertEquals(actual = res.statusCode(), expected = 200)

        val body = res.body().coAwait()
        assertEquals(actual = JSON.parseObject(body.bytes, NewPost::class.java), expected = np)
    }
}
