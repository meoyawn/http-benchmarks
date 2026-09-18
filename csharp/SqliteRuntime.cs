using System.Runtime.InteropServices;

internal static partial class SqliteRuntime
{
    public const string Version = "3.53.4";
    private static nint pagePool;

    public static void Initialize()
    {
        // The assembly-aware loader also searches the single-file bundle's native
        // extraction directory; ordinary/AOT publishes load SQLite beside the app.
        var library = NativeLibrary.Load(
            OperatingSystem.IsMacOS() ? "libsqlite3.dylib" : "libsqlite3.so",
            typeof(SqliteRuntime).Assembly, DllImportSearchPath.AssemblyDirectory);
        NativeLibrary.SetDllImportResolver(typeof(SqliteRuntime).Assembly, (name, _, _) => name == "sqlite3" ? library : 0);
        Check(ConfigInt(9, 0)); // SQLITE_CONFIG_MEMSTATUS
        Check(ConfigOut(24, out var header)); // SQLITE_CONFIG_PCACHE_HDRSZ
        var slotSize = (4096 + header + 7) & ~7;
        // Eight-byte-aligned unmanaged storage must outlive every connection.
        // SQLite and this pool intentionally remain initialized until process exit.
        pagePool = Marshal.AllocHGlobal(slotSize * 1024);
        Check(ConfigPageCache(7, pagePool, slotSize, 1024));
        Check(Native.sqlite3_initialize());
        if (Marshal.PtrToStringUTF8(Native.sqlite3_libversion()) != Version)
            throw new InvalidOperationException("Shared SQLite version mismatch");
    }

    private static void Check(int code)
    {
        if (code != 0) throw new InvalidOperationException($"SQLite configuration failed: {code}");
    }

    // Fixed-signature C shims handle sqlite3_config's varargs ABI on Apple ARM64.
    [LibraryImport("sqlite3", EntryPoint = "benchmark_config_int")]
    private static partial int ConfigInt(int option, int value);
    [LibraryImport("sqlite3", EntryPoint = "benchmark_config_out")]
    private static partial int ConfigOut(int option, out int value);
    [LibraryImport("sqlite3", EntryPoint = "benchmark_config_pagecache")]
    private static partial int ConfigPageCache(int option, nint buffer, int size, int count);
}
