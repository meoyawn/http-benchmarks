# HTTP server benchmarks

- HTTP over unix domain socket
- POST JSON
- Request validation
- [SQLite transaction](db/migrations/001_init.up.sql)

## Results

Apple M1 Pro, running everything on the same machine

```sh
oha http://localhost/posts --no-tui --unix-socket /tmp/benchmark.sock -z 10s -m POST -T 'application/json' -d '{ "content": "oha benchmark", "email": "oha@gmail.com" }'
```

- [x] Rust Actix-Web: 51K rps, p50 0.9ms
- [x] Zig http.zig: 43K rps, p50 1ms
- [x] Go FastHTTP: 43K rps, p50 1.1ms
- [x] Kotlin Vert.x
  - [x] JVM: 33K rps, p50 1.4ms
  - [ ] Graal
- [x] Bun Hono: 21K rps, p50 1.9ms
- [ ] Elixir
- [ ] Python

## Won't do

- Kotlin Native Ktor: can't listen on unix domain sockets
