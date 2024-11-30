import type { Serve } from "bun"
import { Database } from "bun:sqlite"
import { Hono } from "hono"

const db = new Database("sqlite/db.sqlite", { create: true })
db.exec(`
CREATE TABLE IF NOT EXISTS notes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT    NOT NULL    CHECK(length(title) > 0),
    content     TEXT    NOT NULL,
    created_at  INTEGER NOT NULL    DEFAULT(unixepoch('subsec') * 1000),
    updated_at  INTEGER NOT NULL    DEFAULT(unixepoch('subsec') * 1000)
);

CREATE INDEX IF NOT EXISTS idx_notes_updated_at ON notes(updated_at DESC);

PRAGMA optimize;
`)

db.exec(`
PRAGMA journal_mode = wal;
PRAGMA synchronous = normal;
PRAGMA foreign_keys = on;
PRAGMA busy_timeout = 10000;
`)

interface PostNote {
  title: string
  content?: string
}

interface Note {
  id: number
  title: string
  content: string
  created_at: number
  updated_at: number
}

class PreparedQueries {
  constructor(
    db: Database,
    private readonly putNote = db.prepare<
      Note,
      [title: string, content: string]
    >(`
    INSERT INTO notes   (title, content)
    VALUES              (?,     ?)
    RETURNING *
    `),
    private readonly allNotes = db.prepare<
      Note,
      [maxUpdatedAt: number, limit: number]
    >(`
    SELECT * FROM notes
    WHERE updated_at < ?
    ORDER BY updated_at DESC
    LIMIT ?
    `),
    private oneNote = db.prepare<Note, number>(`
    SELECT *
    FROM notes
    WHERE id = ?
    `),
  ) {}

  newNote({ title, content }: PostNote): Note {
    const n = this.putNote.get(title, content ?? "")
    if (!n) throw new Error("failed to create note")
    return n
  }

  listNotes = (
    before: number | undefined,
    limit: number | undefined,
  ): readonly Note[] =>
    this.allNotes.all(before ?? Number.MAX_SAFE_INTEGER, limit ?? 100)

  findNote = (id: number): Note | null => this.oneNote.get(id)

  close(): void {
    this.allNotes.finalize()
    this.putNote.finalize()
    this.oneNote.finalize()
  }
}

const preparedQueries = new PreparedQueries(db)

const hono = new Hono()
  .post("/notes", async ctx =>
    Response.json(preparedQueries.newNote(await ctx.req.json()), {
      status: 201,
    }),
  )
  .get("/notes", ctx => {
    const { before, limit } = ctx.req.query()
    return Response.json(
      preparedQueries.listNotes(
        before ? Number(before) : undefined,
        limit ? Number(limit) : undefined,
      ),
    )
  })
  .get("/notes/:id", ctx => {
    const idStr = ctx.req.param("id")
    const note = preparedQueries.findNote(Number(idStr))
    return note ? Response.json(note) : new Response(null, { status: 404 })
  })

function shutdown(): void {
  console.log("Closing DB")

  preparedQueries.close()
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
  port: 3000,
  fetch: hono.fetch,
  development: false,
} satisfies Serve
