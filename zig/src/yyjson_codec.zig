//! Comptime struct mapping around yyjson; no payload-specific parsing or escaping.
const std = @import("std");
const c = @import("yyjson");

pub fn parse(comptime T: type, alloc: std.mem.Allocator, input: []const u8) !T {
    const doc = c.yyjson_read(input.ptr, input.len, 0) orelse return error.InvalidJson;
    defer c.yyjson_doc_free(doc);
    const obj = c.yyjson_doc_get_root(doc);
    if (!c.yyjson_is_obj(obj)) return error.ExpectedObject;
    var value: T = undefined;
    inline for (@typeInfo(T).@"struct".fields) |field| {
        const item = c.yyjson_obj_getn(obj, field.name.ptr, field.name.len);
        if (field.type == []const u8) {
            if (!c.yyjson_is_str(item)) return error.ExpectedString;
            @field(value, field.name) = try alloc.dupe(u8, c.yyjson_get_str(item)[0..c.yyjson_get_len(item)]);
        } else @compileError("unsupported JSON field");
    }
    return value;
}
pub fn stringify(alloc: std.mem.Allocator, value: anytype) ![]const u8 {
    const doc = c.yyjson_mut_doc_new(null) orelse return error.OutOfMemory;
    defer c.yyjson_mut_doc_free(doc);
    const obj = c.yyjson_mut_obj(doc) orelse return error.OutOfMemory;
    c.yyjson_mut_doc_set_root(doc, obj);
    inline for (@typeInfo(@TypeOf(value)).@"struct".fields) |field| {
        const v = @field(value, field.name);
        const ok = if (field.type == []const u8)
            c.yyjson_mut_obj_add_strn(doc, obj, field.name.ptr, v.ptr, v.len)
        else if (@typeInfo(field.type) == .int)
            c.yyjson_mut_obj_add_sint(doc, obj, field.name.ptr, v)
        else
            @compileError("unsupported JSON field");
        if (!ok) return error.OutOfMemory;
    }
    var len: usize = 0;
    const encoded = c.yyjson_mut_write(doc, 0, &len) orelse return error.OutOfMemory;
    defer std.c.free(encoded);
    return alloc.dupe(u8, encoded[0..len]);
}
