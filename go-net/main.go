package main

import (
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"github.com/eatonphil/gosqlite"
	"github.com/pb33f/libopenapi"
	validator "github.com/pb33f/libopenapi-validator"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"reflect"
	"syscall"
)

type NewPost struct {
	Email   string `json:"email"`
	Content string `json:"content"`
}

type Post struct {
	ID        int64  `json:"id"`
	UserID    int64  `json:"user_id"`
	Content   string `json:"content"`
	CreatedAt int64  `json:"created_at"`
	UpdatedAt int64  `json:"updated_at"`
}

type PostResult struct {
	ok  *Post
	err *error
}

func closeOrPanic(c interface{ Close() error }) {
	log.Println("Closing", reflect.TypeOf(c))

	err := c.Close()
	if err != nil {
		log.Panic(err)
	}
}

func dbWriter(requests chan NewPost, results chan PostResult) {
	defer close(results)

	conn, err := gosqlite.Open("../db/db.sqlite")
	if err != nil {
		log.Panic(err)
	}
	defer func() {
		defer closeOrPanic(conn)

		err := conn.Exec("PRAGMA optimize")
		if err != nil {
			log.Panic(err)
		}
	}()

	err = conn.Exec(`
	PRAGMA journal_mode = wal;
	PRAGMA synchronous = normal;
	PRAGMA foreign_keys = on;
	PRAGMA busy_timeout = 10000;
	PRAGMA optimize=0x10002;
	`)
	if err != nil {
		panic(err)
	}

	insertUser, err := conn.Prepare("INSERT OR IGNORE INTO users (email) VALUES (?)")
	if err != nil {
		panic(err)
	}
	defer closeOrPanic(insertUser)

	insertPost, err := conn.Prepare(`
	INSERT INTO posts   (content,   user_id)
	SELECT              ?,          id  
	FROM        users
	WHERE       email = ?
	RETURNING   id, user_id, content, created_at, updated_at
	`)
	if err != nil {
		panic(err)
	}
	defer closeOrPanic(insertPost)

	for req := range requests {
		res := Post{}
		err = conn.WithTxImmediate(func() error {
			err := insertUser.Exec(req.Email)
			if err != nil {
				return err
			}

			err = insertPost.Bind(req.Content, req.Email)
			if err != nil {
				return err
			}
			ok, err := insertPost.Step()
			if err != nil {
				return err
			}
			if !ok {
				return fmt.Errorf("no rows inserted %+v", req)
			}
			err = insertPost.Scan(&res.ID, &res.UserID, &res.Content, &res.CreatedAt, &res.UpdatedAt)
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
			results <- PostResult{ok: nil, err: &err}
		} else {
			results <- PostResult{ok: &res, err: nil}
		}
	}
}

func respondJSON(w http.ResponseWriter, status int, body interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)

	// write body json
	err := json.NewEncoder(w).Encode(body)
	if err != nil {
		log.Panic(err)
	}
}

func main() {
	var socketFile string
	flag.StringVar(&socketFile, "socket", "/tmp/benchmark.sock", "Unix domain socket path. Default: /tmp/benchmark.sock")
	flag.Parse()

	requests := make(chan NewPost)
	//defer close(requests) // closes dbWriter

	results := make(chan PostResult) // closed by dbWriter since it's the sender

	go dbWriter(requests, results)

	openApiJSON, err := os.ReadFile("../openapi/http.json")
	if err != nil {
		log.Panic(err)
	}

	document, err := libopenapi.NewDocument(openApiJSON)
	if err != nil {
		log.Panic(err)
	}

	v, errs := validator.NewValidator(document)
	if len(errs) > 0 {
		log.Panic(errs)
	}

	mux := http.NewServeMux()
	mux.HandleFunc("POST /posts", func(w http.ResponseWriter, r *http.Request) {
		_, errs := v.ValidateHttpRequest(r)
		if len(errs) > 0 {
			respondJSON(w, http.StatusBadRequest, errs)
			return
		}
		var body NewPost
		err := json.NewDecoder(r.Body).Decode(&body)
		if err != nil {
			respondJSON(w, http.StatusBadRequest, "Could not read body")
			return
		}

		requests <- body
		result, ok := <-results
		if !ok {
			respondJSON(w, http.StatusInternalServerError, "Internal error")
			return
		}

		if result.err != nil {
			respondJSON(w, http.StatusInternalServerError, result.err)
		} else {
			respondJSON(w, http.StatusOK, result.ok)
		}
	})

	unixListener, err := net.Listen("unix", socketFile)
	if err != nil {
		log.Panic(err)
	}

	server := http.Server{
		Handler: mux,
	}

	sigs := make(chan os.Signal, 1)
	defer close(sigs)
	signal.Notify(sigs, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		sig := <-sigs
		log.Println(sig)

		close(requests) // closes dbWriter

		err := server.Close()
		if err != nil {
			log.Panic(err)
		}
	}()

	log.Println(socketFile)

	err = server.Serve(unixListener)
	if err != nil && !errors.Is(err, http.ErrServerClosed) {
		log.Panic(err)
	}
}
