using System.IO.Pipelines;
using System.Text.Json;
using System.Text.Json.Serialization.Metadata;

internal static class HttpJson
{
    public static async ValueTask<T?> ReadAsync<T>(HttpContext context, JsonTypeInfo<T> typeInfo) where T : class
    {
        if (!context.Request.HasJsonContentType())
        {
            context.Response.StatusCode = 415;
            return null;
        }
        try
        {
            var value = await context.Request.ReadFromJsonAsync(typeInfo, context.RequestAborted);
            if (value is null) context.Response.StatusCode = 400;
            return value;
        }
        catch (JsonException) { context.Response.StatusCode = 400; return null; }
        catch (BadHttpRequestException error) { context.Response.StatusCode = error.StatusCode; return null; }
    }

    public static ValueTask<FlushResult> WriteAsync<T>(HttpContext context, T value, JsonTypeInfo<T> typeInfo, int status = 200)
    {
        // Buffer ordinary JSON to supply its actual byte length instead of chunked framing.
        // This works for any model and payload size; no response data or sizes are cached.
        var bytes = JsonSerializer.SerializeToUtf8Bytes(value, typeInfo);
        context.Response.StatusCode = status;
        context.Response.ContentType = "application/json; charset=utf-8";
        context.Response.ContentLength = bytes.Length;
        return context.Response.BodyWriter.WriteAsync(bytes, context.RequestAborted);
    }
}
