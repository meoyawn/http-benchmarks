import polka from "polka"
import * as bp from "body-parser"
import * as http from "node:http";

const app = polka()
  .use(bp.json())
  .post("/echo", (req, res) => void res.setHeader("Content-Type", "application/json").end(JSON.stringify(req.body)))

http.createServer(app.handler as http.RequestListener).listen({
  path: "/tmp/benchmark.sock",
})
