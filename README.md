# HTTP server benchmarks

- HTTP POST JSON
- Request validation
- SQLite transaction

## TODO

- [x] Go FastHTTP: 43K rps, p50 1.1ms
- [x] Vert.x
  - [x] JVM: 33K rps, p50 1.4ms
  - [ ] Graal
- [x] Bun Hono: 21K rps, p50 1.9ms
- [x] Zig http.zig: 10K rps, p50 4.4ms (skill issue)
- [ ] Elixir
- [ ] Python

## Skip

- Kotlin Native Ktor: can't listen on unix domain sockets
