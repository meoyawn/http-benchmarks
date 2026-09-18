package bench

import com.alibaba.fastjson2.JSON
import com.alibaba.fastjson2.JSONWriter
import io.vertx.core.buffer.Buffer
import io.vertx.core.json.DecodeException
import io.vertx.core.json.EncodeException
import io.vertx.core.json.JsonArray
import io.vertx.core.json.JsonObject
import io.vertx.core.json.impl.JsonUtil
import io.vertx.core.spi.JsonFactory
import io.vertx.core.spi.json.JsonCodec
import java.time.Instant

/** Use the same JSON library for Vert.x configuration and JSON wrappers. */
class FastJsonFactory : JsonFactory {
    override fun codec(): JsonCodec = FastJsonCodec
}

internal object FastJsonCodec : JsonCodec {
    override fun <T> fromString(json: String, type: Class<T>): T = decode {
        convert(JSON.parse(json), type)
    }

    override fun <T> fromBuffer(json: Buffer, type: Class<T>): T = decode {
        convert(JSON.parse(json.bytes), type)
    }

    override fun <T> fromValue(value: Any?, type: Class<T>): T = decode {
        convert(JSON.toJSON(unwrap(value)), type)
    }

    @Suppress("UNCHECKED_CAST")
    private fun <T> convert(value: Any?, type: Class<T>): T = when {
        value == null -> null
        type == JsonObject::class.java -> JsonObject(value as Map<String, Any?>)
        type == JsonArray::class.java -> JsonArray(value as List<*>)
        type == Any::class.java -> JsonUtil.wrapJsonValue(value)
        else -> JSON.to(type, value)
    } as T

    private fun unwrap(value: Any?): Any? = when (value) {
        is JsonObject -> unwrap(value.map)
        is JsonArray -> unwrap(value.list)
        is Map<*, *> -> value.mapValues { unwrap(it.value) }
        is Iterable<*> -> value.map(::unwrap)
        is Array<*> -> value.map(::unwrap)
        is ByteArray -> JsonUtil.BASE64_ENCODER.encodeToString(value)
        is Buffer -> JsonUtil.BASE64_ENCODER.encodeToString(value.bytes)
        is Instant -> value.toString()
        else -> value
    }

    override fun toString(value: Any?, pretty: Boolean): String = try {
        val features = if (pretty) arrayOf(JSONWriter.Feature.WriteMapNullValue, JSONWriter.Feature.PrettyFormat)
            else arrayOf(JSONWriter.Feature.WriteMapNullValue)
        JSON.toJSONString(unwrap(value), *features)
    } catch (e: Exception) {
        throw EncodeException("Cannot encode JSON", e)
    }

    override fun toBuffer(value: Any?, pretty: Boolean): Buffer = Buffer.buffer(toString(value, pretty))

    private inline fun <T> decode(block: () -> T): T = try {
        block()
    } catch (e: Exception) {
        throw DecodeException("Cannot decode JSON", e)
    }
}
