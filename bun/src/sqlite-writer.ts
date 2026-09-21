import type { NewPost } from "./new-post.ts"
import type { Post, WriterReply, WriterRequest } from "./post.ts"

export async function openWriter(database: string) {
  const worker = new Worker(new URL("./sqlite-worker.ts", import.meta.url).href, {
    name: "SQLite writer",
    env: { ...Bun.env, BENCHMARK_DB: database },
  })
  const ready = Promise.withResolvers<void>()
  const pending = new Map<number, ReturnType<typeof Promise.withResolvers<Post>>>()
  let closing: ReturnType<typeof Promise.withResolvers<void>> | undefined
  let stopped = false
  let failure: Error | undefined
  let nextRequestId = 0

  function fail(error: Error) {
    failure = error
    ready.reject(error)
    closing?.reject(error)
    for (const request of pending.values()) request.reject(error)
    pending.clear()
    worker.terminate()
  }

  worker.onmessage = function receive(event: MessageEvent<WriterReply>) {
    const reply = event.data
    switch (reply.kind) {
      case "ready":
        ready.resolve()
        break
      case "stopped":
        stopped = true
        closing?.resolve()
        break
      case "post": {
        const { kind, requestId, ...post } = reply
        const request = pending.get(requestId)
        pending.delete(requestId)
        request?.resolve(post)
        break
      }
      case "error": {
        const request = pending.get(reply.requestId)
        pending.delete(reply.requestId)
        request?.reject(new Error(reply.message))
        break
      }
    }
  }
  worker.onerror = function onError(event) {
    fail(new Error(event.message))
  }
  worker.addEventListener("close", function onClose() {
    if (!stopped && !failure) fail(new Error("SQLite writer exited unexpectedly"))
  })

  function send(message: WriterRequest) {
    worker.postMessage(message)
  }

  function create(post: NewPost): Promise<Post> {
    if (failure) return Promise.reject(failure)
    if (closing) return Promise.reject(new Error("SQLite writer is closing"))
    const requestId = ++nextRequestId
    const result = Promise.withResolvers<Post>()
    pending.set(requestId, result)
    send({ kind: "write", requestId, ...post })
    return result.promise
  }

  function close(): Promise<void> {
    if (failure) return Promise.reject(failure)
    if (!closing) {
      closing = Promise.withResolvers<void>()
      send({ kind: "stop" })
    }
    return closing.promise
  }

  await ready.promise
  return { create, close }
}
