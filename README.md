# HTTP server benchmarks

- OpenAPI validation
- SQLite INSERT

## TODO

- [x] Vert.x
  - [x] JVM: 32K rps, p50 1.4ms
  - [ ] Graal
- [x] Bun Hono: 21K rps, p50 1.9ms
- [x] Golang net/http: 35K rps, p50 1.3ms
- [ ] Zig
- [ ] Elixir

## Out

- Kotlin Native Ktor: can't listen on unix domain sockets
