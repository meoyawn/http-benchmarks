const std = @import("std");
const app = @import("app.zig");
const sqlite = @import("sqlite");
const c = sqlite.c;
const use_zqlite = @import("options").sqlite == .zqlite;
const USER_SQL = "INSERT OR IGNORE INTO users (email) VALUES (:email)";
const POST_SQL = "INSERT INTO posts (content, user_id) SELECT :content, id FROM users WHERE email IS :email RETURNING id, user_id, content, created_at, updated_at";
const Empty = struct {};
const UserParams = if (use_zqlite) void else struct { email: sqlite.Text };
const PostParams = if (use_zqlite) void else struct { content: sqlite.Text, email: sqlite.Text };
const Row = if (use_zqlite) void else struct { id: i64, user_id: i64, content: sqlite.Text, created_at: i64, updated_at: i64 };
fn Statement(comptime P: type, comptime R: type) type {
    return if (use_zqlite) sqlite.Stmt else sqlite.Statement(P, R);
}
const Conn = if (use_zqlite) sqlite.Conn else sqlite.Database;
pub const Database = struct {
    conn: Conn,
    begin: Statement(Empty, void),
    user: Statement(UserParams, void),
    post: Statement(PostParams, Row),
    commit: Statement(Empty, void),
    pub fn open(path: [:0]const u8) !Database {
        const raw_conn = try app.runtime.open(path);
        const conn: Conn = if (use_zqlite) .{ .conn = raw_conn } else .{ .ptr = raw_conn };
        errdefer conn.close();
        return fromConnection(conn);
    }
    fn fromConnection(conn: Conn) !Database {
        const begin = try prepare(conn, Empty, void, "BEGIN IMMEDIATE");
        errdefer finalize(begin);
        const user = try prepare(conn, UserParams, void, USER_SQL);
        errdefer finalize(user);
        const post = try prepare(conn, PostParams, Row, POST_SQL);
        errdefer finalize(post);
        const commit = try prepare(conn, Empty, void, "COMMIT");
        return .{ .conn = conn, .begin = begin, .user = user, .post = post, .commit = commit };
    }
    pub fn deinit(self: *Database) void {
        finalize(self.begin);
        finalize(self.user);
        finalize(self.post);
        finalize(self.commit);
        _ = c.sqlite3_exec(self.raw(), "PRAGMA optimize", null, null, null);
        self.conn.close();
    }
    pub fn raw(self: *Database) ?*c.sqlite3 {
        return if (use_zqlite) self.conn.conn else self.conn.ptr;
    }
    pub fn transact(self: *Database, alloc: std.mem.Allocator, input: app.NewPost) !app.Post {
        try execEmpty(self.begin);
        errdefer if (c.sqlite3_get_autocommit(self.raw()) == 0) {
            if (c.sqlite3_exec(self.raw(), "ROLLBACK", null, null, null) != c.SQLITE_OK) @panic("SQLite rollback failed");
        };
        const result = try self.insert(alloc, input);
        errdefer alloc.free(result.content);
        try execEmpty(self.commit);
        return result;
    }
    fn insert(self: *Database, alloc: std.mem.Allocator, input: app.NewPost) !app.Post {
        if (use_zqlite) {
            defer {
                self.user.reset() catch {};
                self.user.clearBindings() catch {};
            }
            try self.user.bind(.{input.email});
            try self.user.stepToCompletion();
            defer {
                self.post.reset() catch {};
                self.post.clearBindings() catch {};
            }
            try self.post.bind(.{ input.content, input.email });
            if (!try self.post.step()) return error.NoRow;
            const result: app.Post = .{ .id = self.post.int(0), .user_id = self.post.int(1), .content = try alloc.dupe(u8, self.post.text(2)), .created_at = self.post.int(3), .updated_at = self.post.int(4) };
            errdefer alloc.free(result.content);
            if (try self.post.step()) return error.ExtraRow;
            return result;
        } else {
            try self.user.exec(.{ .email = sqlite.text(input.email) });
            defer self.post.reset();
            try self.post.bind(.{ .content = sqlite.text(input.content), .email = sqlite.text(input.email) });
            const row = (try self.post.step()) orelse return error.NoRow;
            const result: app.Post = .{ .id = row.id, .user_id = row.user_id, .content = try alloc.dupe(u8, row.content.data), .created_at = row.created_at, .updated_at = row.updated_at };
            errdefer alloc.free(result.content);
            if (try self.post.step() != null) return error.ExtraRow;
            return result;
        }
    }
};
fn prepare(conn: Conn, comptime P: type, comptime R: type, sql: []const u8) !Statement(P, R) {
    return if (use_zqlite) conn.prepare(sql) else conn.prepare(P, R, sql);
}
fn finalize(stmt: anytype) void {
    if (use_zqlite) stmt.deinit() else stmt.finalize();
}
fn execEmpty(stmt: Statement(Empty, void)) !void {
    if (use_zqlite) {
        defer stmt.reset() catch {};
        try stmt.stepToCompletion();
    } else try stmt.exec(.{});
}
test "commit, returned content ownership and rollback recovery" {
    const raw_conn = try app.runtime.open(":memory:");
    try std.testing.expectEqual(c.SQLITE_OK, c.sqlite3_exec(raw_conn, @embedFile("schema"), null, null, null));
    const conn: Conn = if (use_zqlite) .{ .conn = raw_conn } else .{ .ptr = raw_conn };
    var db = try Database.fromConnection(conn);
    defer db.deinit();
    const input: app.NewPost = .{ .content = "snow 雪\x00tail", .email = "a+b@example.com" };
    const post = try db.transact(std.testing.allocator, input);
    defer std.testing.allocator.free(post.content);
    const again = try db.transact(std.testing.allocator, input);
    defer std.testing.allocator.free(again.content);
    try std.testing.expectEqualStrings(input.content, post.content);
    try std.testing.expectEqual(post.user_id, again.user_id);
    try std.testing.expectEqual(post.id + 1, again.id);
    try std.testing.expect(post.created_at == post.updated_at and post.created_at > 1700000000000);
    if (use_zqlite) {
        try std.testing.expectEqual(c.SQLITE_OK, c.sqlite3_exec(raw_conn, "CREATE TRIGGER fail_post BEFORE INSERT ON posts BEGIN SELECT RAISE(ABORT, 'test'); END", null, null, null));
        if (db.transact(std.testing.allocator, .{ .content = "failed", .email = "rollback@example.com" })) |_| return error.ExpectedFailure else |_| {}
        try std.testing.expect(c.sqlite3_get_autocommit(raw_conn) != 0);
        try std.testing.expectEqual(c.SQLITE_OK, c.sqlite3_exec(raw_conn, "DROP TRIGGER fail_post", null, null, null));
        const recovered = try db.transact(std.testing.allocator, input);
        defer std.testing.allocator.free(recovered.content);
        try std.testing.expectEqual(again.id + 1, recovered.id);
    }
}
