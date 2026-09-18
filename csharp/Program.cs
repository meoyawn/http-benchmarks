using Microsoft.AspNetCore.Server.Kestrel.Core;
using Microsoft.AspNetCore.Server.Kestrel.Transport.Sockets;
using Microsoft.Extensions.Options;
using System.Text.Json.Serialization.Metadata;

// The pool serves asynchronous HTTP work; SQLite owns a separate OS thread.
var databasePath = "../db/db.sqlite";
var socketPath = "/tmp/benchmark.sock";
var threads = 2;
var ioThreads = 3;
var checkConfig = false;
for (var index = 0; index < args.Length; index++)
{
    var name = args[index];
    if (name == "-check-config") { checkConfig = true; continue; }
    if (++index == args.Length) throw new ArgumentException($"Missing value for {name}");
    switch (name)
    {
        case "-db": databasePath = args[index]; break;
        case "-socket": socketPath = args[index]; break;
        case "-threads": threads = int.Parse(args[index]); break;
        case "-io-threads": ioThreads = int.Parse(args[index]); break;
        default: throw new ArgumentException($"Unknown option: {name}");
    }
}
// ConsoleLifetime can occupy a pool thread during shutdown; one thread stalls it.
if (threads < 2 || !ThreadPool.SetMinThreads(threads, threads) || !ThreadPool.SetMaxThreads(threads, threads))
    throw new ArgumentException("At least two HTTP pool threads are required");
if (ioThreads < 1) throw new ArgumentException("At least one socket I/O thread is required");
// Configure the socket engines before the first managed socket is created.
// HTTP parsing/serialization can run on I/O threads; SQLite stays on its writer.
Environment.SetEnvironmentVariable("DOTNET_SYSTEM_NET_SOCKETS_INLINE_COMPLETIONS", "1");
Environment.SetEnvironmentVariable("DOTNET_SYSTEM_NET_SOCKETS_THREAD_COUNT", ioThreads.ToString(System.Globalization.CultureInfo.InvariantCulture));
socketPath = Path.GetFullPath(socketPath);
SqliteRuntime.Initialize();
if (checkConfig)
{
    using var database = new Database(databasePath);
    Console.WriteLine(database.Configuration());
    return;
}
if (File.Exists(socketPath)) throw new IOException($"Socket already exists: {socketPath}");
using var writer = new DatabaseWriter(databasePath);
var builder = WebApplication.CreateSlimBuilder();
builder.Logging.ClearProviders();
builder.WebHost.ConfigureKestrel(options =>
{
    options.AddServerHeader = false;
    options.ListenUnixSocket(socketPath, listen => listen.Protocols = HttpProtocols.Http1);
});
builder.Services.ConfigureHttpJsonOptions(options => options.SerializerOptions.TypeInfoResolverChain.Insert(0, JsonModel.Default));
builder.Services.Configure<SocketTransportOptions>(options => options.UnsafePreferInlineScheduling = true);
var app = builder.Build();
// Resolve the normal ASP.NET request options once, preserving its JSON defaults.
var requestOptions = app.Services.GetRequiredService<IOptions<Microsoft.AspNetCore.Http.Json.JsonOptions>>().Value.SerializerOptions;
var requestTypeInfo = (JsonTypeInfo<NewPost>)requestOptions.GetTypeInfo(typeof(NewPost));
RequestDelegate echo = async context =>
{
    var body = await HttpJson.ReadAsync(context, requestTypeInfo);
    if (body is null) return;
    if (body.Content is null || body.Email is null) { context.Response.StatusCode = 400; return; }
    await HttpJson.WriteAsync(context, body, JsonModel.Default.NewPost);
};
app.MapPost("/echo", echo);
RequestDelegate posts = async context =>
{
    var body = await HttpJson.ReadAsync(context, requestTypeInfo);
    if (body is null) return;
    if (!body.IsValid()) { context.Response.StatusCode = 400; return; }
    var post = await writer.WriteAsync(body);
    await HttpJson.WriteAsync(context, post, JsonModel.Default.Post, 201);
};
app.MapPost("/posts", posts);
try
{
    await app.StartAsync();
    Console.WriteLine($"Listening on {socketPath} (SQLite {SqliteRuntime.Version}, {threads} pool + {ioThreads} socket I/O threads + 1 writer)");
    await app.WaitForShutdownAsync();
}
finally
{
    await app.DisposeAsync();
    File.Delete(socketPath);
}
