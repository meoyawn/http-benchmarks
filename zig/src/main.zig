const std = @import("std");
const httpz = @import("httpz");
const sqlite = @import("sqlite");

const App = struct {
    db: sqlite.Db,
};

pub fn main() !void {
    var gpa = std.heap.GeneralPurposeAllocator(.{}){};
    const allocator = gpa.allocator();

    const socket = "/tmp/benchmark.sock";

    var db = try sqlite.Db.init(.{
        .mode = sqlite.Db.Mode{ .File = "../db/db.sqlite" },
        .open_flags = .{
            .write = true,
        },
        .threading_mode = .Serialized,
    });
    defer db.deinit();

    var insertUser = try db.prepare("INSERT INTO users (email) VALUES (?)");
    defer insertUser.deinit();

    const INSERT_POST =
        \\ INSERT INTO posts   (content,   user_id)
        \\ SELECT              ?,          id
        \\ FROM        users
        \\ WHERE       email = ?
        \\ RETURNING   id, user_id, content, created_at, updated_at;
    ;

    var insertPost = try db.prepare(INSERT_POST);
    defer insertPost.deinit();

    var server = try httpz.Server().init(allocator, .{
        .unix_path = socket,
    });
    defer {
        std.debug.print("Shutting down server\n", .{});
        // clean shutdown, finishes serving any live request
        server.stop();
        server.deinit();
    }
    var router = server.router(.{});
    router.post("/posts", newPost);

    std.debug.print("Listening on {s}\n", .{socket});

    var thread = try server.listenInNewThread();
    // thread.detach();

    while (true) {}

    thread.join();

    std.debug.print("Stopped\n", .{});
}

const Allocator = std.mem.Allocator;
const String = []const u8;

const NewPost = struct {
    content: String,
    email: String,
};

fn isValidEmail(alloc: Allocator,email: String) bool {
    // this is a very simple email validation
    // it's just to show how to use the std.regex module
    const regex = try std.regex.compile(alloc, "^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$");
    return try regex.match(email);
}

fn validate(alloc: Allocator, np: NewPost) Allocator.Error!std.ArrayList(String) {
    var errors = std.ArrayList(String).init(alloc);

    if (np.content.len == 0) {
        try errors.append("content: should not be empty");
    }
}

fn newPost(req: *httpz.Request, res: *httpz.Response) !void {
    const body = try req.json(NewPost);
}

test "simple test" {
    var list = std.ArrayList(i32).init(std.testing.allocator);
    defer list.deinit(); // try commenting this out and see if zig detects the memory leak!

    try list.append(42);
    try std.testing.expectEqual(@as(i32, 42), list.pop());
}
