package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"net/mail"
	"os"
	"os/signal"
	"syscall"

	"github.com/eatonphil/gosqlite"
	"github.com/fasthttp/router"
	"github.com/valyala/fasthttp"
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

type DbReq struct {
	body   NewPost
	result chan PostResult
}

func closeOrPanic(c interface{ Close() error }) {
	log.Printf("Closing %+v", c)

	err := c.Close()
	if err != nil {
		log.Panic(err)
	}
}

func dbWriter(requests chan DbReq) {

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
			body := req.body
			err := insertUser.Exec(body.Email)
			if err != nil {
				return err
			}

			err = insertPost.Bind(body.Content, body.Email)
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
			req.result <- PostResult{ok: nil, err: &err}
		} else {
			req.result <- PostResult{ok: &res, err: nil}
		}
	}
}

func validate(p NewPost) []string {
	var errs []string
	_, err := mail.ParseAddress(p.Email)
	if err != nil {
		errs = append(errs, fmt.Sprintf("email: %s: %v", p.Email, err))
	}
	if len(p.Content) < 1 {
		errs = append(errs, "content: must be at least 1 character")
	}
	return errs
}

func main() {
	var socketFile string
	var port int
	flag.StringVar(&socketFile, "socket", "/tmp/benchmark.sock", "Unix domain socket")
	flag.IntVar(&port, "port", 0, "HTTP port")
	flag.Parse()

	requests := make(chan DbReq, 100) // closed below before server.Close()

	go dbWriter(requests)

	router := router.New()
	router.POST("/posts", func(ctx *fasthttp.RequestCtx) {
		body := NewPost{}
		err := json.Unmarshal(ctx.PostBody(), &body)
		if err != nil {
			ctx.Error("POST body", fasthttp.StatusBadRequest)
			return
		}

		ctx.Response.Header.Set("Content-Type", "application/json")

		errs := validate(body)
		if len(errs) > 0 {
			ctx.Response.SetStatusCode(fasthttp.StatusBadRequest)
			err := json.NewEncoder(ctx).Encode(errs)
			if err != nil {
				ctx.Error("JSON", fasthttp.StatusInternalServerError)
			}
			return
		}

		result := make(chan PostResult, 1)
		defer close(result)

		requests <- DbReq{body: body, result: result}
		res := <-result

		if res.err != nil {
			ctx.Error((*res.err).Error(), fasthttp.StatusInternalServerError)
		} else {
			ctx.Response.SetStatusCode(fasthttp.StatusCreated)
			err := json.NewEncoder(ctx).Encode(res.ok)
			if err != nil {
				ctx.Error("JSON", fasthttp.StatusInternalServerError)
			}
		}
	})

	router.POST("/echo", func(ctx *fasthttp.RequestCtx) {
		body := NewPost{}
		err := json.Unmarshal(ctx.PostBody(), &body)
		if err != nil {
			ctx.Error("POST body", fasthttp.StatusBadRequest)
			return
		}

		ctx.Response.Header.Set("Content-Type", "application/json")
		err = json.NewEncoder(ctx).Encode(body)
		if err != nil {
			ctx.Error("JSON", fasthttp.StatusInternalServerError)
		}
	})

	server := fasthttp.Server{
		Handler:         router.Handler,
		CloseOnShutdown: true,
	}

	sigs := make(chan os.Signal, 1)
	defer close(sigs)
	signal.Notify(sigs, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		sig := <-sigs
		log.Println(sig)

		close(requests) // closes dbWriter

		err := server.Shutdown()
		if err != nil {
			log.Panic(err)
		}
	}()

	if port > 0 {
		err := server.ListenAndServe(fmt.Sprintf(":%d", port))
		if err != nil {
			log.Panic(err)
		}
	} else {
		log.Println("Listening on", socketFile)

		err := server.ListenAndServeUNIX(socketFile, os.FileMode(0666))
		if err != nil {
			log.Panic(err)
		}
	}
}
