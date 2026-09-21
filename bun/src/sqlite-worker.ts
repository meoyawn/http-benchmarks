import { Database } from "bun:sqlite"
import type { NewPost } from "./new-post.ts"
import type { Post, WriterReply, WriterRequest } from "./post.ts"

const db = new Database(Bun.env.BENCHMARK_DB!, { readwrite: true, strict: true })
db.exec(`
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 10000;
PRAGMA cache_size = -2000;
PRAGMA wal_autocheckpoint = 1000;
PRAGMA journal_size_limit = -1;
PRAGMA temp_store = MEMORY;
PRAGMA mmap_size = 0;
PRAGMA optimize = 0x10002;
`)

const insertUser = db.query<void, [email: string]>(`
INSERT OR IGNORE INTO users (email) VALUES (?)
`)

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

const reply = (message: WriterReply) => postMessage(message)

function receive(event: MessageEvent<WriterRequest>) {
  const request = event.data
  if (request.kind === "stop") {
    insertUser.finalize()
    insertPost.finalize()
    db.exec("PRAGMA optimize")
    db.close(true)
    removeEventListener("message", receive)
    reply({ kind: "stopped" })
    return
  }
  try {
    const post = createPost.immediate(request)
    reply({ kind: "post", requestId: request.requestId, ...post })
  } catch (error) {
    reply({ kind: "error", requestId: request.requestId, message: String(error) })
  }
}

addEventListener("message", receive)
reply({ kind: "ready" })
