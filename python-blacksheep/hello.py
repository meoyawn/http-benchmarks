import sqlite3
from dataclasses import dataclass

from blacksheep import Application, FromJSON, Response, Router, json

conn = sqlite3.connect(
    database="../db/db.sqlite",
    isolation_level=None,
    cached_statements=128,
)

conn.executescript("""
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 10000;

PRAGMA strict = ON;

PRAGMA optimize = 0x10002;
""")


router = Router()


@dataclass
class NewPost:
    email: str
    content: str


@dataclass
class Post:
    id: int
    user_id: int
    content: str
    created_at: int
    updated_at: int


@router.post("/posts")
def http_post(req: FromJSON[NewPost]) -> Response:
    post: Post | None = None

    conn.execute("BEGIN IMMEDIATE TRANSACTION")
    try:
        conn.execute(
            "INSERT OR IGNORE INTO users (email) VALUES (?)", (req.value.email,)
        )
        res = conn.execute(
            """
            INSERT INTO posts   (content,   user_id)
            SELECT              ?,          id
            FROM        users
            WHERE       email = ?
            RETURNING   id, user_id, content, created_at, updated_at
            """,
            (
                req.value.content,
                req.value.email,
            ),
        )
        post = Post(*res.fetchone())
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")

    return json(post, status=201)


@router.post("/echo")
def http_echo(req: FromJSON[NewPost]) -> Response:
    return json(req.value)


app = Application(router=router)
