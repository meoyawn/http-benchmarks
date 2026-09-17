type t = {
  db : Sqlite3.db;
  begin_tx : Sqlite3.stmt;
  insert_user : Sqlite3.stmt;
  insert_post : Sqlite3.stmt;
  commit : Sqlite3.stmt;
  rollback : Sqlite3.stmt;
  mutable failed : bool;
  lock : Mutex.t;
}

let check db operation code =
  if code <> Sqlite3.Rc.OK then
    failwith (operation ^ ": " ^ Sqlite3.Rc.to_string code ^ ": " ^ Sqlite3.errmsg db)

let execute db stmt =
  let result = Sqlite3.step stmt in
  let reset = Sqlite3.reset stmt in
  if result <> Sqlite3.Rc.DONE then
    failwith ("execute: " ^ Sqlite3.Rc.to_string result ^ ": " ^ Sqlite3.errmsg db);
  check db "reset" reset

let open_db path =
  Sqlite_runtime.initialize ();
  let db = Sqlite3.db_open ~mode:`NO_CREATE ~mutex:`NO path in
  let prepared = ref [] in
  let prepare sql =
    let stmt = Sqlite3.prepare db sql in
    prepared := stmt :: !prepared;
    stmt
  in
  try
    check db "configure SQLite" (Sqlite3.exec db
      "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA foreign_keys=ON; \
       PRAGMA busy_timeout=10000; PRAGMA cache_size=-2000; PRAGMA wal_autocheckpoint=1000; \
       PRAGMA temp_store=MEMORY; PRAGMA mmap_size=0; PRAGMA optimize=0x10002;");
    { db;
      begin_tx = prepare "BEGIN IMMEDIATE";
      insert_user = prepare "INSERT OR IGNORE INTO users (email) VALUES (?)";
      insert_post = prepare
        "INSERT INTO posts (content, user_id) SELECT ?, id FROM users WHERE email IS ? \
         RETURNING id, user_id, content, created_at, updated_at";
      commit = prepare "COMMIT"; rollback = prepare "ROLLBACK"; failed = false; lock = Mutex.create () }
  with exn ->
    List.iter (fun stmt -> ignore (Sqlite3.finalize stmt)) !prepared;
    ignore (Sqlite3.db_close db);
    raise exn

(* Called only by the dedicated writer domain, or with [lock] held by the
   synchronous library comparison adapters. No suspension inside a transaction. *)
let create_transaction s (body : Model.new_post) : Model.post =
  if s.failed then failwith "database is unavailable after a failed rollback";
  execute s.db s.begin_tx;
  try
    check s.db "bind email" (Sqlite3.bind_text s.insert_user 1 body.email);
    execute s.db s.insert_user;
    check s.db "clear user" (Sqlite3.clear_bindings s.insert_user);
    check s.db "bind content" (Sqlite3.bind_text s.insert_post 1 body.content);
    check s.db "bind email" (Sqlite3.bind_text s.insert_post 2 body.email);
    if Sqlite3.step s.insert_post <> Sqlite3.Rc.ROW then
      failwith ("insert post: " ^ Sqlite3.errmsg s.db);
    let post = Model.{
      id = Sqlite3.column_int64 s.insert_post 0;
      user_id = Sqlite3.column_int64 s.insert_post 1;
      content = Sqlite3.column_text s.insert_post 2;
      created_at = Sqlite3.column_int64 s.insert_post 3;
      updated_at = Sqlite3.column_int64 s.insert_post 4;
    } in
    execute s.db s.insert_post;
    check s.db "clear post" (Sqlite3.clear_bindings s.insert_post);
    execute s.db s.commit;
    post
  with exn ->
    List.iter (fun stmt ->
      ignore (Sqlite3.reset stmt); ignore (Sqlite3.clear_bindings stmt))
      [s.insert_user; s.insert_post];
    (try execute s.db s.rollback with _ -> s.failed <- true);
    raise exn

let create s body =
  Mutex.lock s.lock;
  Fun.protect ~finally:(fun () -> Mutex.unlock s.lock) (fun () -> create_transaction s body)

let close s =
  let codes = List.map Sqlite3.finalize
    [s.begin_tx; s.insert_user; s.insert_post; s.commit; s.rollback] in
  let optimized = Sqlite3.exec s.db "PRAGMA optimize" in
  let closed = Sqlite3.db_close s.db in
  if not closed || optimized <> Sqlite3.Rc.OK || List.exists ((<>) Sqlite3.Rc.OK) codes then
    failwith "failed to close SQLite cleanly"
