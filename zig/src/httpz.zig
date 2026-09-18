//! Comparison adapter: http.zig supports kqueue but its ordinary handlers are
//! synchronous. This candidate waits on the same writer from a request thread.
const std = @import("std");
const httpz = @import("httpz");
const app = @import("app");
var io: std.Io = undefined;
pub fn main(init: std.process.Init) !void {
    io = init.io;
    const options = try app.Options.parse(init);
    if (std.Io.Dir.cwd().statFile(io, options.socket, .{ .follow_symlinks = false })) |_| return error.SocketAlreadyExists else |err| if (err != error.FileNotFound) return err;
    var writer = try app.Writer.init(io, options.database);
    try writer.start();
    defer writer.stop();
    var server = try httpz.Server(*app.Writer).init(io, app.allocator, .{ .address = .{ .unix = options.socket }, .workers = .{ .count = 1 }, .thread_pool = .{ .count = options.workers, .buffer_size = 16384 }, .request = .{ .max_body_size = app.body_limit } }, &writer);
    defer server.deinit();
    var router = try server.router(.{});
    router.post("/posts", posts, .{});
    router.post("/echo", echo, .{});
    const thread = try server.listenInNewThread();
    std.debug.print("Listening on {s}\n", .{options.socket});
    thread.join();
}
fn parse(req: *httpz.Request, res: *httpz.Response) ?app.NewPost {
    return app.json.parse(app.NewPost, req.arena, req.body() orelse "") catch {
        res.status = 400;
        res.body = "\"invalid JSON\"";
        return null;
    };
}
fn echo(_: *app.Writer, req: *httpz.Request, res: *httpz.Response) !void {
    const input = parse(req, res) orelse return;
    res.content_type = .JSON;
    res.body = try app.json.stringify(req.arena, input);
}
const Reply = struct { mutex: std.Io.Mutex = .init, ready: std.Io.Condition = .init, done: bool = false };
fn completed(job: *app.Job) void {
    const reply: *Reply = @ptrCast(@alignCast(job.context.?));
    reply.mutex.lockUncancelable(io);
    reply.done = true;
    reply.ready.signal(io);
    reply.mutex.unlock(io);
}
fn posts(writer: *app.Writer, req: *httpz.Request, res: *httpz.Response) !void {
    const input = parse(req, res) orelse return;
    if (!input.valid()) {
        res.status = 400;
        return;
    }
    var reply: Reply = .{};
    var job: app.Job = .{ .arena = .init(app.allocator), .input = input, .context = &reply, .complete = completed };
    defer job.arena.deinit();
    try writer.submit(&job);
    reply.mutex.lockUncancelable(io);
    while (!reply.done) reply.ready.waitUncancelable(io, &reply.mutex);
    reply.mutex.unlock(io);
    res.body = try app.json.stringify(req.arena, try job.result);
    res.status = 201;
    res.content_type = .JSON;
}
