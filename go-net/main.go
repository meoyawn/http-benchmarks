package main

import (
	"context"
	"encoding/json"
	"flag"
	"net"
	"net/http"
	"os"
	"os/signal"
	"syscall"

	"crawshaw.io/sqlite/sqlitex"
	"github.com/pb33f/libopenapi"
	openapiValidator "github.com/pb33f/libopenapi-validator"
)

var dbpool *sqlitex.Pool
var validator openapiValidator.Validator = newValidator()

type NewPost struct {
	Content string `json:"content"`
	Email   string `json:"email"`
}

// Helper function to handle error responses
func respondJSON(w http.ResponseWriter, code int, payload interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(payload)
}

type Post struct {
	ID        int64  `json:"id"`
	UserID    int64  `json:"user_id"`
	Content   string `json:"content"`
	CreatedAt int64  `json:"created_at"`
	UpdatedAt int64  `json:"updated_at"`
}

func handeNewPost(w http.ResponseWriter, req *http.Request) {
	_, errors := validator.ValidateHttpRequest(req)
	if len(errors) > 0 {
		respondJSON(w, http.StatusBadRequest, errors)
		return
	}

	var body NewPost
	err := json.NewDecoder(req.Body).Decode(&body)
	if err != nil {
		respondJSON(w, http.StatusBadRequest, err)
		return
	}

	writeConn := dbpool.Get(context.Background())

	insertUser, err := writeConn.Prepare(`
	INSERT OR IGNORE INTO users (email) VALUES (?);
	`)
	if err != nil {
		respondJSON(w, http.StatusBadRequest, err)
		return
	}
	insertPost, err := writeConn.Prepare(`
	INSERT INTO posts   (content,   user_id)
	SELECT              ?,          id
	FROM        users
	WHERE       email = ?
	RETURNING   id, user_id, content, created_at, updated_at
	`)
	if err != nil {
		respondJSON(w, http.StatusBadRequest, err)
		return
	}

	sqlitex.Exec(writeConn, "BEGIN IMMEDIATE TRANSACTION", nil)
	insertUser.BindText(1, body.Email)
	insertUser.Step()

	insertPost.BindText(1, body.Content)
	insertPost.BindText(2, body.Email)
	insertPost.Step()

	var post Post = Post{
		ID:        insertPost.ColumnInt64(1),
		UserID:    insertPost.ColumnInt64(2),
		Content:   insertPost.ColumnText(3),
		CreatedAt: insertPost.ColumnInt64(4),
		UpdatedAt: insertPost.ColumnInt64(5),
	}
	sqlitex.Exec(writeConn, "COMMIT", nil)

	respondJSON(w, http.StatusCreated, post)
}

func newValidator() openapiValidator.Validator {
	openApiJSON, err := os.ReadFile("../openapi/http.json")
	if err != nil {
		panic(err)
	}

	document, err := libopenapi.NewDocument(openApiJSON)
	if err != nil {
		panic(err)
	}

	v, errors := openapiValidator.NewValidator(document)
	if len(errors) > 0 {
		panic(errors)
	}
	return v
}

func main() {

	var socketFile string
	flag.StringVar(&socketFile, "socket", "/tmp/benchmark.sock", "Unix domain socket path. Default: /tmp/benchmark.sock")
	flag.Parse()

	validator = newValidator()

	mux := http.NewServeMux()
	mux.HandleFunc("POST /posts", handeNewPost)

	server := &http.Server{
		Handler: mux,
	}

	unixListener, err := net.Listen("unix", socketFile)
	if err != nil {
		panic(err)
	}

	dbpool, err = sqlitex.Open("file:../db/db.sqlite", 0, 10)
	if err != nil {
		panic(err)
	}

	sigs := make(chan os.Signal, 1)
	signal.Notify(sigs, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		<-sigs

		err := server.Shutdown(context.TODO())
		if err != nil {
			panic(err)
		}

		err = dbpool.Close()
		if err != nil {
			panic(err)
		}
	}()

	server.Serve(unixListener)
}
