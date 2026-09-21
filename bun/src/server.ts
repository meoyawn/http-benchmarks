import { Database } from "bun:sqlite"
import { existsSync } from "node:fs"
import { rm } from "node:fs/promises"
import { parseArgs } from "node:util"
import { parseNewPost } from "./new-post.ts"
import type { NewPost } from "./new-post.ts"

const { values } = parseArgs({
  args: Bun.argv.slice(2).map(arg => arg === "-db" ? "--db" : arg === "-socket" ? "--socket" : arg),
  options: {
    db: { type: "string", default: "../db/db.sqlite" },
    socket: { type: "string", default: "/tmp/benchmark.sock" },
  },
})

const unix = values.socket
if (existsSync(unix)) throw new Error(`Socket already exists: ${unix}`)

const db = new Database(values.db, { readwrite: true, strict: true })
db.exec(`
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 10000;
PRAGMA cache_size = -2000;
PRAGMA wal_autocheckpoint = 1000;
PRAGMA temp_store = MEMORY;
PRAGMA mmap_size = 0;
PRAGMA optimize = 0x10002;
`)

const insertUser = db.query<void, [email: string]>(`
INSERT OR IGNORE INTO users (email) VALUES (?)
`)

interface Post {
  id: number
  user_id: number
  content: string
  created_at: number
  updated_at: number
}

const insertPost = db.query<Post, [content: string, email: string]>(`
INSERT INTO posts (content, user_id)
SELECT ?, id FROM users WHERE email IS ?
RETURNING id, user_id, content, created_at, updated_at
`)

const createPost = db.transaction(function createPost({ content, email }: NewPost) {
  insertUser.run(email)
  const post = insertPost.get(content, email)
  if (!post) throw new Error("Post insert returned no row")
  return post
})

const server = Bun.serve({
  unix,
  development: false,
  maxRequestBodySize: 2 * 1024 * 1024,
  routes: {
    "/posts": {
      async POST(request) {
        let body: unknown
        try {
          body = await request.json()
        } catch {
          return Response.json({ error: "Invalid JSON" }, { status: 400 })
        }
        const result = parseNewPost(body)
        if (!result.success) return Response.json(result.issues, { status: 400 })
        return Response.json(createPost.immediate(result.output), { status: 201 })
      },
    },
    "/echo": {
      async POST(request) {
        let body: unknown
        try {
          body = await request.json()
        } catch {
          return Response.json({ error: "Invalid JSON" }, { status: 400 })
        }
        if (
          !body || typeof body !== "object" ||
          !("content" in body) || typeof body.content !== "string" ||
          !("email" in body) || typeof body.email !== "string"
        ) {
          return Response.json({ error: "Invalid echo" }, { status: 400 })
        }
        return Response.json({ content: body.content, email: body.email })
      },
    },
  },
  fetch: () => new Response("Not Found", { status: 404 }),
  error(error) {
    console.error(error)
    return Response.json({ error: "Internal Server Error" }, { status: 500 })
  },
})

console.log(`Listening on ${unix}`)

let stopping = false
async function shutdown() {
  if (stopping) return
  stopping = true
  await server.stop()
  insertUser.finalize()
  insertPost.finalize()
  db.exec("PRAGMA optimize")
  db.close(true)
  await rm(unix, { force: true })
}

for (const signal of ["SIGINT", "SIGTERM"] as const) {
  process.once(signal, shutdown)
}
