package bench

import io.vertx.core.buffer.Buffer
import io.vertx.core.json.DecodeException
import io.vertx.core.json.Json
import io.vertx.core.json.JsonArray
import io.vertx.core.json.JsonObject
import org.junit.jupiter.api.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertIs

class FastJsonFactoryTest {
    @Test
    fun `Vertx discovers fastjson without Jackson`() {
        assertIs<FastJsonFactory>(Json.load())
        assertFailsWith<ClassNotFoundException> { Class.forName("com.fasterxml.jackson.core.JsonFactory") }
        assertFailsWith<ClassNotFoundException> { Class.forName("com.fasterxml.jackson.databind.ObjectMapper") }
    }

    @Test
    fun `Vertx JSON wrappers and null values round trip`() {
        val value = JsonObject().put("nested", JsonArray().add(JsonObject().put("unicode", "café 🐈")))
            .putNull("missing").put("bytes", byteArrayOf(0, 1, 2))
        assertEquals(value, JsonObject(value.encode()))
        assertEquals(value, JsonObject(value.encodePrettily()))
        assertEquals(value, Json.decodeValue(value.toBuffer(), JsonObject::class.java))
        assertEquals(value, Json.decodeValue(value.encode()))
        assertEquals(JsonArray.of(1, null, "x"), Json.decodeValue("[1,null,\"x\"]"))
    }

    @Test
    fun `Kotlin models map through Vertx APIs`() {
        val value = NewPost("test@example.com", "quotes \" newline\n NUL\u0000")
        assertEquals(value, Json.decodeValue(Json.encode(value), NewPost::class.java))
        assertEquals(value, JsonObject.mapFrom(value).mapTo(NewPost::class.java))
        val post = Post(1, 2, "x", 3, 4)
        assertEquals(post, Json.decodeValue(Json.encodeToBuffer(post), Post::class.java))
    }

    @Test
    fun `invalid input uses Vertx decode exceptions`() {
        assertFailsWith<DecodeException> { Json.decodeValue("{", JsonObject::class.java) }
        assertFailsWith<DecodeException> { Json.decodeValue(Buffer.buffer("[]"), JsonObject::class.java) }
    }
}
