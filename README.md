# HTTP server benchmarks

- HTTP over unix domain socket
- POST JSON
- Request validation
- [SQLite transaction](db/migrations/001_init.up.sql)

## Results

- [x] Go FastHTTP: 43K rps, p50 1.1ms
- [x] Kotlin Vert.x
  - [x] JVM: 33K rps, p50 1.4ms
  - [ ] Graal
- [x] Bun Hono: 21K rps, p50 1.9ms
- [x] Zig http.zig: 10K rps, p50 4.4ms (skill issue)
- [ ] Elixir
- [ ] Python

## WONTDO

- Kotlin Native Ktor: can't listen on unix domain sockets
