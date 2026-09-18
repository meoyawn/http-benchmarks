const std = @import("std");

pub fn build(b: *std.Build) void {
    const target = b.standardTargetOptions(.{});
    const optimize = b.standardOptimizeOption(.{});
    const http = b.option(enum { zap, httpz, dusty, std }, "http", "HTTP comparison") orelse .std;
    const json = b.option(enum { std, serde, yyjson }, "json", "JSON comparison") orelse .yyjson;
    const sqlite = b.option(enum { zqlite, ndsqlite }, "sqlite", "SQLite comparison") orelse .zqlite;
    const options = b.addOptions();
    options.addOption(@TypeOf(json), "json", json);
    options.addOption(@TypeOf(sqlite), "sqlite", sqlite);

    const c = b.addTranslateC(.{
        .root_source_file = b.path(".tools/sqlite/include/sqlite3.h"),
        .target = target,
        .optimize = optimize,
        .link_libc = true,
    }).createModule();
    const db_dep = b.dependency(@tagName(sqlite), .{});
    const db_mod = b.createModule(.{
        .root_source_file = db_dep.path(if (sqlite == .zqlite) "src/zqlite.zig" else "src/sqlite.zig"),
        .target = target,
        .optimize = optimize,
        .link_libc = true,
    });
    db_mod.addImport("c", c);
    db_mod.addLibraryPath(b.path(".tools/sqlite/lib"));
    db_mod.addRPath(b.path(".tools/sqlite/lib"));
    db_mod.linkSystemLibrary("sqlite3", .{ .use_pkg_config = .no });

    const core = b.createModule(.{
        .root_source_file = b.path("src/app.zig"),
        .target = target,
        .optimize = optimize,
        .link_libc = true,
    });
    core.addImport("sqlite", db_mod);
    core.addOptions("options", options);
    core.addAnonymousImport("sqlite_config", .{ .root_source_file = b.path("../db/sqlite-config.json") });
    core.addAnonymousImport("schema", .{ .root_source_file = b.path("../db/migrations/001_init.up.sql") });
    core.addAnonymousImport("emails", .{ .root_source_file = b.path("../testdata/email-validation.json") });
    if (json == .serde) core.addImport("serde", b.dependency("serde", .{ .target = target, .optimize = optimize }).module("serde"));
    if (json == .yyjson) {
        const yy = b.dependency("yyjson", .{});
        const yy_c = b.addTranslateC(.{ .root_source_file = yy.path("src/yyjson.h"), .target = target, .optimize = optimize });
        yy_c.defineCMacro("YYJSON_DISABLE_UTILS", "1");
        const mod = yy_c.createModule();
        mod.addCSourceFile(.{ .file = yy.path("src/yyjson.c"), .flags = &.{ "-O3", "-DNDEBUG", "-DYYJSON_DISABLE_UTILS=1" } });
        core.addImport("yyjson", mod);
    }
    const root = b.createModule(.{
        .root_source_file = b.path(switch (http) {
            .zap => "src/zap.zig",
            .httpz => "src/httpz.zig",
            .dusty => "src/dusty.zig",
            .std => "src/main.zig",
        }),
        .target = target,
        .optimize = optimize,
        .link_libc = true,
        .strip = optimize == .ReleaseFast,
    });
    root.addImport("app", core);
    if (http == .dusty or http == .std) {
        const zio = b.dependency("zio", .{ .target = target, .optimize = optimize }).module("zio");
        if (http == .dusty) {
            const dusty = b.dependency("dusty", .{ .target = target, .optimize = optimize, .use_tls = false }).module("dusty");
            dusty.addImport("zio", zio);
            root.addImport("dusty", dusty);
        }
        root.addImport("zio", zio);
    } else root.addImport(@tagName(http), b.dependency(@tagName(http), .{ .target = target, .optimize = optimize }).module(@tagName(http)));
    const exe = b.addExecutable(.{ .name = "zig", .root_module = root, .use_llvm = true });
    b.installArtifact(exe);
    const run = b.addRunArtifact(exe);
    if (b.args) |args| run.addArgs(args);
    b.step("run", "Run the server").dependOn(&run.step);
    const tests = b.addTest(.{ .root_module = core, .use_llvm = true });
    b.step("test", "Run application tests").dependOn(&b.addRunArtifact(tests).step);
    const bench_root = b.createModule(.{ .root_source_file = b.path("src/bench.zig"), .target = target, .optimize = optimize, .link_libc = true });
    bench_root.addImport("app", core);
    const bench = b.addExecutable(.{ .name = "bench-libs", .root_module = bench_root, .use_llvm = true });
    b.step("bench", "Install the library benchmark").dependOn(&b.addInstallArtifact(bench, .{}).step);
}
