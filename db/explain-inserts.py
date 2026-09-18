#!/usr/bin/env python3
"""Inspect the existing inserts using the benchmark's exact SQLite library."""
import argparse
import ctypes as c
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, default=ROOT / "rust/.tools/sqlite/lib/libsqlite3.dylib")
    args = parser.parse_args()
    lib = c.CDLL(str(args.library.resolve()))
    lib.sqlite3_initialize.restype = c.c_int
    lib.sqlite3_open.argtypes = [c.c_char_p, c.POINTER(c.c_void_p)]
    lib.sqlite3_prepare_v2.argtypes = [c.c_void_p, c.c_char_p, c.c_int, c.POINTER(c.c_void_p), c.c_void_p]
    lib.sqlite3_step.argtypes = [c.c_void_p]
    lib.sqlite3_finalize.argtypes = [c.c_void_p]
    lib.sqlite3_column_count.argtypes = [c.c_void_p]
    lib.sqlite3_column_text.argtypes = [c.c_void_p, c.c_int]
    lib.sqlite3_column_text.restype = c.c_char_p
    lib.sqlite3_errmsg.argtypes = [c.c_void_p]
    lib.sqlite3_errmsg.restype = c.c_char_p
    lib.sqlite3_close.argtypes = [c.c_void_p]
    database = c.c_void_p()
    if lib.sqlite3_initialize() or lib.sqlite3_open(b":memory:", c.byref(database)):
        raise RuntimeError("SQLite initialization failed")

    def query(sql):
        statement = c.c_void_p()
        code = lib.sqlite3_prepare_v2(database, sql.encode(), -1, c.byref(statement), None)
        if code:
            raise RuntimeError(lib.sqlite3_errmsg(database).decode())
        try:
            rows = []
            while True:
                code = lib.sqlite3_step(statement)
                if code == 101:
                    return rows
                if code != 100:
                    raise RuntimeError(lib.sqlite3_errmsg(database).decode())
                rows.append([(lib.sqlite3_column_text(statement, i) or b"").decode()
                             for i in range(lib.sqlite3_column_count(statement))])
        finally:
            lib.sqlite3_finalize(statement)

    try:
        for sql in (ROOT / "db/migrations/001_init.up.sql").read_text().split(";"):
            if sql.strip():
                query(sql)
        query("PRAGMA foreign_keys=ON")
        statements = {
            "insert_user": "INSERT OR IGNORE INTO users(email) VALUES ('random@example.test')",
            "insert_post": "INSERT INTO posts(content,user_id) SELECT 'random content',id FROM users WHERE email IS 'random@example.test' RETURNING id,user_id,content,created_at,updated_at",
        }
        plans = {}
        for name, sql in statements.items():
            bytecode = query("EXPLAIN " + sql)
            plans[name] = {"sql": sql, "query_plan": query("EXPLAIN QUERY PLAN " + sql),
                           "opcode_count": len(bytecode), "bytecode": bytecode}
        print(json.dumps({"sqlite_version": query("SELECT sqlite_version()"),
                          "compile_options": query("PRAGMA compile_options"), "statements": plans}, indent=2))
    finally:
        lib.sqlite3_close(database)


if __name__ == "__main__":
    main()
