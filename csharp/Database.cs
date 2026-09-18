using System.Text.Json;
using System.Runtime.InteropServices;

internal sealed class Database : IDisposable
{
    internal const string Pragmas = """
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=NORMAL;
        PRAGMA foreign_keys=ON;
        PRAGMA busy_timeout=10000;
        PRAGMA cache_size=-2000;
        PRAGMA wal_autocheckpoint=1000;
        PRAGMA temp_store=MEMORY;
        PRAGMA mmap_size=0;
        PRAGMA optimize=0x10002;
        """;
    internal const string UserSql = "INSERT OR IGNORE INTO users (email) VALUES (?1)";
    internal const string PostSql = """
        INSERT INTO posts (content, user_id)
        SELECT ?1, id FROM users WHERE email IS ?2
        RETURNING id, user_id, content, created_at, updated_at
        """;
    private readonly nint connection;
    private readonly nint begin, user, post, commit, rollback;

    public Database(string path)
    {
        var opened = Native.sqlite3_open_v2(path, out connection, Native.OpenReadWrite | Native.OpenNoMutex, null);
        try
        {
            Check(opened);
            Check(Native.Exec(connection, Pragmas));
            begin = Prepare("BEGIN IMMEDIATE");
            user = Prepare(UserSql);
            post = Prepare(PostSql);
            commit = Prepare("COMMIT");
            rollback = Prepare("ROLLBACK");
        }
        catch { CloseStatements(); Native.sqlite3_close_v2(connection); throw; }
    }

    private nint Prepare(string sql)
    {
        var result = Native.sqlite3_prepare_v3(connection, sql, -1, Native.PreparePersistent, out var statement, 0);
        if (result != Native.Ok)
        {
            Native.sqlite3_finalize(statement);
            Check(result);
        }
        return statement;
    }

    private void Check(int code)
    {
        if (code != Native.Ok) throw new InvalidOperationException($"SQLite {code}: {Marshal.PtrToStringUTF8(Native.sqlite3_errmsg(connection))}");
    }

    private void Done(nint statement)
    {
        var result = Native.sqlite3_step(statement);
        var reset = Native.sqlite3_reset(statement);
        if (result != Native.Done) Check(result);
        Check(reset);
    }

    public Post Insert(NewPost body)
    {
        Done(begin);
        try
        {
            BindText(user, 1, body.Email);
            Done(user);
            BindText(post, 1, body.Content);
            BindText(post, 2, body.Email);
            var result = Native.sqlite3_step(post);
            if (result != Native.Row) Check(result);
            var row = new Post(Native.sqlite3_column_int64(post, 0), Native.sqlite3_column_int64(post, 1),
                Native.ReadText(post, 2), Native.sqlite3_column_int64(post, 3), Native.sqlite3_column_int64(post, 4));
            Done(post); // Exhaust RETURNING and reset the statement before COMMIT.
            Done(commit);
            return row;
        }
        catch
        {
            Native.sqlite3_reset(user);
            Native.sqlite3_reset(post);
            if (Native.sqlite3_get_autocommit(connection) == 0) Done(rollback);
            throw;
        }
        finally
        {
            Native.sqlite3_clear_bindings(user);
            Native.sqlite3_clear_bindings(post);
        }
    }

    private void BindText(nint statement, int index, string text)
    {
        // SQLite copies these length-delimited bytes (SQLITE_TRANSIENT).
        // Explicit lengths preserve embedded NUL characters at every size.
        var length = System.Text.Encoding.UTF8.GetByteCount(text);
        byte[]? rented = null;
        Span<byte> bytes = length <= 512 ? stackalloc byte[length]
            : (rented = System.Buffers.ArrayPool<byte>.Shared.Rent(length)).AsSpan(0, length);
        try
        {
            System.Text.Encoding.UTF8.GetBytes(text, bytes);
            Check(Native.BindText(statement, index, bytes));
        }
        finally { if (rented is not null) System.Buffers.ArrayPool<byte>.Shared.Return(rented); }
    }

    public string Configuration()
    {
        var pragmas = new Dictionary<string, string>();
        foreach (var name in new[] { "journal_mode", "synchronous", "foreign_keys", "busy_timeout", "cache_size", "wal_autocheckpoint", "temp_store", "mmap_size", "page_size" })
        {
            var statement = Prepare("PRAGMA " + name);
            try
            {
                if (Native.sqlite3_step(statement) != Native.Row) throw new InvalidOperationException(name);
                pragmas[name] = Native.ReadText(statement, 0);
            }
            finally { Native.sqlite3_finalize(statement); }
        }
        var options = new List<string>();
        var compile = Prepare("PRAGMA compile_options");
        try
        {
            while (Native.sqlite3_step(compile) == Native.Row) options.Add(Native.ReadText(compile, 0));
        }
        finally { Native.sqlite3_finalize(compile); }
        return JsonSerializer.Serialize(new DatabaseConfiguration(SqliteRuntime.Version, pragmas, options.ToArray()), JsonModel.Default.DatabaseConfiguration);
    }

    private void CloseStatements()
    {
        Native.sqlite3_finalize(begin); Native.sqlite3_finalize(user); Native.sqlite3_finalize(post);
        Native.sqlite3_finalize(commit); Native.sqlite3_finalize(rollback);
    }

    public void Dispose()
    {
        CloseStatements();
        try { Check(Native.Exec(connection, "PRAGMA optimize")); }
        finally { Native.sqlite3_close_v2(connection); }
    }
}
