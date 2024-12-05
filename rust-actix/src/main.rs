use actix::{Actor, Addr, Context, Handler, Message};
use actix_web::error::{ErrorBadRequest, ErrorInternalServerError};
use actix_web::{post, web, App, HttpResponse, HttpServer};
use rusqlite::Connection;
use serde::{Deserialize, Serialize};
use std::io;
use validator::Validate;

#[derive(Debug, Validate, Deserialize, Serialize)]
struct NewPost {
    #[validate(length(min = 1))]
    content: String,
    #[validate(email)]
    email: String,
}

#[derive(Debug, Serialize)]
struct Post {
    id: i64,
    user_id: i64,
    content: String,
    created_at: i64,
    updated_at: i64,
}

struct DbActor {
    conn: Connection,
}

impl Actor for DbActor {
    type Context = Context<Self>;

    fn started(&mut self, _: &mut Self::Context) {
        println!("Started DbActor");
    }

    fn stopped(&mut self, _: &mut Self::Context) {
        self.conn.execute("PRAGMA optimize", []).unwrap();
        // self.conn.close().unwrap();
        println!("Stopped DbActor");
    }
}

impl Message for NewPost {
    type Result = rusqlite::Result<Post>;
}

impl Handler<NewPost> for DbActor {
    type Result = rusqlite::Result<Post>;

    fn handle(&mut self, msg: NewPost, _: &mut Self::Context) -> Self::Result {
        transact(&self.conn, &msg)
    }
}

#[post("/echo")]
async fn http_echo(body: web::Json<NewPost>) -> actix_web::Result<HttpResponse> {
    Ok(HttpResponse::Ok().json(body))
}

#[post("/posts")]
async fn http_post(
    db_actor: web::Data<Addr<DbActor>>,
    body: web::Json<NewPost>,
) -> actix_web::Result<HttpResponse> {
    body.validate().map_err(ErrorBadRequest)?;

    let post = db_actor
        .send(body.0)
        .await
        .map_err(ErrorInternalServerError)? // actor
        .map_err(ErrorInternalServerError)?; // sqlite

    Ok(HttpResponse::Created().json(post))
}

fn new_conn() -> rusqlite::Result<Connection> {
    let conn = Connection::open("../db/db.sqlite")?;

    conn.execute_batch(
        "
        PRAGMA journal_mode = wal;
        PRAGMA synchronous = normal;
        PRAGMA foreign_keys = on;
        PRAGMA busy_timeout = 10000;
        
        PRAGMA optimize = 0x10002;
        ",
    )?;

    Ok(conn)
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
        c.prepare_cached("INSERT OR IGNORE INTO users (email) VALUES (?1)")?
            .execute([&np.email])?;

        c.prepare_cached(
            "
            INSERT INTO posts   (content,   user_id)
            SELECT              ?1,          id
            FROM        users
            WHERE       email = ?2
            RETURNING   id, user_id, content, created_at, updated_at
            ",
        )?
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

    let data = web::Data::new(addr);

    println!("Listening on {}", socket);

    HttpServer::new(move || App::new().app_data(data.clone()).service(http_post).service(http_echo))
        .bind_uds(socket)?
        .run()
        .await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_db() -> rusqlite::Result<()> {
        let conn = new_conn()?;

        let busy_t: i32 = conn.pragma_query_value(None, "busy_timeout", |r| Ok(r.get(0)?))?;
        assert_eq!(busy_t, 10000);

        let post = transact(
            &conn,
            &NewPost {
                content: String::from("content"),
                email: String::from("email@gmail.com"),
            },
        )?;

        assert!(post.id > 0);
        assert_eq!(post.content, "content");

        conn.execute("PRAGMA optimize", [])?;

        Ok(())
    }
}
