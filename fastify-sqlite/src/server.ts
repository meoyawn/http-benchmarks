import { Database } from "bun:sqlite"
import Fastify, { type FastifyReply, type FastifyRequest } from "fastify"
import openapiGlue, {
  type FastifyOpenapiGlueOptions,
} from "fastify-openapi-glue"
import openApiDoc from "../openapi.json" with { type: "json" }

const db = new Database("sqlite/db.sqlite", { create: true })

db.exec(`
PRAGMA journal_mode = wal;
PRAGMA synchronous = normal;
PRAGMA foreign_keys = on;
PRAGMA busy_timeout = 10000;

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

const fastify = Fastify({
  logger: false,
})

interface Note {
  id: number
  title: string
  content: string
  created_at: number
  updated_at: number
}

interface NewNote {
  title: string
  content?: string
}

class DisposableDB {
  private putNote = db.prepare<Note, [title: string, content: string]>(
    `
    INSERT INTO notes   (title, content)
    VALUES              (?,     ?)
    RETURNING *
    `,
  )

  newNote({ title, content }: NewNote): Note {
    const n = this.putNote.get(title, content ?? "")
    if (!n) throw new Error("failed to create note")
    return n
  }

  private readonly allNotes = db.prepare<Note, number>(
    `
    SELECT * FROM notes
    WHERE updated_at < ?
    ORDER BY updated_at DESC
    LIMIT 100
    `,
  )

  listNotes = (before: number | undefined): readonly Note[] =>
    this.allNotes.all(before ?? Number.MAX_SAFE_INTEGER)

  private oneNote = db.prepare<Note, number>(
    `
    SELECT *
    FROM notes
    WHERE id = ?
    `,
  )

  findNote = (id: number): Note | null => this.oneNote.get(id)

  finalize() {
    this.allNotes[Symbol.dispose]()
    this.putNote[Symbol.dispose]()
    this.oneNote[Symbol.dispose]()
  }
}

// noinspection JSUnusedGlobalSymbols
class ServiceHandlers {
  constructor(private readonly dd: DisposableDB) {}

  newNote = ({
    body,
  }: FastifyRequest<{
    Body: { title: string; content?: string }
  }>): Note => this.dd.newNote(body)

  listNotes = ({
    query,
  }: FastifyRequest<{
    Querystring: { after?: number; before?: number }
  }>): readonly Note[] => this.dd.listNotes(query.before)

  getNote = (
    { params }: FastifyRequest<{ Params: { id: number } }>,
    rep: FastifyReply,
  ): Note | FastifyReply => {
    const n = this.dd.findNote(params.id)
    return n ? n : rep.code(404).send()
  }
}

const preparedStatements = new DisposableDB()

fastify.register(openapiGlue, {
  specification: openApiDoc,
  serviceHandlers: new ServiceHandlers(preparedStatements),
} satisfies FastifyOpenapiGlueOptions)

process.once("SIGINT", () => {
  preparedStatements.finalize()
  db.exec("PRAGMA optimize;")
  db.close()
  process.exit(0)
})

try {
  await fastify.listen({ host: "127.0.0.1", port: 3000 })
  console.log("Server listening at http://127.0.0.1:3000")
} catch (err) {
  console.error(err)
  process.exit(1)
}
