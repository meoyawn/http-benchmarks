use rust_benchmark::{Database, NewPost, open, sqlite_runtime};

fn setup() -> (tempfile::TempDir, Database) {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("test.sqlite");
    open(&path)
        .unwrap()
        .execute_batch(include_str!("../../db/migrations/001_init.up.sql"))
        .unwrap();
    (directory, Database::open(&path).unwrap())
}

fn input(email: &str, content: &str) -> NewPost {
    NewPost {
        email: email.into(),
        content: content.into(),
    }
}

#[test]
fn shared_email_rule_and_nonempty_content() {
    let fixtures: serde_json::Value =
        serde_json::from_str(include_str!("../../testdata/email-validation.json")).unwrap();
    for fixture in fixtures.as_array().unwrap() {
        let post = input(fixture["email"].as_str().unwrap(), "hello");
        assert_eq!(
            post.valid(),
            fixture["valid"].as_bool().unwrap(),
            "{post:?}"
        );
    }
    assert!(!input("foo@gmail.com", "").valid());
    assert!(input("foo@gmail.com", " ").valid());
}

#[test]
fn engine_and_every_connection_pragma_match_shared_configuration() {
    let (directory, _db) = setup();
    let conn = open(&directory.path().join("test.sqlite")).unwrap();
    assert_eq!(rusqlite::version(), sqlite_runtime::config().version);
    for (name, expected) in &sqlite_runtime::config().pragmas {
        let actual: serde_json::Value = conn
            .pragma_query_value(None, name, |row| {
                Ok(match row.get_ref(0)? {
                    rusqlite::types::ValueRef::Text(value) => {
                        serde_json::Value::String(std::str::from_utf8(value).unwrap().into())
                    }
                    rusqlite::types::ValueRef::Integer(value) => value.into(),
                    value => panic!("unexpected pragma type: {value:?}"),
                })
            })
            .unwrap();
        assert_eq!(&actual, expected, "PRAGMA {name}");
    }
    let page_size: i32 = conn
        .pragma_query_value(None, "page_size", |row| row.get(0))
        .unwrap();
    assert_eq!(page_size, sqlite_runtime::config().runtime.page_bytes);
    let report: serde_json::Value =
        serde_json::from_str(&sqlite_runtime::snapshot().unwrap()).unwrap();
    let config: serde_json::Value =
        serde_json::from_str(include_str!("../../db/sqlite-config.json")).unwrap();
    for define in config["defines"].as_array().unwrap() {
        let define = define.as_str().unwrap();
        if let Some(option) = define.strip_prefix("SQLITE_") {
            if option == "ENABLE_JSON1" {
                continue;
            }
            assert!(
                report["compile_options"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .any(|value| value == option || value == option.trim_end_matches("=1")),
                "missing compile option: {option}"
            );
        }
    }
}

#[test]
fn returned_post_is_committed_with_all_five_columns() {
    let (directory, mut db) = setup();
    let post = db
        .transact(&input("foo@gmail.com", "quotes \" newline\n雪\0"))
        .unwrap();
    assert_eq!(post.id, 1);
    assert_eq!(post.user_id, 1);
    assert!(post.created_at > 1_700_000_000_000);
    assert_eq!(post.updated_at, post.created_at);
    let observer = open(&directory.path().join("test.sqlite")).unwrap();
    let stored = observer
        .query_row(
            "SELECT id, user_id, content, created_at, updated_at FROM posts",
            [],
            |r| {
                Ok(rust_benchmark::Post {
                    id: r.get(0)?,
                    user_id: r.get(1)?,
                    content: r.get(2)?,
                    created_at: r.get(3)?,
                    updated_at: r.get(4)?,
                })
            },
        )
        .unwrap();
    assert_eq!(stored, post);
}

#[test]
fn case_insensitive_users_and_autoincrement_are_not_cached() {
    let (directory, mut db) = setup();
    for email in ["foo@gmail.com", "FOO@GMAIL.COM", "foo@gmail.com"] {
        assert_eq!(db.transact(&input(email, "hello")).unwrap().user_id, 1);
    }
    assert_eq!(
        db.transact(&input("new@gmail.com", "hello"))
            .unwrap()
            .user_id,
        4
    );
    let observer = open(&directory.path().join("test.sqlite")).unwrap();
    assert_eq!(
        observer
            .query_row("SELECT count(*) FROM users", [], |r| r.get::<_, i64>(0))
            .unwrap(),
        2
    );
    assert_eq!(
        observer
            .query_row(
                "SELECT seq FROM sqlite_sequence WHERE name IS 'users'",
                [],
                |r| r.get::<_, i64>(0)
            )
            .unwrap(),
        4
    );
}

#[test]
fn statement_failure_rolls_back_user_and_allows_the_next_write() {
    let (directory, mut db) = setup();
    assert!(db.transact(&input("failed@gmail.com", "")).is_err());
    let post = db.transact(&input("ok@gmail.com", "hello")).unwrap();
    assert_eq!(post.user_id, 1);
    assert_eq!(post.id, 1);
    let observer = open(&directory.path().join("test.sqlite")).unwrap();
    assert_eq!(
        observer
            .query_row(
                "SELECT count(*) FROM users WHERE email IS 'failed@gmail.com'",
                [],
                |r| r.get::<_, i64>(0)
            )
            .unwrap(),
        0
    );
}

#[test]
fn commit_failure_rolls_back_and_allows_the_next_write() {
    let (directory, mut db) = setup();
    let observer = open(&directory.path().join("test.sqlite")).unwrap();
    observer.execute_batch("CREATE TABLE invalid_references (user_id INTEGER REFERENCES users(id) DEFERRABLE INITIALLY DEFERRED); CREATE TRIGGER fail_commit AFTER INSERT ON posts BEGIN INSERT INTO invalid_references VALUES (999999); END;").unwrap();
    assert!(db.transact(&input("failed@gmail.com", "hello")).is_err());
    assert_eq!(
        observer
            .query_row("SELECT count(*) FROM users", [], |r| r.get::<_, i64>(0))
            .unwrap(),
        0
    );
    assert_eq!(
        observer
            .query_row("SELECT count(*) FROM posts", [], |r| r.get::<_, i64>(0))
            .unwrap(),
        0
    );
    observer.execute_batch("DROP TRIGGER fail_commit").unwrap();
    assert_eq!(db.transact(&input("ok@gmail.com", "hello")).unwrap().id, 1);
}

#[test]
fn json_is_parsed_and_reserialized_without_losing_escapes() {
    let post = input("foo@gmail.com", "\"\\\n\t雪\0");
    let encoded = serde_json::to_vec(&post).unwrap();
    let decoded: NewPost = serde_json::from_slice(&encoded).unwrap();
    assert_eq!(post, decoded);
    for invalid in [
        r#"{"content":1,"email":"a@b.com"}"#,
        r#"{"content":"x"}"#,
        r#"{"content":"x","email":"a@b.com"} trailing"#,
    ] {
        assert!(serde_json::from_str::<NewPost>(invalid).is_err());
    }
}
