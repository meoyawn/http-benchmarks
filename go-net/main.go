package main

import (
	"flag"
	"fmt"
	"log"
	"net"
	"net/mail"
	"os"
	"os/signal"
	"reflect"
	"syscall"

	"github.com/eatonphil/gosqlite"
	"github.com/gofiber/fiber/v2"
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
	log.Println("Closing", reflect.TypeOf(c))

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

	app := fiber.New(fiber.Config{
		Prefork:   port > 0,
		Immutable: true,
	})

	app.Post("/posts", func(ctx *fiber.Ctx) error {
		body := NewPost{}
		err := ctx.BodyParser(&body)
		if err != nil {
			return err
		}
		errs := validate(body)
		if len(errs) > 0 {
			err := ctx.Status(fiber.StatusBadRequest).JSON(errs)
			if err != nil {
				return err
			}
			return nil
		}

		result := make(chan PostResult, 1)
		defer close(result)

		requests <- DbReq{body: body, result: result}
		res, ok := <-result
		if !ok {
			return fiber.NewError(fiber.StatusInternalServerError, "Server is closing")
		}

		if res.err != nil {
			return *res.err
		} else {
			err := ctx.Status(fiber.StatusCreated).JSON(res.ok)
			if err != nil {
				return err
			}
			return nil
		}
	})

	sigs := make(chan os.Signal, 1)
	defer close(sigs)
	signal.Notify(sigs, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		sig := <-sigs
		log.Println(sig)

		close(requests) // closes dbWriter

		err := app.Shutdown()
		if err != nil {
			log.Panic(err)
		}
	}()

	if port > 0 {
		err := app.Listen(fmt.Sprintf(":%d", port))
		if err != nil {
			log.Panic(err)
		}
	} else {
		unixListener, err := net.Listen("unix", socketFile)
		if err != nil {
			log.Panic(err)
		}

		err = app.Listener(unixListener)
		if err != nil {
			log.Panic(err)
		}
	}
}
