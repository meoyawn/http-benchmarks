(* Configure once on the main domain, before opening any connection or worker.
   sqlite3-ocaml does not expose sqlite3_config; SQL still uses its public API. *)
external configure_native : int -> int = "bench_sqlite_configure" [@@noalloc]

let settings = match Sys.getenv_opt "OCAML_SQLITE_CONFIG" with
  | None | Some "optimized" -> 2
  | Some "memstatus-off" -> 1
  | Some "default" -> 0
  | Some value -> invalid_arg ("unknown OCAML_SQLITE_CONFIG: " ^ value)

let configured = lazy (
  let code = configure_native settings in
  if code <> 0 then failwith (Printf.sprintf "SQLite configuration failed: %d" code))

let initialize () = Lazy.force configured
