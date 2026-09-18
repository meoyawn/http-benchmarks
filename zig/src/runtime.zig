const std = @import("std");
const sqlite = @import("sqlite");
pub const c = sqlite.c;
const allocator = std.heap.smp_allocator;
const Runtime = struct { memstatus: bool, pagecache_slots: c_int, page_bytes: c_int, connection_mutex: bool };
const Config = struct { version: []const u8, runtime: Runtime, pragmas: std.json.Value };
// Initialized by main before any worker exists; subsequent test opens are serial.
var initialized = false;
var config: Config = undefined;
pub fn initialize() !void {
    if (initialized) return;
    try initializeInner();
    initialized = true;
}
fn check(rc: c_int) !void {
    if (rc != c.SQLITE_OK) return error.SqliteConfiguration;
}
fn initializeInner() !void {
    config = try std.json.parseFromSliceLeaky(Config, allocator, @embedFile("sqlite_config"), .{ .ignore_unknown_fields = true });
    try check(c.sqlite3_config(c.SQLITE_CONFIG_MEMSTATUS, @as(c_int, @intFromBool(config.runtime.memstatus))));
    var header: c_int = 0;
    try check(c.sqlite3_config(c.SQLITE_CONFIG_PCACHE_HDRSZ, &header));
    const slot = (config.runtime.page_bytes + header + 7) & ~@as(c_int, 7);
    // SQLite borrows this aligned pool for the entire process lifetime.
    const pool = try allocator.alloc(u64, @intCast(@divExact(slot, 8) * config.runtime.pagecache_slots));
    @memset(pool, 0);
    try check(c.sqlite3_config(c.SQLITE_CONFIG_PAGECACHE, pool.ptr, slot, config.runtime.pagecache_slots));
    try check(c.sqlite3_initialize());
    if (!std.mem.eql(u8, std.mem.span(c.sqlite3_libversion()), config.version)) return error.WrongSqliteVersion;
}
pub fn open(path: [:0]const u8) !*c.sqlite3 {
    try initialize();
    var db: ?*c.sqlite3 = null;
    const flags = c.SQLITE_OPEN_READWRITE | c.SQLITE_OPEN_CREATE | (if (config.runtime.connection_mutex) @as(c_int, c.SQLITE_OPEN_FULLMUTEX) else @as(c_int, c.SQLITE_OPEN_NOMUTEX));
    const rc = c.sqlite3_open_v2(path, &db, flags, null);
    errdefer if (db) |conn| {
        _ = c.sqlite3_close_v2(conn);
    };
    try check(rc);
    const page_sql = try std.fmt.allocPrintSentinel(allocator, "PRAGMA page_size={d}", .{config.runtime.page_bytes}, 0);
    defer allocator.free(page_sql);
    try check(c.sqlite3_exec(db, page_sql, null, null, null));
    var pragmas = config.pragmas.object.iterator();
    while (pragmas.next()) |entry| {
        const sql = switch (entry.value_ptr.*) {
            .string => |value| try std.fmt.allocPrintSentinel(allocator, "PRAGMA {s}={s}", .{ entry.key_ptr.*, value }, 0),
            .integer => |value| try std.fmt.allocPrintSentinel(allocator, "PRAGMA {s}={d}", .{ entry.key_ptr.*, value }, 0),
            else => return error.InvalidPragma,
        };
        defer allocator.free(sql);
        try check(c.sqlite3_exec(db, sql, null, null, null));
    }
    try check(c.sqlite3_exec(db, "PRAGMA optimize=0x10002", null, null, null));
    return db.?;
}
pub fn snapshot() !void {
    const db = try open(":memory:");
    defer _ = c.sqlite3_close_v2(db);
    var stmt: ?*c.sqlite3_stmt = null;
    try check(c.sqlite3_prepare_v2(db, "PRAGMA compile_options", -1, &stmt, null));
    defer _ = c.sqlite3_finalize(stmt);
    var options: std.ArrayList([]const u8) = .empty;
    defer options.deinit(allocator);
    while (c.sqlite3_step(stmt) == c.SQLITE_ROW) try options.append(allocator, std.mem.span(c.sqlite3_column_text(stmt, 0)));
    const encoded = try std.json.Stringify.valueAlloc(allocator, .{ .version = config.version, .compile_options = options.items, .runtime = config.runtime, .pragmas = config.pragmas }, .{});
    defer allocator.free(encoded);
    std.debug.print("{s}\n", .{encoded});
}
