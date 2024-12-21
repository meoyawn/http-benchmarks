using System.ComponentModel.DataAnnotations;
using System.Text.Json.Serialization;
using System.Threading.Channels;
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
webApp.MapPost("/posts", async (NewPost body) =>
{
    var errs = body.Validate();
    if (errs.Count > 0) return Results.BadRequest(errs);

    var post = await Call(dbChan, body);
    return TypedResults.Created((string?)null, post);
});

webApp.Run();
return;

static async Task<TRes> Call<TReq, TRes>(Channel<Call<TReq, TRes>> chan, TReq req)
{
    var res = Channel.CreateBounded<TRes>(1);
    await chan.Writer.WriteAsync(new Call<TReq, TRes>(req, res.Writer));
    return await res.Reader.ReadAsync();
}

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
            Id: reader.GetInt64(0),
            UserId: reader.GetInt64(1),
            Content: reader.GetString(2),
            CreatedAt: reader.GetInt64(3),
            UpdatedAt: reader.GetInt64(4)
        );
    }
}

internal class CachingConnection(SqliteConnection conn) : IDisposable
{
    public enum TxMode
    {
        Immediate,
        Deferred,
        Exclusive
    }

    private readonly Dictionary<string, SqliteCommand> _cache = new();

    public static CachingConnection Open(SqliteConnection conn)
    {
        conn.Open();
        return new CachingConnection(conn);
    }

    public SqliteCommand PrepareCached(string sql)
    {
        if (_cache.TryGetValue(sql, out var cmd)) return cmd;

        cmd = conn.CreateCommand();
        cmd.CommandText = sql;
        _cache[sql] = cmd;
        return cmd;
    }

    public void Exec(string sql)
    {
        using var cmd = conn.CreateCommand();
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
        conn.Dispose();
    }
}

internal readonly record struct NewPost(
    string Content,
    string Email
)
{
    private static readonly EmailAddressAttribute EmailAttr = new();

    public IReadOnlyList<string> Validate()
    {
        var errs = new List<string>();
        if (Content.Length == 0) errs.Add("content: cannot be empty");
        if (!EmailAttr.IsValid(Email)) errs.Add($"email: invalid: {Email}");
        return errs;
    }
}

internal readonly record struct Post(
    long Id,
    [property: JsonPropertyName("user_id")]
    long UserId,
    string Content,
    [property: JsonPropertyName("created_at")]
    long CreatedAt,
    [property: JsonPropertyName("updated_at")]
    long UpdatedAt
);