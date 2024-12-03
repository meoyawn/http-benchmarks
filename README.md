# HTTP server benchmarks

- HTTP POST JSON
- Request validation
- SQLite transaction

## TODO

- [x] Go [FastHTTP](https://github.com/fasthttp/router): 43K rps, p50 1.1ms
- [x] [Vert.x](https://github.com/eclipse-vertx/vert.x)
  - [x] JVM: 33K rps, p50 1.4ms
  - [ ] Graal
- [x] Bun [Hono](https://github.com/honojs/hono): 21K rps, p50 1.9ms
- [x] Zig [http.zig](https://github.com/karlseguin/http.zig): 10K rps, p50 4.4ms (skill issue)
- [ ] Elixir
- [ ] Python

## Skip

- Kotlin Native Ktor: can't listen on unix domain sockets
