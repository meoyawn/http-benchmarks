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
import java.lang.foreign.Arena
import java.nio.file.Files
import java.nio.file.Path
import sqlite.SQLite3Conn

private fun Vertx.test(f: suspend CoroutineScope.(Vertx) -> Unit): Unit =
    runBlocking(dispatcher()) { f(this@test) }

class AppTest {

    private companion object {
        val dbDir = Files.createTempDirectory("vertx-panama-test")
        val dbPath = dbDir.resolve("test.sqlite")
        val vertx = Vertx.vertx(VertxOptions().setPreferNativeTransport(true))!!
        val addr = SocketAddress.domainSocketAddress("/tmp/${System.currentTimeMillis().toString(radix = 36)}.sock")!!
        val client = WebClient.create(vertx)!!

        @JvmStatic
        @BeforeAll
        fun beforeAll(): Unit = runBlocking {
            Arena.ofConfined().use { arena ->
                SQLite3Conn.open(arena, dbPath).use { conn ->
                    conn.exec(Files.readString(Path.of("../db/migrations/001_init.up.sql")))
                }
            }
            vertx.deployVerticle(
                App(),
                DeploymentOptions()
                    .setConfig(JsonObject(mapOf("http.socket" to addr.path(), "db.path" to dbPath.toString())))
            ).coAwait()
        }

        @JvmStatic
        @AfterAll
        fun afterAll(): Unit = runBlocking {
            vertx.close().coAwait()
            Files.deleteIfExists(Path.of(addr.path()))
            Files.list(dbDir).use { files -> files.forEach { Files.delete(it) } }
            Files.delete(dbDir)
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

        val post = requireNotNull(res.body())
        assertThat(post.content).isEqualTo(np.content)
        assertThat(post.id).isPositive()
        assertThat(post.user_id).isPositive()
        assertThat(post.created_at).isPositive()
        assertThat(post.updated_at).isEqualTo(post.created_at)
        Arena.ofConfined().use { arena ->
            SQLite3Conn.open(arena, dbPath).use { conn ->
                conn.prepare("SELECT content FROM posts WHERE id IS ?").use { stmt ->
                    assertThat(stmt.queryFirst(arrayOf(post.id)) { it.getString(0) })
                        .isEqualTo(np.content)
                }
            }
        }
    }

    @Test
    fun invalidPost() = vertx.test {
        val res = client.request(HttpMethod.POST, addr, "/posts")
            .sendJson(NewPost(email = "invalid", content = ""))
            .coAwait()

        assertThat(res.statusCode()).isEqualTo(400)
        assertThat(requireNotNull(res.bodyAsJsonArray()).size()).isEqualTo(2)
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
