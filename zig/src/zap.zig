const std = @import("std");
const zap = @import("zap");
const fio = zap.fio;
const app = @import("app");

var writer: *app.Writer = undefined;
var options: app.Options = undefined;
// Public facil.io lifecycle API, not re-exported by Zap.
extern fn fio_state_callback_add(kind: c_int, callback: *const fn (?*anyopaque) callconv(.c) void, arg: ?*anyopaque) void;
const on_start = 6;
const on_shutdown = 8;

pub fn main(init: std.process.Init) !void {
    options = try app.Options.parse(init);
    if (std.Io.Dir.cwd().statFile(init.io, options.socket, .{ .follow_symlinks = false })) |_| return error.SocketAlreadyExists else |err| if (err != error.FileNotFound) return err;
    var db_writer = try app.Writer.init(init.io, options.database);
    writer = &db_writer;
    try writer.start();
    errdefer writer.stop();
    var settings = std.mem.zeroes(fio.http_settings_s);
    settings.on_request = request;
    settings.max_body_size = app.body_limit;
    settings.max_header_size = 32768;
    settings.timeout = 30;
    if (fio.http_listen(null, options.socket.ptr, settings) == -1) return error.ListenFailed;
    defer std.Io.Dir.deleteFileAbsolute(init.io, options.socket) catch {};
    fio_state_callback_add(on_start, started, null);
    fio_state_callback_add(on_shutdown, shutdown, null);
    zap.start(.{ .threads = @intCast(options.workers), .workers = 1 });
}
fn started(_: ?*anyopaque) callconv(.c) void {
    std.debug.print("Listening on {s} (Zig 0.16, Zap kqueue, {d} HTTP threads + 1 SQLite writer)\n", .{ options.socket, options.workers });
}
fn shutdown(_: ?*anyopaque) callconv(.c) void {
    writer.stop();
}
fn slice(obj: fio.FIOBJ) []const u8 {
    const s = fio.fiobj_obj2cstr(obj);
    return s.data[0..s.len];
}
fn send(h: [*c]fio.http_s, status: usize, body: []const u8) void {
    h.*.status = status;
    _ = fio.http_set_header2(h, .{ .data = @constCast("content-type"), .len = 12, .capa = 0 }, .{ .data = @constCast("application/json"), .len = 16, .capa = 0 });
    _ = fio.http_send_body(h, @ptrCast(@constCast(body.ptr)), body.len);
}
fn request(h: [*c]fio.http_s) callconv(.c) void {
    handle(h) catch |err| {
        std.log.err("HTTP: {s}", .{@errorName(err)});
        send(h, 500, "\"internal error\"");
    };
}
fn handle(h: [*c]fio.http_s) !void {
    const path = slice(h.*.path);
    if (!std.mem.eql(u8, slice(h.*.method), "POST") or (!std.mem.eql(u8, path, "/posts") and !std.mem.eql(u8, path, "/echo"))) return send(h, 404, "\"not found\"");
    var arena = std.heap.ArenaAllocator.init(app.allocator);
    var owned = true;
    defer if (owned) arena.deinit();
    const alloc = arena.allocator();
    const input = app.json.parse(app.NewPost, alloc, slice(h.*.body)) catch return send(h, 400, "\"invalid JSON\"");
    if (std.mem.eql(u8, path, "/echo")) return send(h, 200, try app.json.stringify(alloc, input));
    if (!input.valid()) return send(h, 400, "\"invalid content or email\"");
    // The JSON decoder may borrow strings. Copy them before pausing because the
    // framework invalidates the request handle and its body on http_pause.
    const copy: app.NewPost = .{ .content = try alloc.dupe(u8, input.content), .email = try alloc.dupe(u8, input.email) };
    const job = try app.allocator.create(app.Job);
    job.* = .{ .arena = arena, .input = copy, .complete = completed };
    owned = false;
    h.*.udata = job;
    fio.http_pause(h, paused);
}
fn paused(pause_handle: ?*fio.http_pause_handle_s) callconv(.c) void {
    const job: *app.Job = @ptrCast(@alignCast(fio.http_paused_udata_get(pause_handle).?));
    job.context = pause_handle;
    writer.submit(job) catch |err| {
        job.result = err;
        completed(job);
    };
}
fn completed(job: *app.Job) void {
    fio.http_resume(@ptrCast(job.context), resumed, abandoned);
}
fn destroy(job: *app.Job) void {
    job.arena.deinit();
    app.allocator.destroy(job);
}
fn resumed(h: [*c]fio.http_s) callconv(.c) void {
    const job: *app.Job = @ptrCast(@alignCast(h.*.udata.?));
    h.*.udata = null;
    defer destroy(job);
    const post = job.result catch |err| {
        send(h, if (err == error.WriterUnavailable) 503 else 500, "\"write failed\"");
        return;
    };
    const body = app.json.stringify(job.arena.allocator(), post) catch {
        send(h, 500, "\"encoding failed\"");
        return;
    };
    send(h, 201, body);
}
fn abandoned(context: ?*anyopaque) callconv(.c) void {
    destroy(@ptrCast(@alignCast(context.?)));
}
