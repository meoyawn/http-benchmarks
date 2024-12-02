package main

import (
	"errors"
	"github.com/eatonphil/gosqlite"
	"testing"
)

func testClose(t *testing.T, c interface{ Close() error }) {
	err := c.Close()
	if err != nil {
		t.Fatal(err)
	}
}

func TestGoSQLite(t *testing.T) {
	conn, err := gosqlite.Open("../db/db.sqlite")
	if err != nil {
		t.Fatal(err)
	}
	defer testClose(t, conn)

	err = conn.Exec(`
	PRAGMA journal_mode = wal;
	PRAGMA synchronous = normal;
	PRAGMA foreign_keys = on;
	PRAGMA busy_timeout = 10000;
	`)
	if err != nil {
		t.Fatal(err)
	}

	insertUser, err := conn.Prepare("INSERT OR IGNORE INTO users (email) VALUES (?)")
	if err != nil {
		t.Fatal(err)
	}
	defer testClose(t, insertUser)

	insertPost, err := conn.Prepare(`
	INSERT INTO posts   (content,   user_id)
	SELECT              ?,          id  
	FROM        users
	WHERE       email = ?
	RETURNING   id, user_id, content, created_at, updated_at
	`)
	if err != nil {
		t.Fatal(err)
	}
	defer testClose(t, insertPost)

	email := "foo@gmail.com"
	content := "content"
	post := Post{}
	err = conn.WithTxImmediate(func() error {
		err := insertUser.Exec(email)
		if err != nil {
			return err
		}
		err = insertPost.Bind(content, email)
		if err != nil {
			return err
		}
		ok, err := insertPost.Step()
		if err != nil {
			return err
		}
		if !ok {
			return errors.New("no rows")
		}
		err = insertPost.Scan(&post.ID, &post.UserID, &post.Content, &post.CreatedAt, &post.UpdatedAt)
		if err != nil {
			return err
		}
		err = insertPost.Reset()
		if err != nil {
			return err
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if post.Content != content {
		t.Fatal(post)
	}
}
