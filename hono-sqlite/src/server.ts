import { Database } from "bun:sqlite"
import { Hono } from "hono"
import { HTTPException } from "hono/http-exception"
import { validator } from "hono/validator"
import * as fs from "node:fs/promises"
import * as v from "valibot"

const db = new Database("../db/db.sqlite", {
  readwrite: true,
  strict: true,
})

db.exec(`
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 10000;

PRAGMA strict = ON;

PRAGMA optimize = 0x10002;
`)

const insertUser = db.prepare<void, [email: string]>(`
INSERT OR IGNORE INTO users (email)
VALUES (?)
`)

interface Post {
  id: number
  user_id: number
  content: string
  created_at: number
  updated_at: number
}

const insertPost = db.prepare<Post, [content: string, email: string]>(`
INSERT INTO posts   (content,   user_id)
SELECT              ?,          id
FROM        users
WHERE       email = ?
RETURNING   id, user_id, content, created_at, updated_at
`)

const NewPost = v.object({
  content: v.pipe(v.string(), v.nonEmpty()),
  email: v.pipe(v.string(), v.email()),
})

type NewPost = v.InferInput<typeof NewPost>

const safeParse = v.safeParser(NewPost)

const hono = new Hono()
  .post(
    "/posts",
    validator("json", (value, c) => {
      const x = safeParse(value)
      if (!x.success) return c.json(x.issues, 400)
      return x.output
    }),
    ctx => {
      const body = ctx.req.valid("json")

      let post: Post | null = null
      db.transaction(({ content, email }: NewPost) => {
        insertUser.run(email)
        post = insertPost.get(content, email)
        if (!post) throw new HTTPException(500)
      }).immediate(body)

      return ctx.json(post, 201)
    },
  )
  .post("/echo", async ctx => {
    const body = await ctx.req.json()
    return ctx.json(body)
  })

const unix = "/tmp/benchmark.sock"

const server = Bun.serve({
  unix,
  fetch: hono.fetch,
  development: false,
})

console.log(`Listening on ${unix}`)

async function shutdown() {
  console.log("Closing DB")

  insertUser.finalize()
  insertPost.finalize()

  db.exec("PRAGMA optimize")
  db.close(true)

  await server.stop()
  await fs.rm(unix)
}

for (const sig of ["SIGINT", "SIGTERM"]) {
  process.once(sig, async () => {
    await shutdown()
    process.exit(0)
  })
}
