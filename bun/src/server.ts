import { existsSync } from "node:fs"
import { rm } from "node:fs/promises"
import { parseArgs } from "node:util"
import { parseNewPost } from "./new-post.ts"
import { openWriter } from "./sqlite-writer.ts"

const { values } = parseArgs({
  args: Bun.argv.slice(2).map(arg => arg === "-db" ? "--db" : arg === "-socket" ? "--socket" : arg),
  options: {
    db: { type: "string", default: "../db/db.sqlite" },
    socket: { type: "string", default: "/tmp/benchmark.sock" },
  },
})

const unix = values.socket
if (existsSync(unix)) throw new Error(`Socket already exists: ${unix}`)

const writer = await openWriter(values.db)

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
        return Response.json(await writer.create(result.output), { status: 201 })
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
  await writer.close()
  await rm(unix, { force: true })
}

for (const signal of ["SIGINT", "SIGTERM"] as const) {
  process.once(signal, shutdown)
}
