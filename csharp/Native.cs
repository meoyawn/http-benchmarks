using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;
using System.Text;

internal static partial class Native
{
    internal const int Ok = 0, Row = 100, Done = 101;
    internal const int OpenReadWrite = 2, OpenNoMutex = 0x8000;
    internal const uint PreparePersistent = 1;
    [LibraryImport("sqlite3")]
    internal static partial int sqlite3_initialize();
    [LibraryImport("sqlite3")]
    internal static partial nint sqlite3_libversion();
    [LibraryImport("sqlite3", StringMarshalling = StringMarshalling.Utf8)]
    internal static partial int sqlite3_open_v2(string path, out nint db, int flags, string? vfs);
    [LibraryImport("sqlite3")]
    internal static partial int sqlite3_close_v2(nint db);
    [LibraryImport("sqlite3", StringMarshalling = StringMarshalling.Utf8)]
    private static partial int sqlite3_exec(nint db, string sql, nint callback, nint state, nint error);
    internal static int Exec(nint db, string sql) => sqlite3_exec(db, sql, 0, 0, 0);
    [LibraryImport("sqlite3", StringMarshalling = StringMarshalling.Utf8)]
    internal static partial int sqlite3_prepare_v3(nint db, string sql, int size, uint flags, out nint stmt, nint tail);
    [LibraryImport("sqlite3")]
    internal static partial int sqlite3_finalize(nint stmt);
    [LibraryImport("sqlite3")]
    internal static partial int sqlite3_step(nint stmt);
    [LibraryImport("sqlite3")]
    internal static partial int sqlite3_reset(nint stmt);
    [LibraryImport("sqlite3")]
    internal static partial int sqlite3_clear_bindings(nint stmt);
    // Only nonblocking scalar accessors skip the GC transition. Stepping,
    // binding, conversion and transaction/IO calls retain normal transitions.
    [LibraryImport("sqlite3"), SuppressGCTransition]
    internal static partial long sqlite3_column_int64(nint stmt, int column);
    [LibraryImport("sqlite3")]
    private static partial nint sqlite3_column_blob(nint stmt, int column);
    [LibraryImport("sqlite3"), SuppressGCTransition]
    private static partial int sqlite3_column_bytes(nint stmt, int column);
    [LibraryImport("sqlite3"), SuppressGCTransition]
    internal static partial int sqlite3_get_autocommit(nint db);
    [LibraryImport("sqlite3"), SuppressGCTransition]
    internal static partial nint sqlite3_errmsg(nint db);
    [LibraryImport("sqlite3")]
    private static unsafe partial int sqlite3_bind_text(nint stmt, int index, byte* text, int length, nint destructor);
    internal static unsafe int BindText(nint stmt, int index, ReadOnlySpan<byte> bytes)
    {
        fixed (byte* pointer = bytes) return sqlite3_bind_text(stmt, index, pointer, bytes.Length, -1);
    }
    internal static unsafe string ReadText(nint stmt, int index)
    {
        var pointer = sqlite3_column_blob(stmt, index);
        var length = sqlite3_column_bytes(stmt, index);
        return Encoding.UTF8.GetString(new ReadOnlySpan<byte>((void*)pointer, length));
    }
}
