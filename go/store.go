package main

import (
	"errors"
	"fmt"
	"strings"
	"sync"

	"github.com/tailscale/sqlite/cgosqlite"
	"github.com/tailscale/sqlite/sqliteh"
	"golang.org/x/sync/errgroup"
)

// SQLite has one writer. Reuse request/reply objects and keep the connection on
// a dedicated writer goroutine instead of handing a contended mutex between
// every HTTP request. The lifecycle lock only excludes shutdown/maintenance.
type postStore struct {
	mu                                              sync.RWMutex
	db                                              sqliteh.DB
	begin, insertUser, insertPost, commit, rollback sqliteh.Stmt
	version                                         string
	failed                                          error
	requests                                        chan *writeRequest
	pool                                            sync.Pool
	writer                                          errgroup.Group
}

type writeRequest struct {
	body NewPost
	post Post
	err  error
	done chan struct{}
}

func openStore(path string) (_ *postStore, err error) {
	db, err := cgosqlite.Open(path, sqliteh.SQLITE_OPEN_READWRITE|sqliteh.SQLITE_OPEN_NOMUTEX|sqliteh.SQLITE_OPEN_URI, "")
	if err != nil {
		if db != nil {
			_ = db.Close()
		}
		return nil, fmt.Errorf("open database %q: %w", path, err)
	}
	s := &postStore{db: db}
	defer func() {
		if err != nil {
			err = errors.Join(err, s.close())
		}
	}()
	if err = execScript(db, `PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=10000;
PRAGMA optimize=0x10002;`); err != nil {
		return nil, err
	}
	version, _, err := db.Prepare("SELECT sqlite_version()", 0)
	if err != nil {
		return nil, err
	}
	row, stepErr := version.Step(nil)
	if row {
		s.version = version.ColumnText(0)
	}
	err = errors.Join(stepErr, version.Finalize())
	if err != nil {
		return nil, err
	}
	for _, item := range []struct {
		query  string
		target *sqliteh.Stmt
	}{
		{"BEGIN IMMEDIATE", &s.begin},
		{"INSERT OR IGNORE INTO users (email) VALUES (?)", &s.insertUser},
		{`INSERT INTO posts (content, user_id)
SELECT ?, id FROM users WHERE email IS ?
RETURNING id, user_id, content, created_at, updated_at`, &s.insertPost},
		{"COMMIT", &s.commit},
		{"ROLLBACK", &s.rollback},
	} {
		*item.target, _, err = db.Prepare(item.query, sqliteh.SQLITE_PREPARE_PERSISTENT)
		if err != nil {
			return nil, fmt.Errorf("prepare %q: %w", item.query, err)
		}
	}
	s.requests = make(chan *writeRequest, 128)
	s.pool.New = func() any { return &writeRequest{done: make(chan struct{}, 1)} }
	s.writer.Go(func() error {
		for request := range s.requests {
			request.post, request.err = s.createTransaction(request.body)
			request.done <- struct{}{}
		}
		return nil
	})
	return s, nil
}

// execScript is used only for setup/maintenance, never in the request hot path.
func execScript(db sqliteh.DB, script string) error {
	for strings.TrimSpace(script) != "" {
		stmt, rest, err := db.Prepare(script, 0)
		if err != nil {
			return fmt.Errorf("prepare SQL: %w (%s)", err, db.ErrMsg())
		}
		script = rest
		for {
			row, stepErr := stmt.Step(nil)
			if stepErr != nil {
				err = stepErr
				break
			}
			if !row {
				break
			}
		}
		if err = errors.Join(err, stmt.Finalize()); err != nil {
			return fmt.Errorf("execute SQL: %w (%s)", err, db.ErrMsg())
		}
	}
	return nil
}

func execute(stmt sqliteh.Stmt) error {
	_, _, _, _, err := stmt.StepResult() // step + reset + clear bindings in one C call
	return err
}

func (s *postStore) create(body NewPost) (post Post, err error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	if s.db == nil {
		return post, fmt.Errorf("database is closed")
	}
	request := s.pool.Get().(*writeRequest)
	request.body = body
	s.requests <- request
	<-request.done
	post, err = request.post, request.err
	request.body, request.post, request.err = NewPost{}, Post{}, nil
	s.pool.Put(request)
	return post, err
}

func (s *postStore) createTransaction(body NewPost) (post Post, err error) {
	if s.failed != nil {
		return post, s.failed
	}
	if err = execute(s.begin); err != nil {
		return post, fmt.Errorf("begin transaction: %w", err)
	}
	defer func() {
		if err != nil {
			// A failure must not leave a live RETURNING cursor or a transaction
			// that leaks a new user into the next request.
			_, userErr := s.insertUser.ResetAndClear()
			_, postErr := s.insertPost.ResetAndClear()
			rollbackErr := execute(s.rollback)
			err = errors.Join(err, userErr, postErr, rollbackErr)
			if rollbackErr != nil {
				s.failed = fmt.Errorf("database rollback failed: %w", err)
			}
		}
	}()
	if err = s.insertUser.BindText64(1, body.Email); err != nil {
		return post, err
	}
	if err = execute(s.insertUser); err != nil {
		return post, fmt.Errorf("insert user: %w", err)
	}
	if err = s.insertPost.BindText64(1, body.Content); err != nil {
		return post, err
	}
	if err = s.insertPost.BindText64(2, body.Email); err != nil {
		return post, err
	}
	row, err := s.insertPost.Step(nil)
	if err != nil {
		return post, fmt.Errorf("insert post: %w", err)
	}
	if !row {
		return post, fmt.Errorf("insert post returned no row")
	}
	post = Post{
		ID: s.insertPost.ColumnInt64(0), UserID: s.insertPost.ColumnInt64(1),
		Content: s.insertPost.ColumnText(2), CreatedAt: s.insertPost.ColumnInt64(3),
		UpdatedAt: s.insertPost.ColumnInt64(4),
	}
	row, err = s.insertPost.Step(nil)
	if err != nil {
		return post, fmt.Errorf("finish insert post: %w", err)
	}
	if row {
		return post, fmt.Errorf("insert post returned multiple rows")
	}
	if _, err = s.insertPost.ResetAndClear(); err != nil {
		return post, err
	}
	if err = execute(s.commit); err != nil {
		return post, fmt.Errorf("commit transaction: %w", err)
	}
	return post, nil
}

func (s *postStore) close() (err error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.db == nil {
		return nil
	}
	if s.requests != nil {
		close(s.requests)
		err = s.writer.Wait()
	}
	for _, stmt := range []sqliteh.Stmt{s.begin, s.insertUser, s.insertPost, s.commit, s.rollback} {
		if stmt != nil {
			err = errors.Join(err, stmt.Finalize())
		}
	}
	err = errors.Join(err, execScript(s.db, "PRAGMA optimize"), s.db.Close())
	s.db = nil
	return err
}
