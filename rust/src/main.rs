#![forbid(unsafe_code)]

use actix_web::{
    App, HttpResponse, HttpServer,
    error::{ErrorBadRequest, ErrorInternalServerError},
    web::{self, Data, Json, JsonConfig},
};
use rust_benchmark::{BODY_LIMIT, Client, NewPost, Options};
use std::{
    io,
    os::unix::net::UnixListener,
    sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    },
};

async fn echo(body: Json<NewPost>) -> HttpResponse {
    HttpResponse::Ok().json(body.into_inner())
}

async fn posts(db: Data<Client>, body: Json<NewPost>) -> actix_web::Result<HttpResponse> {
    let input = body.into_inner();
    if !input.valid() {
        return Err(ErrorBadRequest("invalid content or email"));
    }
    let post = db.write(input).await.map_err(ErrorInternalServerError)?;
    Ok(HttpResponse::Created().json(post))
}

#[actix_web::main]
async fn main() -> io::Result<()> {
    let options = Options::parse()?;
    let (client, writer) = options.start()?;
    let socket = options.socket.clone();
    let workers = options.workers;
    // Bind explicitly: never unlink or replace another process's socket.
    let listener = UnixListener::bind(&socket)?;
    let started = Arc::new(AtomicUsize::new(0));
    let server = HttpServer::new(move || {
        let app = App::new()
            .app_data(Data::new(client.clone()))
            .app_data(JsonConfig::default().limit(BODY_LIMIT))
            .route("/posts", web::post().to(posts))
            .route("/echo", web::post().to(echo));
        // Startup includes binding, SQLite initialization and HTTP worker setup.
        if started.fetch_add(1, Ordering::Relaxed) + 1 == workers {
            options.listening();
        }
        app
    })
    .workers(workers)
    .listen_uds(listener)?
    .run();
    let result = server.await;
    // Finish every accepted write, including requests whose clients disconnected.
    let stopped = writer.stop();
    let _ = std::fs::remove_file(&socket);
    result.and(stopped)
}
