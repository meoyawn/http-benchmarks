const std = @import("std");
const selected = @import("options").json;

pub fn parse(comptime T: type, alloc: std.mem.Allocator, input: []const u8) !T {
    return switch (selected) {
        .std => std.json.parseFromSliceLeaky(T, alloc, input, .{ .ignore_unknown_fields = true }),
        .serde => @import("serde").json.fromSlice(T, alloc, input),
        .yyjson => @import("yyjson_codec.zig").parse(T, alloc, input),
    };
}
pub fn stringify(alloc: std.mem.Allocator, value: anytype) ![]const u8 {
    return switch (selected) {
        .std => std.json.Stringify.valueAlloc(alloc, value, .{}),
        .serde => @import("serde").json.toSlice(alloc, value),
        .yyjson => @import("yyjson_codec.zig").stringify(alloc, value),
    };
}

test "JSON round trip and strict input" {
    const T = @import("app.zig").NewPost;
    var arena = std.heap.ArenaAllocator.init(std.testing.allocator);
    defer arena.deinit();
    const alloc = arena.allocator();
    const value: T = .{ .content = "雪\x00\n\"\\", .email = "a@b.com" };
    const encoded = try stringify(alloc, value);
    const decoded = try parse(T, alloc, encoded);
    try std.testing.expectEqualStrings(value.content, decoded.content);
    try std.testing.expectEqualStrings(value.email, decoded.email);
    for ([_][]const u8{ "", "{", "null", "[]", "{\"content\":1,\"email\":\"a@b.com\"}", "{\"content\":\"x\"}", "{\"content\":\"\xff\",\"email\":\"a@b.com\"}", "{\"content\":\"x\",\"email\":\"a@b.com\"} trailing" }) |bad| {
        if (parse(T, alloc, bad)) |_| {
            std.debug.print("accepted invalid JSON: {x}\n", .{bad});
            return error.AcceptedInvalidJson;
        } else |_| {}
    }
}
