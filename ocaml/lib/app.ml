let handle_using ~create ~meth ~target body =
  let path = List.hd (String.split_on_char '?' target) in
  if path <> "/echo" && path <> "/posts" then 404, Json_codec.errors ["route not found"]
  else if meth <> "POST" then 405, Json_codec.errors ["method must be POST"]
  else
    try
      let request = Json_codec.decode body in
      if path = "/echo" then 200, Json_codec.echo request
      else match Model.validate request with
      | [] -> (match create request with
          | Ok post -> 201, Json_codec.post post
          | Error exn ->
              Printf.eprintf "request failed: %s\n%!" (Printexc.to_string exn);
              500, Json_codec.errors ["database error"])
      | errors -> 400, Json_codec.errors errors
    with
    | Json_codec.Invalid_body -> 400, Json_codec.errors ["invalid JSON body"]

let handle store = handle_using ~create:(fun request ->
  try Ok (Store.create store request) with exn -> Error exn)
