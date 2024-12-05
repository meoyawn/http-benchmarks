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

| Framework      | RPS | p50 latency |
| -------------- | --- | ----------- |
| Rust Actix-Web | 51K | 0.9ms       |
| Zig http.zig   | 43K | 1ms         |
| Go FastHTTP    | 43K | 1.1ms       |
| Kotlin Vert.x  | 33K | 1.4ms       |
| Bun Hono       | 21K | 1.9ms       |

## Won't do

- Kotlin Native Ktor: can't listen on unix domain sockets

## Bonus

HTTP POST JSON echo

```sh
oha http://localhost/echo --no-tui --unix-socket /tmp/benchmark.sock -z 10s -m POST -T 'application/json' -d '{ "content": "oha benchmark", "email": "oha@gmail.com" }'
```

| Framework     | RPS  | p50 latency |
| ------------- | ---- | ----------- |
| Kotlin Vert.x | 158K | 0.3ms       |
