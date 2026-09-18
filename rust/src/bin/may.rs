#![forbid(unsafe_code)]

use may_minihttp::{
    Response,
    unix::{Request, Server, Service},
};
use rust_benchmark::{BODY_LIMIT, Client, NewPost, Options};
use signal_hook::{
    consts::{SIGINT, SIGTERM},
    iterator::Signals,
};
use std::io;

#[derive(Clone)]
struct App(Client);

impl Service for App {
    fn call(&mut self, request: Request<'_, '_>, response: &mut Response<'_>) -> io::Result<()> {
        let posts = match (request.method(), request.path()) {
            ("POST", "/posts") => true,
            ("POST", "/echo") => false,
            _ => {
                response.status_code(404, "Not Found");
                return Ok(());
            }
        };
        let input: NewPost = match serde_json::from_slice(request.body()) {
            Ok(input) => input,
            Err(_) => {
                response.status_code(400, "Bad Request");
                return Ok(());
            }
        };
        if posts {
            if !input.valid() {
                response.status_code(400, "Bad Request");
                return Ok(());
            }
            let post = self.0.write_coroutine(input).map_err(io::Error::other)?;
            response
                .status_code(201, "Created")
                .header("Content-Type: application/json");
            response.body_vec(serde_json::to_vec(&post)?);
        } else {
            response.header("Content-Type: application/json");
            response.body_vec(serde_json::to_vec(&input)?);
        }
        Ok(())
    }
}

fn main() -> io::Result<()> {
    let options = Options::parse()?;
    may::config()
        .set_workers(options.workers)
        .set_pool_capacity(64)
        .set_stack_size(64 * 1024);
    may::config().set_worker_pin(false);
    let mut signals = Signals::new([SIGINT, SIGTERM])?;
    let (client, writer) = options.start()?;
    let server = Server::bind(&options.socket, BODY_LIMIT, App(client))?;
    options.listening();
    signals.forever().next();
    server.shutdown();
    writer.stop()
}
