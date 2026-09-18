const std = @import("std");
const app = @import("app.zig");
pub const Job = struct {
    input: app.NewPost,
    arena: std.heap.ArenaAllocator,
    context: ?*anyopaque = null,
    complete: *const fn (*Job) void,
    result: anyerror!app.Post = error.NotReady,
};
pub const Writer = struct {
    io: std.Io,
    db: app.Database,
    mutex: std.Io.Mutex = .init,
    ready: std.Io.Condition = .init,
    queue: [1024]*Job = undefined,
    head: usize = 0,
    len: usize = 0,
    stopping: bool = false,
    thread: ?std.Thread = null,
    pub fn init(io: std.Io, path: [:0]const u8) !Writer {
        return .{ .io = io, .db = try app.Database.open(path) };
    }
    // Start only after Writer is in its final stable address.
    pub fn start(self: *Writer) !void {
        self.thread = try std.Thread.spawn(.{}, run, .{self});
    }
    pub fn submit(self: *Writer, job: *Job) !void {
        self.mutex.lockUncancelable(self.io);
        defer self.mutex.unlock(self.io);
        if (self.stopping or self.len == self.queue.len) return error.WriterUnavailable;
        self.queue[(self.head + self.len) % self.queue.len] = job;
        self.len += 1;
        self.ready.signal(self.io);
    }
    pub fn stop(self: *Writer) void {
        self.mutex.lockUncancelable(self.io);
        self.stopping = true;
        self.ready.signal(self.io);
        self.mutex.unlock(self.io);
        if (self.thread) |thread| thread.join();
        self.db.deinit();
    }
    fn run(self: *Writer) void {
        while (true) {
            self.mutex.lockUncancelable(self.io);
            while (self.len == 0 and !self.stopping) self.ready.waitUncancelable(self.io, &self.mutex);
            if (self.len == 0) {
                self.mutex.unlock(self.io);
                return;
            }
            const job = self.queue[self.head];
            self.head = (self.head + 1) % self.queue.len;
            self.len -= 1;
            self.mutex.unlock(self.io);
            job.result = self.db.transact(job.arena.allocator(), job.input);
            // One ordinary completion per commit, including disconnected clients.
            job.complete(job);
        }
    }
};
