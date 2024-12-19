package bench

import com.fasterxml.jackson.annotation.JsonInclude
import com.fasterxml.jackson.databind.DeserializationFeature
import com.fasterxml.jackson.databind.MapperFeature
import com.fasterxml.jackson.databind.ObjectMapper
import com.fasterxml.jackson.databind.SerializationFeature
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule
import com.fasterxml.jackson.module.kotlin.KotlinFeature
import com.fasterxml.jackson.module.kotlin.KotlinModule

fun ObjectMapper.configKotlin(): ObjectMapper {
    setSerializationInclusion(JsonInclude.Include.NON_NULL)

    registerModule(
        KotlinModule.Builder()
            .withReflectionCacheSize(512)
            .configure(KotlinFeature.NullToEmptyCollection, enabled = false)
            .configure(KotlinFeature.NullToEmptyMap, enabled = false)
            .configure(KotlinFeature.NullIsSameAsDefault, enabled = false)
            .configure(KotlinFeature.SingletonSupport, enabled = false)
            .configure(KotlinFeature.StrictNullChecks, enabled = false)
            .build()
    )
    registerModule(JavaTimeModule())

    disable(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES)
    disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS)
    disable(SerializationFeature.WRITE_DURATIONS_AS_TIMESTAMPS)

    // can't use JsonMapper.builder() with vert.x
    @Suppress("DEPRECATION")
    disable(MapperFeature.ALLOW_COERCION_OF_SCALARS)

    return this
}
