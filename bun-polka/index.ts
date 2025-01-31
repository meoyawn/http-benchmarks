import * as http from "node:http"
import polka from "polka"

const bodyParser: polka.Middleware = (req, res, next) => {
  const ct = req.headers["content-type"]

  switch (true) {
    case ct?.startsWith("application/json"): {
      let data = ""
      req
        .on("error", next)
        .on("data", (chunk: Uint8Array) => (data += chunk))
        .on("end", () => {
          req.body = JSON.parse(data)
          next()
        })
      break
    }

    case ct?.startsWith("application/x-www-form-urlencoded"): {
      throw new Error("TODO")
    }

    default:
      next()
  }
}

const app = polka({})
  .use(bodyParser)
  .post(
    "/echo",
    (req, res) =>
      void res
        .setHeader("Content-Type", "application/json")
        .end(JSON.stringify(req.body)),
  )

const path = "/tmp/benchmark.sock"
http.createServer(app.handler as http.RequestListener).listen({ path })

console.log(`Listening on ${path}`)
