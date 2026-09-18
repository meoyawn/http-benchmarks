const std = @import("std");
pub const json = @import("json.zig");
pub const Database = @import("database.zig").Database;
pub const runtime = @import("runtime.zig");
pub const Writer = @import("writer.zig").Writer;
pub const Job = @import("writer.zig").Job;
pub const allocator = std.heap.smp_allocator;
pub const body_limit = 2 * 1024 * 1024;

pub const NewPost = struct {
    content: []const u8,
    email: []const u8,

    pub fn valid(self: NewPost) bool {
        return self.content.len != 0 and validEmail(self.email);
    }
};
pub const Post = struct {
    id: i64,
    user_id: i64,
    content: []const u8,
    created_at: i64,
    updated_at: i64,
};

// Equivalent whole-string ASCII rule to the shared precompiled regex. No locale
// or Unicode character classes; checking bytes also rejects embedded NULs.
fn validEmail(email: []const u8) bool {
    const at = std.mem.indexOfScalar(u8, email, '@') orelse return false;
    if (at == 0) return false;
    for (email[0..at]) |ch| if (!std.ascii.isAlphanumeric(ch) and std.mem.indexOfScalar(u8, "._%+-", ch) == null) return false;
    const domain = email[at + 1 ..];
    const dot = std.mem.lastIndexOfScalar(u8, domain, '.') orelse return false;
    if (dot == 0 or domain.len - dot - 1 < 2) return false;
    for (domain[0..dot]) |ch| if (!std.ascii.isAlphanumeric(ch) and ch != '.' and ch != '-') return false;
    for (domain[dot + 1 ..]) |ch| if (!std.ascii.isAlphabetic(ch)) return false;
    return true;
}

pub const Options = struct {
    database: [:0]const u8 = "../db/db.sqlite",
    socket: [:0]const u8 = "/tmp/benchmark.sock",
    workers: u16 = 1,

    pub fn parse(init: std.process.Init) !Options {
        var options: Options = .{};
        const args = try init.minimal.args.toSlice(init.arena.allocator());
        var i: usize = 1;
        while (i < args.len) : (i += 2) {
            if (std.mem.eql(u8, args[i], "-check-config")) {
                try runtime.snapshot();
                std.process.exit(0);
            }
            if (i + 1 == args.len) return error.MissingArgument;
            if (std.mem.eql(u8, args[i], "-db")) options.database = args[i + 1] else if (std.mem.eql(u8, args[i], "-socket")) options.socket = args[i + 1] else if (std.mem.eql(u8, args[i], "-workers")) options.workers = try std.fmt.parseInt(u16, args[i + 1], 10) else return error.UnknownArgument;
        }
        if (options.workers == 0 or options.workers > 64) return error.InvalidWorkers;
        return options;
    }
};

test "shared email rule and nonempty content" {
    const fixtures = try std.json.parseFromSlice([]struct { email: []const u8, valid: bool }, std.testing.allocator, @embedFile("emails"), .{});
    defer fixtures.deinit();
    for (fixtures.value) |f| try std.testing.expectEqual(f.valid, (NewPost{ .content = "x", .email = f.email }).valid());
    try std.testing.expect(!(NewPost{ .content = "", .email = "a@b.com" }).valid());
}
test {
    _ = @import("database.zig");
    _ = @import("json.zig");
}
