exception Invalid_body

(* Yojson also accepts comments and non-finite numbers. The HTTP contract is
   standard JSON, so reject those extensions outside quoted strings first.
   JSON syntax, escapes and trailing input are still checked by Yojson. *)
let check_standard source =
  let length = String.length source in
  let rec scan index quoted =
    if index < length then
      match source.[index], quoted with
      | '\\', true -> scan (index + 2) true
      | '"', _ -> scan (index + 1) (not quoted)
      | ('/' | 'N' | 'I' | '(' | ')' | '<' | '>' | '\''), false -> raise Invalid_body
      | c, true when Char.code c < 0x20 -> raise Invalid_body
      | c, false when Char.code c < 0x20 && c <> '\n' && c <> '\r' && c <> '\t' -> raise Invalid_body
      | _ -> scan (index + 1) quoted
  in
  scan 0 false

(* Each domain owns its mutable scratch state. Codec calls never suspend,
   and returned strings own their storage. *)
let buffers = Domain.DLS.new_key (fun () -> Buffer.create 128, Buffer.create 256)

let decode source : Model.new_post =
  check_standard source;
  let read_buffer, _ = Domain.DLS.get buffers in
  (* Unlike the generated of_string helper, Util.Json also checks trailing input. *)
  try Atdgen_runtime.Util.Json.from_string ~buf:read_buffer Request_j.read_new_post source
  with Yojson.Json_error _ | Yojson.End_of_input | Atdgen_runtime.Oj_run.Error _ -> raise Invalid_body

let echo (body : Model.new_post) =
  let _, write_buffer = Domain.DLS.get buffers in
  Buffer.clear write_buffer;
  Request_j.write_new_post write_buffer body;
  Buffer.contents write_buffer

let post (row : Model.post) =
  let _, write_buffer = Domain.DLS.get buffers in
  let number value = `Intlit (Int64.to_string value) in
  Yojson.Safe.to_string ~buf:write_buffer (`Assoc [
    "id", number row.id; "user_id", number row.user_id; "content", `String row.content;
    "created_at", number row.created_at; "updated_at", number row.updated_at;
  ])

let errors messages =
  let _, write_buffer = Domain.DLS.get buffers in
  Yojson.Safe.to_string ~buf:write_buffer (`List (List.map (fun x -> `String x) messages))
