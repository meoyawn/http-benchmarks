import re
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


@dataclass()
class NewPost:
    email: str
    content: str

    def validate(self) -> list[str]:
        errs = []

        if len(self.content) == 0:
            errs.append("content: should not be empty")

        if not re.match(r"^[^@]+@[^@]+\.[^@]{2,}$", self.email):
            errs.append("email: should be a valid email address")

        return errs


@dataclass
class Post:
    id: int
    user_id: int
    content: str
    created_at: int
    updated_at: int


@router.post("/posts")
def http_post(req: FromJSON[NewPost]) -> Response:
    body = req.value

    errs = body.validate()
    if len(errs) > 0:
        return json({"errors": errs}, status=400)

    post: Post | None = None

    conn.execute("BEGIN IMMEDIATE TRANSACTION")
    try:
        conn.execute("INSERT OR IGNORE INTO users (email) VALUES (?)", (body.email,))
        res = conn.execute(
            """
            INSERT INTO posts   (content,   user_id)
            SELECT              ?,          id
            FROM        users
            WHERE       email = ?
            RETURNING   id, user_id, content, created_at, updated_at
            """,
            (
                body.content,
                body.email,
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
