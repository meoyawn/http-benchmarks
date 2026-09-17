type job = Write of Model.new_post * (Model.post, exn) result Eio.Promise.u | Stop
type t = job Eio.Stream.t

let run create jobs =
  let rec loop () =
    match Eio.Stream.take jobs with
    | Stop -> ()
    | Write (body, reply) ->
        let result = try Ok (create body) with exn -> Error exn in
        (* Resolve each request immediately after its own commit, before taking
           another job. The bounded queue does not batch transactions or replies. *)
        Eio.Promise.resolve reply result;
        loop ()
  in
  loop ()

let create jobs body =
  let reply, resolve = Eio.Promise.create () in
  Eio.Stream.add jobs (Write (body, resolve));
  Eio.Promise.await reply

let with_writer ~minor_heap_words ~sw ~domain_mgr create fn =
  let jobs = Eio.Stream.create 1024 in
  let ready, signal_ready = Eio.Promise.create () in
  let worker = Eio.Fiber.fork_promise ~sw (fun () ->
    Eio.Domain_manager.run domain_mgr (fun () ->
      Gc.set { (Gc.get ()) with minor_heap_size = minor_heap_words };
      Eio.Promise.resolve signal_ready ();
      run create jobs)) in
  Eio.Promise.await ready;
  Fun.protect (fun () -> fn jobs) ~finally:(fun () ->
    (* The HTTP switch has already stopped every producer. Drain queued writes
       before joining the domain and allowing the caller to close SQLite. *)
    Eio.Cancel.protect (fun () ->
      Eio.Stream.add jobs Stop;
      Eio.Promise.await_exn worker))
