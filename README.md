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

| Framework         | RPS | p50 latency |
| ----------------- | --- | ----------- |
| Rust Actix-Web    | 51K | 0.9ms       |
| Zig http.zig      | 43K | 1ms         |
| Go FastHTTP       | 43K | 1.1ms       |
| Kotlin Vert.x     | 40K | 1.1ms       |
| Bun Hono          | 21K | 1.9ms       |
| Python Blacksheep | 19K | 2.5ms       |
| Elixir Bandit     | 10K | 4.9ms       |

## Won't do

- Kotlin Native Ktor: can't listen on unix domain sockets

## Bonus

HTTP POST JSON echo

```sh
oha http://localhost/echo --no-tui --unix-socket /tmp/benchmark.sock -z 10s -m POST -T 'application/json' -d '{ "content": "oha benchmark", "email": "foo@gmail.com" }'
```

| Framework         | RPS  | p50 latency |
| ----------------- | ---- | ----------- |
| Rust Actix-Web    | 266K | 0.2ms       |
| Zig http.zig      | 264K | 0.2ms       |
| Kotlin Vert.x     | 207K | 0.2ms       |
| Go FastHTTP       | 199K | 0.2ms       |
| Python Blacksheep | 192K | 0.2ms       |
| Bun Hono          | 156K | 0.3ms       |
| Elixir Bandit     | 139K | 0.3ms       |
