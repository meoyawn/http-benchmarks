use actix_web::{error, post, web, App, HttpResponse, HttpServer, Result};
use rusqlite;
use rusqlite::{CachedStatement, Connection, TransactionBehavior};
use serde::{Deserialize, Serialize};
use std::io;
use validator::Validate;

#[derive(Debug, Validate, Deserialize)]
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

#[post("/posts")]
async fn handle_post(body: web::Json<NewPost>) -> Result<HttpResponse, actix_web::Error> {
    body.validate()
        .or_else(|e| Err(error::ErrorBadRequest(e)))?;

    let post = Post {
        id: 1,
        user_id: 1,
        content: body.content.clone(),
        created_at: 0,
        updated_at: 0,
    };

    Ok(HttpResponse::Ok().json(post))
}

fn setup_db() -> rusqlite::Result<()> {
    let mut conn = Connection::open("../db/db.sqlite")?;

    conn.execute_batch(
        "
        PRAGMA journal_mode = wal;
        PRAGMA synchronous = normal;
        PRAGMA foreign_keys = on;
        PRAGMA busy_timeout = 10000;
        
        PRAGMA optimize = 0x10002;
        ",
    )?;

    let mut insert_user = conn.prepare_cached("INSERT OR IGNORE INTO users (email) VALUES (?1)")?;

    let mut insert_post = conn.prepare_cached(
        "
        INSERT INTO posts   (content,   user_id)
        SELECT              ?1,          id
        FROM        users
        WHERE       email = ?2
        RETURNING   id, user_id, content, created_at, updated_at
        ",
    )?;

    _ = transact(
        &mut conn,
        &mut insert_user,
        &mut insert_post,
        &NewPost {
            content: String::from("content"),
            email: String::from("email@gmail.com"),
        },
    )?;

    conn.execute("PRAGMA optimize", [])?;

    Ok(())
}

fn transact<'conn>(
    conn: &'conn mut Connection,
    insert_user: &'conn mut CachedStatement<'conn>,
    insert_post: &'conn mut CachedStatement<'conn>,
    np: &NewPost,
) -> rusqlite::Result<Post> {
    let tx = conn.transaction_with_behavior(TransactionBehavior::Immediate)?; // rolls back on error

    insert_user.execute((&np.email,))?;

    let post = insert_post.query_row((&np.content, &np.email), |row| {
        Ok(Post {
            id: row.get(0)?,
            user_id: row.get(1)?,
            content: row.get(2)?,
            created_at: row.get(3)?,
            updated_at: row.get(4)?,
        })
    })?;

    tx.commit()?;

    Ok(post)
}

#[actix_web::main]
async fn main() -> io::Result<()> {
    let socket = "/tmp/benchmark.sock";

    println!("Listening on {}", socket);

    HttpServer::new(|| App::new().service(handle_post))
        .bind_uds(socket)?
        .run()
        .await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_setup_db() -> rusqlite::Result<()> {
        setup_db()
    }
}
