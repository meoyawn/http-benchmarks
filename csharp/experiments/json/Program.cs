using System.Diagnostics;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

var input = "{ \"content\": \"oha benchmark\", \"email\": \"oha@gmail.com\" }"u8.ToArray();
var options = new JsonSerializerOptions();
var text = Encoding.UTF8.GetString(input);
const int count = 1_000_000;
var methods = new (string Name, Func<bool, byte[]> Run)[]
{
    ("System.Text.Json generated", posts => {
        var body = JsonSerializer.Deserialize(input, Model.Default.Request)!;
        return posts ? JsonSerializer.SerializeToUtf8Bytes(new Response(123, 1, body.content, 1789000000000, 1789000000000), Model.Default.Response)
            : JsonSerializer.SerializeToUtf8Bytes(body, Model.Default.Request);
    }),
    ("System.Text.Json reflection", posts => {
        var body = JsonSerializer.Deserialize<Request>(input, options)!;
        return posts ? JsonSerializer.SerializeToUtf8Bytes(new Response(123, 1, body.content, 1789000000000, 1789000000000), options)
            : JsonSerializer.SerializeToUtf8Bytes(body, options);
    }),
    ("SpanJson 4.2.1", posts => {
        var body = SpanJson.JsonSerializer.Generic.Utf8.Deserialize<Request>(input);
        return posts ? SpanJson.JsonSerializer.Generic.Utf8.Serialize(new Response(123, 1, body.content, 1789000000000, 1789000000000))
            : SpanJson.JsonSerializer.Generic.Utf8.Serialize(body);
    }),
    ("Newtonsoft.Json 13.0.4", posts => {
        var body = Newtonsoft.Json.JsonConvert.DeserializeObject<Request>(Encoding.UTF8.GetString(input))!;
        return Encoding.UTF8.GetBytes(posts ? Newtonsoft.Json.JsonConvert.SerializeObject(new Response(123, 1, body.content, 1789000000000, 1789000000000))
            : Newtonsoft.Json.JsonConvert.SerializeObject(body));
    }),
};
foreach (var method in methods)
    for (var i = 0; i < 100_000; i++) method.Run(i % 2 == 0);
var results = new List<object>();
for (var round = 0; round < 5; round++)
    for (var j = 0; j < methods.Length; j++)
        foreach (var posts in new[] { true, false })
        {
            var method = methods[(j + round) % methods.Length];
            long checksum = 0;
            var before = GC.GetAllocatedBytesForCurrentThread();
            var timer = Stopwatch.StartNew();
            for (var i = 0; i < count; i++) checksum += method.Run(posts).Length;
            timer.Stop();
            var allocated = GC.GetAllocatedBytesForCurrentThread() - before;
            var result = new { method.Name, round, endpoint = posts ? "posts" : "echo", ns = timer.Elapsed.TotalNanoseconds / count, bytes = (double)allocated / count, checksum };
            results.Add(result);
            Console.Error.WriteLine(JsonSerializer.Serialize(result));
        }
Console.WriteLine(JsonSerializer.Serialize(results));
public sealed record Request(string content, string email);
public sealed record Response(long id, long user_id, string content, long created_at, long updated_at);
[JsonSerializable(typeof(Request))]
[JsonSerializable(typeof(Response))]
internal partial class Model : JsonSerializerContext;
