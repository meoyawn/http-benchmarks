const std = @import("std");
const http = @import("dusty");
const zio = @import("zio");
const app = @import("app");
const Server = http.Server(app.Writer);

pub fn main(init: std.process.Init) !void {
    const options = try app.Options.parse(init);
    if (std.Io.Dir.cwd().statFile(init.io, options.socket, .{ .follow_symlinks = false })) |_| return error.SocketAlreadyExists else |err| if (err != error.FileNotFound) return err;
    var writer = try app.Writer.init(init.io, options.database);
    try writer.start();
    defer writer.stop();
    const rt = try zio.Runtime.init(app.allocator, .{ .executors = .exact(@intCast(options.workers)) });
    defer rt.deinit();
    const io = rt.io();
    var signal = try zio.Signal.init(.terminate);
    defer signal.deinit();
    var interrupt = try zio.Signal.init(.interrupt);
    defer interrupt.deinit();
    var server = Server.init(app.allocator, io, .{ .request = .{ .max_body_size = app.body_limit } }, &writer);
    defer server.deinit();
    server.router.post("/echo", echo);
    server.router.post("/posts", posts);
    const address: http.Address = .{ .unix = try std.Io.net.UnixAddress.init(options.socket) };
    var serving = try rt.spawn(Server.listen, .{ &server, address });
    defer std.Io.Dir.deleteFileAbsolute(init.io, options.socket) catch {};
    // Dusty logs Listening only after bind and its ready event. Its server task
    // drains active handlers on cancellation; protected writes complete first.
    const stopped = try zio.select(.{ .terminate = &signal, .interrupt = &interrupt, .server = &serving });
    switch (stopped) {
        .server => |result| try result,
        else => {
            serving.cancel();
            serving.join() catch |err| if (err != error.Canceled) return err;
        },
    }
}
fn input(req: *http.Request, res: *http.Response) !?app.NewPost {
    const body = (try req.body()) orelse "";
    return app.json.parse(app.NewPost, req.arena, body) catch {
        res.status = .bad_request;
        res.body = "\"invalid JSON\"";
        return null;
    };
}
fn echo(_: *app.Writer, req: *http.Request, res: *http.Response) !void {
    const value = (try input(req, res)) orelse return;
    try res.header("content-type", "application/json");
    res.body = try app.json.stringify(req.arena, value);
}
fn completed(job: *app.Job) void {
    const event: *zio.Event = @ptrCast(@alignCast(job.context.?));
    event.set();
}
fn posts(writer: *app.Writer, req: *http.Request, res: *http.Response) !void {
    const value = (try input(req, res)) orelse return;
    if (!value.valid()) {
        res.status = .bad_request;
        return;
    }
    var event: zio.Event = .init;
    var job: app.Job = .{ .arena = .init(app.allocator), .input = value, .context = &event, .complete = completed };
    defer job.arena.deinit();
    // Keep the coroutine, request strings and reply alive until the writer is
    // finished, even on timeout/disconnect/shutdown. Waiting yields the executor.
    const protection = req.io.swapCancelProtection(.blocked);
    defer _ = req.io.swapCancelProtection(protection);
    writer.submit(&job) catch {
        res.status = .service_unavailable;
        return;
    };
    try event.wait();
    const result = job.result catch {
        res.status = .internal_server_error;
        return;
    };
    try res.header("content-type", "application/json");
    res.body = try app.json.stringify(req.arena, result);
    res.status = .created;
}
