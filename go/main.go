package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log"
	"net/mail"
	"os"
	"os/signal"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/cloudwego/hertz/pkg/app"
	"github.com/cloudwego/hertz/pkg/app/server"
	"github.com/cloudwego/hertz/pkg/common/config"
	"github.com/cloudwego/hertz/pkg/protocol/consts"
	json "github.com/goccy/go-json"
	"golang.org/x/sync/errgroup"
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

func validate(p NewPost) []string {
	var errs []string
	address, err := mail.ParseAddress(p.Email)
	if err != nil || address.Address != p.Email {
		errs = append(errs, fmt.Sprintf("email: invalid address %q", p.Email))
	}
	if p.Content == "" {
		errs = append(errs, "content: must be at least 1 character")
	}
	return errs
}

func writeJSON(ctx *app.RequestContext, status int, value any) {
	data, err := json.Marshal(value)
	if err != nil {
		writeError(ctx, consts.StatusInternalServerError, "encode JSON")
		return
	}
	ctx.SetStatusCode(status)
	ctx.SetContentType("application/json")
	ctx.Response.SetBodyRaw(data)
}

func newHandler(store *postStore) app.HandlerFunc {
	return func(_ context.Context, ctx *app.RequestContext) {
		path := string(ctx.Path())
		if !ctx.IsPost() {
			writeError(ctx, consts.StatusMethodNotAllowed, "method must be POST")
			ctx.Response.Header.Set("Allow", "POST")
			return
		}
		var body NewPost
		if err := json.Unmarshal(ctx.Request.Body(), &body); err != nil {
			writeError(ctx, consts.StatusBadRequest, "invalid JSON body")
			return
		}
		if path == "/echo" {
			writeJSON(ctx, consts.StatusOK, &body)
			return
		}
		if errs := validate(body); len(errs) > 0 {
			writeJSON(ctx, consts.StatusBadRequest, errs)
			return
		}
		post, err := store.create(body)
		if err != nil {
			log.Printf("create post: %v", err)
			writeError(ctx, consts.StatusInternalServerError, "database error")
			return
		}
		writeJSON(ctx, consts.StatusCreated, &post)
	}
}

func writeError(ctx *app.RequestContext, status int, message string) {
	ctx.SetStatusCode(status)
	ctx.SetContentType("text/plain; charset=utf-8")
	ctx.SetBodyString(message)
}

func newHTTPServer(socketFile string, port int, handler app.HandlerFunc) *server.Hertz {
	options := []config.Option{server.WithReadTimeout(10 * time.Second), server.WithIdleTimeout(10 * time.Second)}
	if port > 0 {
		options = append(options, server.WithHostPorts(fmt.Sprintf(":%d", port)))
	} else {
		options = append(options, server.WithNetwork("unix"), server.WithHostPorts(socketFile))
	}
	h := server.New(options...)
	h.Any("/echo", handler)
	h.Any("/posts", handler)
	return h
}

func serve(ctx context.Context, h *server.Hertz) error {
	if ctx.Err() != nil {
		return nil
	}
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	var finished atomic.Bool
	var group errgroup.Group
	group.Go(func() error {
		defer func() { finished.Store(true); cancel() }()
		return h.Run()
	})
	group.Go(func() error {
		<-ctx.Done()
		// Cancellation can arrive during startup; do not call Shutdown before
		// Hertz has initialized its listener, or wait after Run has failed.
		for !h.IsRunning() {
			if finished.Load() {
				return nil
			}
			time.Sleep(time.Millisecond)
		}
		return h.Shutdown(context.Background())
	})
	return group.Wait() // Complete active handlers before the caller closes SQLite.
}

func run(ctx context.Context, socketFile, database string, port int) (err error) {
	if port < 0 || port > 65535 {
		return fmt.Errorf("port must be between 0 and 65535, got %d", port)
	}
	if port == 0 && socketFile == "" {
		return fmt.Errorf("socket path must not be empty, got %q", socketFile)
	}
	store, err := openStore(database)
	if err != nil {
		return err
	}
	defer func() { err = errors.Join(err, store.close()) }()
	if port == 0 {
		if _, err := os.Lstat(socketFile); err == nil {
			return fmt.Errorf("socket path %q already exists", socketFile)
		} else if !errors.Is(err, os.ErrNotExist) {
			return err
		}
	}
	h := newHTTPServer(socketFile, port, newHandler(store))
	log.Printf("Listening on %s (SQLite %s)", h.GetOptions().Addr, store.version)
	return serve(ctx, h)
}

func main() {
	socketFile := flag.String("socket", "/tmp/benchmark.sock", "Unix domain socket")
	database := flag.String("db", "../db/db.sqlite", "SQLite database (apply the shared migration first)")
	port := flag.Int("port", 0, "HTTP TCP port; 0 uses the Unix socket")
	flag.Parse()
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if err := run(ctx, *socketFile, *database, *port); err != nil {
		log.Fatal(err)
	}
}
