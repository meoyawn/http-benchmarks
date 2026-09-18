package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/tailscale/sqlite/cgosqlite"
	"github.com/tailscale/sqlite/sqliteh"
	"github.com/valyala/fasthttp"
	"golang.org/x/sync/errgroup"
)

func must(t testing.TB, err error) {
	t.Helper()
	if err != nil {
		t.Fatal(err)
	}
}

func TestSharedEmailValidation(t *testing.T) {
	data, err := os.ReadFile("../testdata/email-validation.json")
	must(t, err)
	var cases []struct {
		Email string `json:"email"`
		Valid bool   `json:"valid"`
	}
	must(t, json.Unmarshal(data, &cases))
	for _, test := range cases {
		if got := len(validate(NewPost{Email: test.Email, Content: "valid"})) == 0; got != test.Valid {
			t.Errorf("email %q: got %v, want %v", test.Email, got, test.Valid)
		}
	}
}

func TestSharedSQLiteConfiguration(t *testing.T) {
	data, err := os.ReadFile("../db/sqlite-config.json")
	must(t, err)
	var config struct {
		Version string         `json:"version"`
		Defines []string       `json:"defines"`
		Pragmas map[string]any `json:"pragmas"`
	}
	must(t, json.Unmarshal(data, &config))
	store := testStore(t)
	if store.version != config.Version {
		t.Fatalf("SQLite version %s, want %s", store.version, config.Version)
	}
	for name, expected := range config.Pragmas {
		query(t, store, "PRAGMA "+name, func(stmt sqliteh.Stmt) {
			if got := stmt.ColumnText(0); got != fmt.Sprint(expected) {
				t.Errorf("SQLite %s: got %s, want %v", name, got, expected)
			}
		})
	}
	query(t, store, "SELECT json_group_array(compile_options) FROM pragma_compile_options", func(stmt sqliteh.Stmt) {
		var options []string
		must(t, json.Unmarshal([]byte(stmt.ColumnText(0)), &options))
		actual := make(map[string]bool)
		for _, option := range options {
			actual[option] = true
		}
		for _, define := range config.Defines {
			if !strings.HasPrefix(define, "SQLITE_") || define == "SQLITE_ENABLE_JSON1" {
				continue // Platform defines and the obsolete JSON1 switch are not reported.
			}
			option := strings.TrimPrefix(define, "SQLITE_")
			if !actual[option] && !actual[strings.TrimSuffix(option, "=1")] {
				t.Errorf("SQLite compile option missing: %s", option)
			}
		}
		t.Logf("SQLite compile options: %s", stmt.ColumnText(0))
	})
}

func testDatabase(t testing.TB) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "test.sqlite")
	db, err := cgosqlite.Open(path, sqliteh.OpenFlagsDefault, "")
	must(t, err)
	schema, err := os.ReadFile("../db/migrations/001_init.up.sql")
	must(t, err)
	must(t, execScript(db, string(schema)))
	must(t, db.Close())
	return path
}

func testStore(t testing.TB) *postStore {
	t.Helper()
	store, err := openStore(testDatabase(t))
	must(t, err)
	t.Cleanup(func() { must(t, store.close()) })
	return store
}

func query(t testing.TB, s *postStore, sql string, read func(sqliteh.Stmt)) {
	t.Helper()
	s.mu.Lock()
	defer s.mu.Unlock()
	stmt, _, err := s.db.Prepare(sql, 0)
	must(t, err)
	defer func() { must(t, stmt.Finalize()) }()
	row, err := stmt.Step(nil)
	must(t, err)
	if !row {
		t.Fatalf("no row from %q", sql)
	}
	read(stmt)
}

func count(t testing.TB, s *postStore, sql string) (n int64) {
	t.Helper()
	query(t, s, sql, func(stmt sqliteh.Stmt) { n = stmt.ColumnInt64(0) })
	return
}

func testServer(t *testing.T, handler fasthttp.RequestHandler) (*http.Client, context.CancelFunc, *errgroup.Group) {
	t.Helper()
	dir, err := os.MkdirTemp("/tmp", "go-http-test-") // Unix sockets have a short path limit.
	must(t, err)
	t.Cleanup(func() { must(t, os.RemoveAll(dir)) })
	path := filepath.Join(dir, "http.sock")
	listener, err := net.Listen("unix", path)
	must(t, err)
	h := newHTTPServer(handler)
	ctx, cancel := context.WithCancel(t.Context())
	var server errgroup.Group
	server.Go(func() error { return serve(ctx, h, listener) })
	transport := &http.Transport{DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
		return (&net.Dialer{}).DialContext(ctx, "unix", path)
	}, MaxIdleConnsPerHost: 50}
	client := &http.Client{Transport: transport, Timeout: 10 * time.Second}
	t.Cleanup(func() { transport.CloseIdleConnections(); cancel(); must(t, server.Wait()) })
	return client, cancel, &server
}

