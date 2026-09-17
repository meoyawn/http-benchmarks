package bench

import org.assertj.core.api.Assertions.assertThat
import org.assertj.core.api.Assertions.assertThatThrownBy
import org.junit.jupiter.api.Test

class JsonCodecTest {
    @Test
    fun stringsAndUnknownFields() {
        val content = "quote: \" backslash: \\ newline: \n NUL: \u0000 café 中文 🙂"
        val input = NewPost("foo@gmail.com", content)
        assertThat(JsonCodec.decode(JsonCodec.encode(input))).isEqualTo(input)
        assertThat(JsonCodec.decode("""{ "unknown": [null, {"nested": true}], "content": "bar", "email": "foo@gmail.com" }""".toByteArray()))
            .isEqualTo(NewPost("foo@gmail.com", "bar"))
    }

    @Test
    fun rejectInvalidSchemas() {
        for (input in listOf("null", "[]", "{}", "{", "{'email':'a@b.com','content':'ok'}", """{"email":null,"content":"ok"}""",
            """{"email":123,"content":"ok"}""", """{"email":"a@b.com","content":false}""",
            """{"email":"a@b.com" "content":"ok"}""", """{"email":"a@b.com","content":"ok",}""",
            """{"email":"a@b.com","content":"ok"},""",
            """{"email":"a@b.com","content":"ok"} {}""")) {
            assertThatThrownBy { JsonCodec.decode(input.toByteArray()) }.describedAs(input).isInstanceOf(Exception::class.java)
        }
    }
}
