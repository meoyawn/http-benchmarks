//! Standard HTTP parser with zio's coroutine implementation of std.Io.
const std = @import("std");
const zio = @import("zio");
const app = @import("app");
pub fn main(init: std.process.Init) !void {
    const options = try app.Options.parse(init);
    if (std.Io.Dir.cwd().statFile(init.io, options.socket, .{ .follow_symlinks = false })) |_| return error.SocketAlreadyExists else |err| if (err != error.FileNotFound) return err;
    var writer = try app.Writer.init(init.io, options.database);
    try writer.start();
    defer writer.stop();
    const rt = try zio.Runtime.init(app.allocator, .{ .executors = .exact(@intCast(options.workers)) });
    defer rt.deinit();
    const io = rt.io();
    var terminate = try zio.Signal.init(.terminate);
    defer terminate.deinit();
    var interrupt = try zio.Signal.init(.interrupt);
    defer interrupt.deinit();
    const addr = try std.Io.net.UnixAddress.init(options.socket);
    var listener = try addr.listen(io, .{});
    defer listener.deinit(io);
    defer std.Io.Dir.deleteFileAbsolute(init.io, options.socket) catch {};
    std.debug.print("Listening on {s} (std.http + zio, {d} executors + 1 SQLite writer)\n", .{ options.socket, options.workers });
    var serving = try rt.spawn(serve, .{ io, &listener, &writer });
    const stopped = try zio.select(.{ .terminate = &terminate, .interrupt = &interrupt, .server = &serving });
    switch (stopped) {
        .server => |result| try result,
        else => {
            serving.cancel();
            serving.join() catch |err| if (err != error.Canceled) return err;
        },
    }
}
fn serve(io: std.Io, listener: *std.Io.net.Server, writer: *app.Writer) !void {
    var group: std.Io.Group = .init;
    defer group.cancel(io);
    while (true) {
        const stream = try listener.accept(io);
        group.concurrent(io, connection, .{ io, stream, writer }) catch |err| {
            stream.close(io);
            return err;
        };
    }
}
fn connection(io: std.Io, stream: std.Io.net.Stream, writer: *app.Writer) void {
    defer stream.close(io);
    process(io, stream, writer) catch {};
}
fn send(req: *std.http.Server.Request, status: std.http.Status, body: []const u8) !void {
    try req.respond(body, .{ .status = status, .extra_headers = &.{.{ .name = "content-type", .value = "application/json" }} });
}
fn process(io: std.Io, stream: std.Io.net.Stream, writer: *app.Writer) !void {
    var read_buffer: [16384]u8 = undefined;
    var write_buffer: [16384]u8 = undefined;
    var reader = stream.reader(io, &read_buffer);
    var output = stream.writer(io, &write_buffer);
    var server = std.http.Server.init(&reader.interface, &output.interface);
    var arena = std.heap.ArenaAllocator.init(app.allocator);
    defer arena.deinit();
    while (true) {
        _ = arena.reset(.{ .retain_with_limit = 16384 });
        const alloc = arena.allocator();
        var req = try server.receiveHead();
        const keep_alive = req.head.keep_alive;
        const is_post = req.head.method == .POST;
        const path = std.mem.sliceTo(req.head.target, '?');
        const posts = std.mem.eql(u8, path, "/posts");
        const echo = std.mem.eql(u8, path, "/echo");
        var body_buffer: [4096]u8 = undefined;
        const body_reader = try req.readerExpectContinue(&body_buffer);
        const body = body_reader.allocRemaining(alloc, .limited(app.body_limit)) catch {
            try req.respond("", .{ .status = .payload_too_large, .keep_alive = false });
            return;
        };
        if (!is_post or (!posts and !echo)) {
            try send(&req, .not_found, "\"not found\"");
        } else if (app.json.parse(app.NewPost, alloc, body)) |value| {
            if (echo) {
                try send(&req, .ok, try app.json.stringify(alloc, value));
            } else if (!value.valid()) {
                try send(&req, .bad_request, "\"invalid content or email\"");
            } else {
                var event: zio.Event = .init;
                var job: app.Job = .{ .arena = .init(app.allocator), .input = value, .context = &event, .complete = completed };
                defer job.arena.deinit();
                const accepted = blk: {
                    const protection = io.swapCancelProtection(.blocked);
                    defer _ = io.swapCancelProtection(protection);
                    writer.submit(&job) catch break :blk false;
                    try event.wait();
                    break :blk true;
                };
                if (!accepted) {
                    try send(&req, .service_unavailable, "\"writer unavailable\"");
                    if (!keep_alive) return;
                    continue;
                }
                if (job.result) |post| try send(&req, .created, try app.json.stringify(alloc, post)) else |_| try send(&req, .internal_server_error, "\"write failed\"");
            }
        } else |_| try send(&req, .bad_request, "\"invalid JSON\"");
        if (!keep_alive) return;
    }
}
fn completed(job: *app.Job) void {
    const event: *zio.Event = @ptrCast(@alignCast(job.context.?));
    event.set();
}