func request(client *http.Client, method, path, body string) (int, http.Header, []byte, error) {
	req, err := http.NewRequest(method, "http://localhost"+path, strings.NewReader(body))
	if err != nil {
		return 0, nil, nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	res, err := client.Do(req)
	if err != nil {
		return 0, nil, nil, err
	}
	defer res.Body.Close()
	data, err := io.ReadAll(res.Body)
	return res.StatusCode, res.Header, data, err
}

func createPost(client *http.Client, body NewPost) (Post, error) {
	data, err := json.Marshal(body)
	if err != nil {
		return Post{}, err
	}
	status, headers, data, err := request(client, "POST", "/posts", string(data))
	if err != nil {
		return Post{}, err
	}
	if status != 201 || headers.Get("Content-Type") != "application/json" {
		return Post{}, fmt.Errorf("POST /posts: status %d, headers %v, body %s", status, headers, data)
	}
	var post Post
	err = json.Unmarshal(data, &post)
	return post, err
}

func TestEchoAndPersistedPost(t *testing.T) {
	store := testStore(t)
	client, _, _ := testServer(t, newHandler(store))
	body := NewPost{Email: "foo@example.com", Content: "Unicode 日本語 🌍, quotes \" and \\, newline\n, NUL\x00end"}
	encoded, err := json.Marshal(body)
	must(t, err)
	status, headers, data, err := request(client, "POST", "/echo", string(encoded))
	must(t, err)
	if status != 200 || headers.Get("Content-Type") != "application/json" {
		t.Fatalf("echo: %d %v %s", status, headers, data)
	}
	var echoed NewPost
	must(t, json.Unmarshal(data, &echoed))
	if echoed != body {
		t.Fatalf("echo: got %+v, want %+v", echoed, body)
	}
	post, err := createPost(client, body)
	must(t, err)
	if post.ID != 1 || post.UserID != 1 || post.Content != body.Content || post.CreatedAt < time.Now().Add(-time.Minute).UnixMilli() || post.UpdatedAt != post.CreatedAt {
		t.Fatalf("unexpected post: %+v", post)
	}
	query(t, store, "SELECT content, length(CAST(content AS BLOB)) FROM posts", func(stmt sqliteh.Stmt) {
		if stmt.ColumnText(0) != body.Content || stmt.ColumnInt64(1) != int64(len(body.Content)) {
			t.Fatal("stored text differs from submitted text")
		}
	})
	body.Email = "FOO@example.com"
	second, err := createPost(client, body)
	must(t, err)
	if second.ID != 2 || second.UserID != post.UserID || count(t, store, "SELECT count(*) FROM users") != 1 {
		t.Fatalf("case-insensitive email reuse: %+v", second)
	}
}

func TestInvalidRequestsAndRouting(t *testing.T) {
	store := testStore(t)
	client, _, _ := testServer(t, newHandler(store))
	for _, input := range []string{
		`{`, `{"email":"foo@example.com","content":"x"} {}`,
		`{"email":123,"content":"x"}`, `{"email":"foo@example.com","content":5}`,
		`{"email":"foo@example.com"}`, `{"email":"foo@example.com","content":""}`,
		`{"email":"invalid","content":"x"}`, `{"email":"Name <foo@example.com>","content":"x"}`, `null`,
	} {
		t.Run(input, func(t *testing.T) {
			status, _, _, err := request(client, "POST", "/posts", input)
			must(t, err)
			if status != 400 {
				t.Fatalf("status = %d, want 400", status)
			}
		})
	}
	for _, path := range []string{"/echo", "/posts"} {
		status, headers, _, err := request(client, "GET", path, "")
		must(t, err)
		if status != 405 || headers.Get("Allow") != "POST" {
			t.Fatalf("GET %s: %d %v", path, status, headers)
		}
	}
	status, _, _, err := request(client, "POST", "/missing", `{}`)
	must(t, err)
	if status != 404 {
		t.Fatalf("missing route: %d", status)
	}
	if count(t, store, "SELECT count(*) FROM users") != 0 || count(t, store, "SELECT count(*) FROM posts") != 0 {
		t.Fatal("invalid request changed the database")
	}
}

func TestRollbackAndRecovery(t *testing.T) {
	store := testStore(t)
	must(t, execScript(store.db, `CREATE TRIGGER reject_post BEFORE INSERT ON posts
WHEN NEW.content IS 'reject' BEGIN SELECT RAISE(ABORT, 'test rejection'); END;`))
	client, _, _ := testServer(t, newHandler(store))
	status, _, data, err := request(client, "POST", "/posts", `{"email":"new@example.com","content":"reject"}`)
	must(t, err)
	if status != 500 || bytes.Contains(data, []byte("test rejection")) {
		t.Fatalf("database failure: %d %s", status, data)
	}
	if count(t, store, "SELECT count(*) FROM users") != 0 || count(t, store, "SELECT count(*) FROM posts") != 0 {
		t.Fatal("failed transaction was not rolled back")
	}
	post, err := createPost(client, NewPost{Email: "new@example.com", Content: "accepted"})
	must(t, err)
	if post.ID != 1 || post.UserID != 1 {
		t.Fatalf("recovery: %+v", post)
	}
}

func TestConcurrentPosts(t *testing.T) {
	store := testStore(t)
	client, _, _ := testServer(t, newHandler(store))
	var results [100]Post
	var requests errgroup.Group
	requests.SetLimit(50)
	for i := range results {
		requests.Go(func() error {
			post, err := createPost(client, NewPost{Email: "same@example.com", Content: fmt.Sprintf("post %d", i)})
			results[i] = post
			return err
		})
	}
	must(t, requests.Wait())
	seen := make(map[int64]bool)
	for i, post := range results {
		if seen[post.ID] || post.UserID != 1 || post.Content != fmt.Sprintf("post %d", i) {
			t.Fatalf("request %d: %+v", i, post)
		}
		seen[post.ID] = true
	}
	if count(t, store, "SELECT count(*) FROM posts") != 100 || count(t, store, "SELECT count(*) FROM users") != 1 {
		t.Fatal("concurrent inserts lost rows or duplicated users")
	}
}

func TestShutdownDrainsActiveRequest(t *testing.T) {
	store := testStore(t)
	entered := make(chan struct{})
	handler := newHandler(store)
	client, cancel, server := testServer(t, func(ctx *fasthttp.RequestCtx) { close(entered); handler(ctx) })
	store.mu.Lock()
	var requests errgroup.Group
	requests.Go(func() error {
		_, err := createPost(client, NewPost{Email: "stop@example.com", Content: "finish before closing"})
		return err
	})
	select {
	case <-entered:
	case <-time.After(10 * time.Second):
		store.mu.Unlock()
		t.Fatal("request did not reach handler")
	}
	cancel()
	store.mu.Unlock()
	must(t, requests.Wait())
	must(t, server.Wait())
	if count(t, store, "SELECT count(*) FROM posts") != 1 {
		t.Fatal("shutdown lost the active write")
	}
}

func TestSQLiteSettingsAndForeignKeys(t *testing.T) {
	store := testStore(t)
	query(t, store, "PRAGMA journal_mode", func(s sqliteh.Stmt) {
		if s.ColumnText(0) != "wal" {
			t.Fatal("WAL is disabled")
		}
	})
	for sql, want := range map[string]int64{"PRAGMA synchronous": 1, "PRAGMA foreign_keys": 1, "PRAGMA busy_timeout": 10000, "PRAGMA wal_autocheckpoint": 1000} {
		if got := count(t, store, sql); got != want {
			t.Fatalf("%s: got %d, want %d", sql, got, want)
		}
	}
	if err := execScript(store.db, "INSERT INTO posts (user_id, content) VALUES (999, 'orphan')"); err == nil {
		t.Fatal("foreign key violation succeeded")
	}
	query(t, store, "PRAGMA integrity_check", func(s sqliteh.Stmt) {
		if s.ColumnText(0) != "ok" {
			t.Fatal(s.ColumnText(0))
		}
	})
	must(t, store.close())
	if _, err := store.create(NewPost{Email: "closed@example.com", Content: "x"}); err == nil {
		t.Fatal("write to closed store succeeded")
	}
}

func TestCancellationDuringServerStartup(t *testing.T) {
	for i := range 50 {
		listener, err := net.Listen("tcp", "127.0.0.1:0")
		must(t, err)
		ctx, cancel := context.WithCancel(t.Context())
		var server errgroup.Group
		server.Go(func() error { return serve(ctx, newHTTPServer(func(*fasthttp.RequestCtx) {}), listener) })
		if i%2 == 0 {
			runtime.Gosched()
		}
		cancel()
		must(t, server.Wait())
	}
}

func TestStartupFailures(t *testing.T) {
	if _, err := openStore(filepath.Join(t.TempDir(), "missing.sqlite")); err == nil {
		t.Fatal("missing database was silently created")
	}
	path := testDatabase(t)
	ctx, cancel := context.WithCancel(t.Context())
	cancel()
	if err := run(ctx, "", path, -1); err == nil {
		t.Fatal("negative port accepted")
	}
	file := filepath.Join(t.TempDir(), "not-a-socket")
	must(t, os.WriteFile(file, []byte("preserve me"), 0600))
	if err := run(ctx, file, path, 0); err == nil {
		t.Fatal("existing file overwritten")
	}
	data, err := os.ReadFile(file)
	must(t, err)
	if string(data) != "preserve me" {
		t.Fatal("existing file modified")
	}
}
