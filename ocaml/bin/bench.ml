let () =
  Sys.set_signal Sys.sigpipe Sys.Signal_ignore;
  let database = ref "../db/db.sqlite" in
  let socket = ref "/tmp/benchmark.sock" in
  let domains = ref 1 in
  let minor_heap_words = ref 1_048_576 in
  Arg.parse [
    "-db", Arg.Set_string database, "Path to an already migrated SQLite database";
    "-socket", Arg.Set_string socket, "Unix domain socket path";
    "-domains", Arg.Set_int domains, "HTTP domains (default 1), plus one SQLite writer domain";
    "-minor-heap-words", Arg.Set_int minor_heap_words, "Minor heap per domain, in words (default 1048576 = 8 MiB)";
  ] (fun argument -> raise (Arg.Bad ("unexpected argument: " ^ argument))) "OCaml HTTP benchmark";
  try
    if !socket = "" then failwith "socket path must not be empty";
    if !domains < 1 then failwith "HTTP domains must be positive";
    if !minor_heap_words < 256 then failwith "minor heap must contain at least 256 words";
    Gc.set { (Gc.get ()) with minor_heap_size = !minor_heap_words };
    (match Unix.lstat !socket with
     | _ -> failwith ("socket path already exists: " ^ !socket)
     | exception Unix.Unix_error (Unix.ENOENT, _, _) -> ());
    let store = Store.open_db !database in
    Fun.protect ~finally:(fun () -> Store.close store)
      (fun () -> Eio_server.run ~connection_handler:Cohttp_adapter.serve
        (Store.create_transaction store) !socket !domains)
  with exn ->
    Printf.eprintf "%s\n%!" (Printexc.to_string exn);
    exit 1
