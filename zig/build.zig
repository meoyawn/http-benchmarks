const std = @import("std");

pub fn build(b: *std.Build) void {
    const target = b.standardTargetOptions(.{});
    const optimize = b.standardOptimizeOption(.{});
    const httpz = b.dependency("httpz", .{ .target = target, .optimize = optimize });
    // This pinned upstream revision applies TCP_NODELAY to Unix sockets.
    // Patch only the generated build copy; keep the downloaded dependency intact.
    const source = std.fs.cwd().readFileAlloc(b.allocator, httpz.path("src/httpz.zig").getPath(b), 1024 * 1024) catch @panic("read httpz");
    if (std.mem.count(u8, source, "const no_delay = config.isUnixAddress();") != 1) @panic("httpz Unix-socket patch no longer matches");
    const patched = std.mem.replaceOwned(u8, b.allocator, source, "const no_delay = config.isUnixAddress();", "const no_delay = !config.isUnixAddress();") catch @panic("patch httpz");
    const files = b.addWriteFiles();
    _ = files.addCopyDirectory(httpz.path("src"), "httpz", .{ .exclude_extensions = &.{"httpz.zig"} });
    httpz.module("httpz").root_source_file = files.add("httpz/httpz.zig", patched);
    const mvzr = b.dependency("mvzr", .{ .target = target, .optimize = optimize });
    const sqlite = b.dependency("sqlite", .{ .target = target, .optimize = optimize });

    // Use the shared SQLite build, not the binding's bundled older engine.
    const sqlite_module = b.createModule(.{
        .root_source_file = sqlite.path("sqlite.zig"),
        .target = target,
        .optimize = optimize,
        .link_libc = true,
    });
    sqlite_module.addIncludePath(sqlite.path("c"));
    sqlite_module.addIncludePath(b.path(".tools/sqlite/include"));
    sqlite_module.addCSourceFile(.{ .file = sqlite.path("c/workaround.c"), .flags = &.{} });
    sqlite_module.addLibraryPath(b.path(".tools/sqlite/lib"));
    sqlite_module.addRPath(b.path(".tools/sqlite/lib"));
    sqlite_module.linkSystemLibrary("sqlite3", .{ .use_pkg_config = .no });
    const options = b.addOptions();
    options.addOption(bool, "in_memory", true);
    options.addOption(?[]const u8, "dbfile", null);
    sqlite_module.addImport("build_options", options.createModule());

    const root = b.createModule(.{
        .root_source_file = b.path("src/main.zig"),
        .target = target,
        .optimize = optimize,
        .imports = &.{
            .{ .name = "httpz", .module = httpz.module("httpz") },
            .{ .name = "mvzr", .module = mvzr.module("mvzr") },
            .{ .name = "sqlite", .module = sqlite_module },
        },
    });
    root.addAnonymousImport("schema", .{ .root_source_file = b.path("../db/migrations/001_init.up.sql") });
    const exe = b.addExecutable(.{ .name = "zig", .root_module = root });
    b.installArtifact(exe);
    const run = b.addRunArtifact(exe);
    if (b.args) |args| run.addArgs(args);
    b.step("run", "Run the server").dependOn(&run.step);

    const tests = b.addTest(.{ .root_module = root });
    b.step("test", "Run unit tests").dependOn(&b.addRunArtifact(tests).step);
}
