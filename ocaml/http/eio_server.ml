exception Stop

let run ~connection_handler create socket_path domains =
  let request_counts = Array.make domains 0 in
  let minor_heap_words = (Gc.get ()).minor_heap_size in
  Eio_main.run (fun env -> Eio.Switch.run (fun writer_sw ->
    Writer.with_writer ~minor_heap_words ~sw:writer_sw ~domain_mgr:env#domain_mgr create (fun writer ->
      try Eio.Switch.run (fun sw ->
        let interrupted = Eio.Condition.create () in
        Eio.Fiber.fork ~sw (fun () ->
          Eio.Condition.await_no_mutex interrupted;
          raise Stop);
        let listener = Eio.Net.listen ~sw ~backlog:1024 env#net (`Unix socket_path) in
        let previous_signals = List.map (fun signal ->
          (* Signals may arrive in any domain. Only broadcast here; cancellation
             must run in the fiber that owns the HTTP switch. *)
          signal, Sys.signal signal (Sys.Signal_handle (fun _ -> Eio.Condition.broadcast interrupted)))
          [Sys.sigint; Sys.sigterm] in
        Eio.Switch.on_release sw (fun () ->
          List.iter (fun (signal, previous) -> Sys.set_signal signal previous) previous_signals);
        let serve ~ready index =
          (* Domain.spawn does not inherit Gc.set's minor-heap setting. *)
          Gc.set { (Gc.get ()) with minor_heap_size = minor_heap_words };
          let requests = ref 0 in
          let handle ~meth ~target body =
            incr requests;
            App.handle_using ~create:(Writer.create writer) ~meth ~target body in
          let connection client address = Eio.Switch.run (fun sw ->
            connection_handler ~handle ~sw client address) in
          Eio.Promise.resolve ready ();
          Fun.protect (fun () -> Eio.Net.run_server listener
            ~on_error:(function Eio.Io _ -> () | exn -> Printf.eprintf "connection: %s\n%!" (Printexc.to_string exn))
            connection)
            ~finally:(fun () -> request_counts.(index) <- !requests)
        in
        let ready = List.init (domains - 1) (fun index ->
          let ready, signal_ready = Eio.Promise.create () in
          Eio.Fiber.fork ~sw (fun () -> Eio.Domain_manager.run env#domain_mgr (fun () ->
            serve ~ready:signal_ready (index + 1)));
          ready) in
        List.iter Eio.Promise.await ready;
        Printf.printf "Listening on %s (SQLite %s), %d HTTP domains + 1 writer domain\n%!"
          socket_path (Sqlite3.sqlite_version_info ()) domains;
        let _, signal_ready = Eio.Promise.create () in
        serve ~ready:signal_ready 0)
      with Stop -> ())));
  (* Domain joins above establish ownership before the main domain reads these
     counters. Print here so concurrent formatted writes cannot interleave. *)
  Array.iteri (fun index count -> Printf.printf "HTTP domain %d: %d requests, minor heap %d words\n%!"
    index count minor_heap_words) request_counts
