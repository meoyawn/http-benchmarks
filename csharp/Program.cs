using System.Collections.Generic;
using System.ComponentModel.DataAnnotations;
using System.Threading.Channels;
using System.Threading.Tasks;
using System.Threading;
using System;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.Data.Sqlite;

var dbChan = Channel.CreateUnbounded<Call<NewPost, Post>>();
var webApp = WebApplication.CreateBuilder(args).Build();
webApp.Lifetime.ApplicationStopping.Register(() => dbChan.Writer.Complete());

// ReSharper disable once AsyncVoidMethod
new Thread(async void () =>
{
    using var conn = CachingConnection.Open(new SqliteConnection("Data Source=../db/db.sqlite"));

    conn.Exec(
        """
        PRAGMA journal_mode = WAL;
        PRAGMA synchronous = NORMAL;
        PRAGMA foreign_keys = ON;
        PRAGMA busy_timeout = 10000;

        PRAGMA strict = ON;

        PRAGMA optimize = 0x10002;
        """
    );

    await foreach (var (req, res) in dbChan.Reader.ReadAllAsync())
    {
        var post = conn.Transact(CachingConnection.TxMode.Immediate, c =>
        {
            c.InsertUser(req.Email);
            return c.InsertPost(req);
        });

        await res.WriteAsync(post);
    }

    conn.Exec("PRAGMA optimize;");
}).Start();

webApp.MapPost("/echo", (NewPost post) => post);
webApp.MapPost("/posts", async Task<IValueHttpResult> (NewPost body) =>
{
    var errs = body.Validate();
    if (errs.Count > 0) return TypedResults.ValidationProblem(errs);

    var post = await dbChan.Call(body);
    return TypedResults.Created((string?)null, post);
});

webApp.Run();

internal readonly record struct Call<TReq, TRes>(
    TReq Req,
    ChannelWriter<TRes> Res
);

/**
 * LOL
 * <see href="https://github.com/dotnet/efcore/issues/24480" />
 */
internal static class DbOps
{
    public static void InsertUser(this CachingConnection conn, string email)
    {
        var cmd = conn.PrepareCached("INSERT OR IGNORE INTO users (email) VALUES (:email)");
        cmd.Parameters.Clear();
        cmd.Parameters.AddWithValue(":email", email);
        cmd.ExecuteNonQuery();
    }

    public static Post InsertPost(this CachingConnection conn, NewPost np)
    {
        var cmd = conn.PrepareCached(
            """
            INSERT INTO posts   (content,   user_id)
            SELECT              :content,   id
            FROM        users
            WHERE       email = :email
            RETURNING   id, user_id, content, created_at, updated_at
            """
        );
        cmd.Parameters.Clear();
        cmd.Parameters.AddWithValue(":content", np.Content);
        cmd.Parameters.AddWithValue(":email", np.Email);

        using var reader = cmd.ExecuteReader();
        reader.Read();
        return new Post(
            id: reader.GetInt64(0),
            user_id: reader.GetInt64(1),
            content: reader.GetString(2),
            created_at: reader.GetInt64(3),
            updated_at: reader.GetInt64(4)
        );
    }

    public static async Task<TRes> Call<TReq, TRes>(this Channel<Call<TReq, TRes>> chan, TReq req)
    {
        var res = Channel.CreateBounded<TRes>(1);
        await chan.Writer.WriteAsync(new Call<TReq, TRes>(req, res.Writer));
        return await res.Reader.ReadAsync();
    }
}

internal class CachingConnection : IDisposable
{
    public enum TxMode
    {
        Immediate,
        Deferred,
        Exclusive
    }

    private readonly Dictionary<string, SqliteCommand> _cache = new();
    private readonly SqliteConnection _conn;

    private CachingConnection(SqliteConnection conn)
    {
        _conn = conn;
    }

    public static CachingConnection Open(SqliteConnection conn)
    {
        conn.Open();
        return new CachingConnection(conn);
    }

    public SqliteCommand PrepareCached(string sql)
    {
        if (_cache.TryGetValue(sql, out var cmd)) return cmd;

        cmd = _conn.CreateCommand();
        cmd.CommandText = sql;
        _cache[sql] = cmd;
        return cmd;
    }

    public void Exec(string sql)
    {
        using var cmd = _conn.CreateCommand();
        cmd.CommandText = sql;
        cmd.ExecuteNonQuery();
    }

    public T Transact<T>(TxMode mode, Func<CachingConnection, T> fn)
    {
        PrepareCached($"BEGIN {mode}").ExecuteNonQuery();
        try
        {
            var res = fn(this);
            PrepareCached("COMMIT").ExecuteNonQuery();
            return res;
        }
        catch (Exception)
        {
            PrepareCached("ROLLBACK").ExecuteNonQuery();
            throw;
        }
    }

    public void Dispose()
    {
        foreach (var cmd in _cache.Values) cmd.Dispose();

        _cache.Clear();
        _conn.Dispose();
    }
}

internal readonly record struct NewPost(
    string Content,
    string Email
)
{
    private static readonly EmailAddressAttribute EmailAttr = new();

    public Dictionary<string, string[]> Validate()
    {
        var errs = new Dictionary<string, string[]>();

        if (Content.Length == 0) errs["content"] = ["Cannot be empty"];
        if (!EmailAttr.IsValid(Email)) errs["email"] = [$"Invalid: {Email}"];
        return errs;
    }
}

internal readonly record struct Post(
    // ReSharper disable InconsistentNaming
    // ReSharper disable NotAccessedPositionalProperty.Global
    long id,
    long user_id,
    string content,
    long created_at,
    long updated_at
    // ReSharper restore NotAccessedPositionalProperty.Global
    // ReSharper restore InconsistentNaming
);

namespace Tests
{
    using System.Text.Json;
    using Xunit;

    public class NewPostTests
    {
        [Fact]
        public void NewPost_Serialization_Deserialization_Works()
        {
            var original = new NewPost(Content: "Sample content", Email: "test@example.com");
            var json = JsonSerializer.Serialize(original);
            var deserialized = JsonSerializer.Deserialize<NewPost>(json);

            Assert.Equal(original, deserialized);
        }
    }
}