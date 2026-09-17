let serve ~handle ~sw socket address =
  (* Cohttp 6.3's body flow mishandles the offset when repeatedly splitting a
     chunk into smaller reads. Its transfer reader emits at most 32 KiB, so
     consume whole chunks into connection-owned scratch space. *)
  let scratch = Cstruct.create 0x8000 in
  let buffer = Buffer.create 128 in
  let server = Cohttp_eio.Server.make ~callback:(fun _ request body ->
    Buffer.clear buffer;
    let too_large = ref false in
    (try while true do
      let count = Eio.Flow.single_read body scratch in
      if Buffer.length buffer + count > 1024 * 1024 then too_large := true;
      if not !too_large then Buffer.add_string buffer (Cstruct.to_string ~len:count scratch)
    done with End_of_file -> ());
    let code, response = if !too_large then 413, Json_codec.errors ["request body exceeds 1 MiB"]
      else handle ~meth:(Cohttp.Code.string_of_method (Cohttp.Request.meth request))
        ~target:(Cohttp.Request.resource request) (Buffer.contents buffer)
    in
    let headers = Cohttp.Header.of_list
      (["content-type", "application/json"] @ if code = 405 then ["allow", "POST"] else []) in
    Cohttp_eio.Server.respond_string ~headers ~status:(Cohttp.Code.status_of_code code) ~body:response ()) () in
  let input = Eio.Buf_read.of_flow ~max_size:(1024 * 1024) socket in
  Eio.Buf_write.with_flow socket (fun output ->
    Cohttp_eio.Server.callback server (sw, address) input output)
