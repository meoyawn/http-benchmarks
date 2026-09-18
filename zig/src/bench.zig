const std = @import("std");
const app = @import("app");
pub fn main(init: std.process.Init) !void {
    const alloc = init.arena.allocator();
    const args = try init.minimal.args.toSlice(alloc);
    if (args.len != 4) return error.UsageExpectedModeCorpusDatabase;
    const corpus = try std.Io.Dir.cwd().readFileAlloc(init.io, args[2], alloc, .limited(128 * 1024 * 1024));
    var lines: std.ArrayList([]const u8) = .empty;
    var iter = std.mem.splitScalar(u8, corpus, '\n');
    while (iter.next()) |line| if (line.len > 0) {
        try lines.append(alloc, line);
    };
    var arena = std.heap.ArenaAllocator.init(app.allocator);
    defer arena.deinit();
    var checksum: u64 = 0;
    if (std.mem.eql(u8, args[1], "json")) {
        const count = lines.items.len * 16;
        const start = std.Io.Clock.awake.now(init.io).toNanoseconds();
        for (0..count) |i| {
            _ = arena.reset(.retain_capacity);
            const input = try app.json.parse(app.NewPost, arena.allocator(), lines.items[i % lines.items.len]);
            const out = try app.json.stringify(arena.allocator(), input);
            checksum +%= out.len;
            std.mem.doNotOptimizeAway(out);
        }
        const ns = std.Io.Clock.awake.now(init.io).toNanoseconds() - start;
        std.debug.print("{{\"operations\":{d},\"ns_per_op\":{d},\"checksum\":{d}}}\n", .{ count, @as(f64, @floatFromInt(ns)) / @as(f64, @floatFromInt(count)), checksum });
    } else {
        var inputs: std.ArrayList(app.NewPost) = .empty;
        for (lines.items) |line| try inputs.append(alloc, try std.json.parseFromSliceLeaky(app.NewPost, alloc, line, .{}));
        var db = try app.Database.open(args[3]);
        defer db.deinit();
        const count = inputs.items.len * 2;
        const start = std.Io.Clock.awake.now(init.io).toNanoseconds();
        for (0..count) |i| {
            _ = arena.reset(.retain_capacity);
            const post = try db.transact(arena.allocator(), inputs.items[i % inputs.items.len]);
            checksum +%= @intCast(post.id);
        }
        const ns = std.Io.Clock.awake.now(init.io).toNanoseconds() - start;
        std.debug.print("{{\"operations\":{d},\"ns_per_op\":{d},\"checksum\":{d}}}\n", .{ count, @as(f64, @floatFromInt(ns)) / @as(f64, @floatFromInt(count)), checksum });
    }
}
