package bench

import com.alibaba.fastjson2.JSON
import com.alibaba.fastjson2.JSONException
import com.alibaba.fastjson2.JSONReader

/** Typed schema adapter: fastjson2 owns tokenization, escaping, UTF-8 and output. */
object JsonCodec {
    @JvmStatic
    fun decode(bytes: ByteArray): NewPost = JSONReader.of(bytes).use { reader ->
        reader.context.config(JSONReader.Feature.DisableSingleQuote)
        if (!reader.nextIfObjectStart()) throw JSONException("expected JSON object")
        var email: String? = null
        var content: String? = null
        while (!reader.nextIfObjectEnd()) {
            when (reader.readFieldName()) {
                "email" -> {
                    if (!reader.isString) throw JSONException("email must be a string")
                    email = reader.readString()
                }
                "content" -> {
                    if (!reader.isString) throw JSONException("content must be a string")
                    content = reader.readString()
                }
                else -> reader.skipValue()
            }
            if (reader.hasComma() == (reader.current() == '}')) throw JSONException("invalid field separator")
        }
        if (!reader.isEnd || reader.hasComma()) throw JSONException("trailing JSON data")
        NewPost(email ?: throw JSONException("email is required"), content ?: throw JSONException("content is required"))
    }

    @JvmStatic
    fun encode(value: Any): ByteArray = JSON.toJSONBytes(value)
}
