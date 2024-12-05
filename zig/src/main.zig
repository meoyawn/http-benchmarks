const std = @import("std");
const httpz = @import("httpz");
const sqlite = @import("sqlite");
const mvzr = @import("mvzr");

const Allocator = std.mem.Allocator;

const String = []const u8;

const INSERT_USER = "INSERT OR IGNORE INTO users (email) VALUES (?);";

const INSERT_POST =
    \\ INSERT INTO posts   (content,   user_id)
    \\ SELECT              ?,          id
    \\ FROM        users
    \\ WHERE       email = ?
    \\ RETURNING   id, user_id, content, created_at, updated_at;
;

const Post = struct {
    id: i64,
    user_id: i64,
    content: String,
    created_at: i64,
    updated_at: i64,

    fn deinit(self: Post, alloc: Allocator) void {
        alloc.free(self.content);
    }
};

const App = struct {
    db: sqlite.Db,
    insertUser: sqlite.Statement(.{}, sqlite.ParsedQuery(INSERT_USER)),
    insertPost: sqlite.Statement(.{}, sqlite.ParsedQuery(INSERT_POST)),
    writeMutex: std.Thread.Mutex = std.Thread.Mutex{},
};

const OPEN_PRAGMAS =
    \\ PRAGMA journal_mode = wal;
    \\ PRAGMA synchronous = normal;
    \\ PRAGMA foreign_keys = on;
    \\ PRAGMA busy_timeout = 10000;
    \\ PRAGMA optimize = 0x10002;
;

fn openPragmas(conn: *sqlite.Db) !void {
    var it = std.mem.splitScalar(u8, OPEN_PRAGMAS, '\n');
    while (it.next()) |expr| {
        // exec throws because pragma returns a result
        _ = try conn.oneDynamic(void, expr, .{}, .{});
    }
}

pub fn main() !void {
    var gpa = std.heap.GeneralPurposeAllocator(.{}){};

    const allocator = gpa.allocator();

    const socket = "/tmp/benchmark.sock";

    var db = try sqlite.Db.init(.{
        .mode = sqlite.Db.Mode{ .File = "../db/db.sqlite" },
        .open_flags = .{
            .write = true,
        },
        .threading_mode = .SingleThread,
    });
    defer {
        db.exec("PRAGMA optimize;", .{}, .{}) catch |err| std.debug.panic("Couldn't PRAGMA optimize: {}", .{err});
        db.deinit();
    }

    try openPragmas(&db);

    var insertUser = try db.prepare(INSERT_USER);
    defer insertUser.deinit();

    var insertPost = try db.prepare(INSERT_POST);
    defer insertPost.deinit();

    var app = App{
        .db = db,
        .insertUser = insertUser,
        .insertPost = insertPost,
    };

    var server = try httpz.ServerApp(*App).init(
        allocator,
        .{
            .unix_path = socket,
        },
        &app,
    );
    defer {
        server.stop();
        server.deinit();
    }
    var router = server.router();
    router.post("/posts", httpPost);
    router.post("/echo", httpEcho);

    std.debug.print("Listening on {s}\n", .{socket});
    try server.listen();
}

const NewPost = struct {
    content: String,
    email: String,
};

const VALID_EMAIL = mvzr.compile("^[a-zA-Z0-9._-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$").?;

fn isValidEmail(email: String) bool {
    return VALID_EMAIL.isMatch(email);
}

fn validate(alloc: Allocator, np: NewPost) !std.ArrayList(String) {
    var errs = try std.ArrayList(String).initCapacity(alloc, 2);

    if (np.content.len == 0) {
        try errs.append("content: should not be empty");
    }

    if (!isValidEmail(np.email)) {
        const err = try std.fmt.allocPrint(alloc, "email: invalid: {s}", .{np.email});
        try errs.append(err);
    }

    return errs;
}

fn deinitList(arr: std.ArrayList(String)) void {
    for (arr.items) |value| {
        arr.allocator.free(value);
    }

    arr.deinit();
}

fn beginImmediate(conn: *sqlite.Db) !void {
    return conn.exec("BEGIN IMMEDIATE TRANSACTION;", .{}, .{});
}

fn rollback(conn: *sqlite.Db) void {
    return conn.exec("ROLLBACK;", .{}, .{}) catch |err| std.debug.panic("Couldn't ROLLBACK: {}", .{err});
}

fn commit(db: *sqlite.Db) !void {
    return db.exec("COMMIT;", .{}, .{});
}

fn transact(alloc: Allocator, app: *App, body: NewPost) !Post {
    app.writeMutex.lock();
    defer app.writeMutex.unlock();

    try beginImmediate(&app.db);
    errdefer rollback(&app.db);

    try app.insertUser.exec(.{}, .{body.email});
    app.insertUser.reset();

    const post = try app.insertPost.oneAlloc(Post, alloc, .{}, .{ body.content, body.email });
    app.insertPost.reset();

    errdefer post.?.deinit(alloc);

    try commit(&app.db);

    return post orelse error.EmptyQuery;
}

fn httpPost(app: *App, req: *httpz.Request, res: *httpz.Response) !void {
    const body: NewPost = try req.json(NewPost) orelse {
        res.status = 400;
        return res.json("POST body", .{});
    };

    const errs = try validate(req.arena, body);
    defer deinitList(errs);
    if (errs.items.len > 0) {
        res.status = 400;
        return res.json(errs.items, .{});
    }

    const post = try transact(req.arena, app, body);
    defer post.deinit(req.arena);

    res.status = 201;
    return res.json(post, .{});
}

fn httpEcho(_: *App, req: *httpz.Request, res: *httpz.Response) !void {
    const body: NewPost = try req.json(NewPost) orelse {
        res.status = 400;
        return res.json("POST body", .{});
    };
    return res.json(body, .{});
}

test "invalid post" {
    const t = std.testing;

    const errs = try validate(t.allocator, NewPost{ .content = "hello", .email = "gmail.com" });
    defer deinitList(errs);

    try t.expectEqual(1, errs.items.len);
    try t.expectEqualStrings("email: invalid: gmail.com", errs.items[0]);
}

test "valid post" {
    const t = std.testing;

    const errs = try validate(t.allocator, NewPost{ .content = "hello", .email = "foo@gmail.com" });
    defer deinitList(errs);

    try t.expectEqual(0, errs.items.len);
}

test "transaction" {
    const t = std.testing;

    var db = try sqlite.Db.init(.{
        .mode = .{ .File = "../db/db.sqlite" },
        .open_flags = .{
            .write = true,
        },
    });
    defer {
        db.exec("PRAGMA optimize;", .{}, .{}) catch |err| std.debug.panic("Couldn't PRAGMA optimize: {}", .{err});
        db.deinit();
    }

    try openPragmas(&db);

    var insertUser = try db.prepare(INSERT_USER);
    defer insertUser.deinit();

    var insertPost = try db.prepare(INSERT_POST);
    defer insertPost.deinit();

    var app = App{
        .db = db,
        .insertUser = insertUser,
        .insertPost = insertPost,
    };

    const content = "hello";

    const post = try transact(t.allocator, &app, .{ .content = content, .email = "foo@gmail.com" });
    defer post.deinit(t.allocator);

    try t.expectEqualStrings(content, post.content);
}
