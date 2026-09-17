type new_post = { email : string; content : string }
type post = {
  id : int64;
  user_id : int64;
  content : string;
  created_at : int64;
  updated_at : int64;
}

(* Same whole-string ASCII rule as Go and Kotlin. Each HTTP domain owns its
   compiled matcher, including Re's lazily populated automaton state cache. *)
let email_pattern = Domain.DLS.new_key (fun () ->
  Re.compile (Re.whole_string (Re.Perl.re {|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|})))

let valid_email email = Re.execp (Domain.DLS.get email_pattern) email

let validate (body : new_post) =
  (if valid_email body.email then [] else [ "email: invalid address" ])
  @ if body.content <> "" then [] else [ "content: must be at least 1 character" ]
