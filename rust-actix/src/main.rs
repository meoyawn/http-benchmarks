use actix::prelude::*;
use actix_web::{error, post, web, App, HttpResponse, HttpServer};
use rusqlite::Connection;
use serde::{Deserialize, Serialize};
use std::io;
use validator::Validate;

#[derive(Debug, Serialize)]
struct Post {
    id: i64,
    user_id: i64,
    content: String,
    created_at: i64,
    updated_at: i64,
}

#[derive(Debug, Validate, Deserialize)]
struct NewPost {
    #[validate(length(min = 1))]
    content: String,
    #[validate(email)]
    email: String,
}

impl Message for &NewPost {
    type Result = rusqlite::Result<Post>;
}

struct DbActor {
    conn: Connection,
}

impl Actor for DbActor {
    type Context = Context<Self>;

    fn started(&mut self, _: &mut Context<Self>) {
        self.conn
            .execute_batch(
                "
                PRAGMA journal_mode = wal;
                PRAGMA synchronous = normal;
                PRAGMA foreign_keys = on;
                PRAGMA busy_timeout = 10000;
                
                PRAGMA optimize = 0x10002;
                ",
            )
            .unwrap();
    }

    fn stopped(&mut self, _: &mut Self::Context) {
        self.conn.execute("PRAGMA optimize", []).unwrap();
        // self.conn.close().unwrap()
    }
}

impl Handler<&NewPost> for DbActor {
    type Result = rusqlite::Result<Post>;

    fn handle(&mut self, msg: &NewPost, _: &mut Self::Context) -> Self::Result {
        transact(&self.conn, &msg)
    }
}

#[post("/posts")]
async fn handle_post(body: web::Json<NewPost>) -> actix_web::Result<HttpResponse> {
    body.validate().map_err(|e| error::ErrorBadRequest(e))?;

    let post = Post {
        id: 1,
        user_id: 1,
        content: body.content.clone(),
        created_at: 0,
        updated_at: 0,
    };

    Ok(HttpResponse::Ok().json(post))
}

const INSERT_USER: &str = "INSERT OR IGNORE INTO users (email) VALUES (?1)";

const INSERT_POST: &str = "
INSERT INTO posts   (content,   user_id)
SELECT              ?1,          id
FROM        users
WHERE       email = ?2
RETURNING   id, user_id, content, created_at, updated_at
";

const OPEN_PRAGMAS: &str = "
PRAGMA journal_mode = wal;
PRAGMA synchronous = normal;
PRAGMA foreign_keys = on;
PRAGMA busy_timeout = 10000;

PRAGMA optimize = 0x10002;
";

fn new_conn() -> rusqlite::Result<Connection> {
    let conn = Connection::open("../db/db.sqlite")?;
    conn.execute_batch(OPEN_PRAGMAS)?;
    Ok(conn)
}

fn setup_db() -> rusqlite::Result<()> {
    let conn = new_conn()?;

    let post = transact(
        &conn,
        &NewPost {
            content: String::from("content"),
            email: String::from("email@gmail.com"),
        },
    )?;
    println!("{:?}", post);

    conn.execute("PRAGMA optimize", [])?;

    Ok(())
}

#[inline]
fn immediate_tx<T, F>(conn: &Connection, exec: F) -> rusqlite::Result<T>
where
    F: FnOnce(&Connection) -> rusqlite::Result<T>,
{
    conn.prepare_cached("BEGIN IMMEDIATE TRANSACTION")?
        .execute([])?;

    let ret = exec(conn);

    if ret.is_ok() {
        conn.prepare_cached("COMMIT")?.execute([])?;
    } else {
        conn.prepare_cached("ROLLBACK")?.execute([])?;
    }

    ret
}

fn transact(conn: &Connection, np: &NewPost) -> rusqlite::Result<Post> {
    immediate_tx(conn, |c| {
        c.prepare_cached(INSERT_USER)?.execute([&np.email])?;

        c.prepare_cached(INSERT_POST)?
            .query_row([&np.content, &np.email], |row| {
                Ok(Post {
                    id: row.get(0)?,
                    user_id: row.get(1)?,
                    content: row.get(2)?,
                    created_at: row.get(3)?,
                    updated_at: row.get(4)?,
                })
            })
    })
}

fn to_io(e: rusqlite::Error) -> io::Error {
    io::Error::new(io::ErrorKind::Other, e)
}

#[actix_web::main]
async fn main() -> io::Result<()> {
    let socket = "/tmp/benchmark.sock";

    let conn = new_conn().map_err(to_io)?;

    let addr = DbActor { conn }.start();

    println!("Listening on {}", socket);

    let https = HttpServer::new(move || App::new().app_data(addr.clone()).service(handle_post));
    let srv = https.bind_uds(socket)?.run();
    srv.await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_setup_db() -> rusqlite::Result<()> {
        setup_db()
    }
}
