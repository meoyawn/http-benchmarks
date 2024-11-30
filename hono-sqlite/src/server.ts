import type { Serve } from "bun"
import { Database } from "bun:sqlite"
import { Hono } from "hono"
import { validator } from "hono/validator"
import * as v from "valibot"

const db = new Database("../db/db.sqlite")

db.exec(`
PRAGMA journal_mode = wal;
PRAGMA synchronous = normal;
PRAGMA foreign_keys = on;
PRAGMA busy_timeout = 10000;
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

const hono = new Hono().post(
  "/posts",
  validator("json", (value, c) => {
    const x = safeParse(value)
    if (!x.success) return c.json(x.issues, 400)
    return x.output
  }),
  ctx => {
    const body = ctx.req.valid("json")

    let res: Post | null = null

    db.transaction(({ content, email }: NewPost) => {
      insertUser.run(email)
      res = insertPost.get(content, email)
    }).immediate(body)

    return res ? ctx.json(res, 201) : ctx.text("Could not insert", 500)
  },
)

function shutdown(): void {
  console.log("Closing DB")

  insertUser.finalize()
  insertPost.finalize()

  db.exec("PRAGMA optimize")
  db.close(true)
}

for (const sig of ["SIGINT", "SIGTERM"]) {
  process.once(sig, () => {
    shutdown()
    process.exit(0)
  })
}

// noinspection JSUnusedGlobalSymbols
export default {
  unix: "/tmp/benchmark.sock",
  fetch: hono.fetch,
  development: false,
} satisfies Serve
