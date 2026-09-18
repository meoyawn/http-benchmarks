package main

import (
	"context"
	json "encoding/json/v2"
	"errors"
	"flag"
	"fmt"
	"log"
	"net"
	"os"
	"os/signal"
	"regexp"
	"runtime"
	"syscall"
	"time"

	"github.com/valyala/fasthttp"
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

var emailPattern = regexp.MustCompile(`^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$`)

func validate(p NewPost) []string {
	var errs []string
	if !emailPattern.MatchString(p.Email) {
		errs = append(errs, fmt.Sprintf("email: invalid address %q", p.Email))
	}
	if p.Content == "" {
		errs = append(errs, "content: must be at least 1 character")
	}
	return errs
}

func writeJSON(ctx *fasthttp.RequestCtx, status int, value any) {
	data, err := json.Marshal(value)
	if err != nil {
		writeError(ctx, fasthttp.StatusInternalServerError, "encode JSON")
		return
	}
	ctx.SetStatusCode(status)
	ctx.SetContentType("application/json")
	ctx.Response.SetBodyRaw(data)
}

func newHandler(store *postStore) fasthttp.RequestHandler {
	return func(ctx *fasthttp.RequestCtx) {
		path := string(ctx.Path())
		if path != "/echo" && path != "/posts" {
			writeError(ctx, fasthttp.StatusNotFound, "not found")
			return
		}
		if !ctx.IsPost() {
			writeError(ctx, fasthttp.StatusMethodNotAllowed, "method must be POST")
			ctx.Response.Header.Set("Allow", "POST")
			return
		}
		var body NewPost
		if err := json.Unmarshal(ctx.Request.Body(), &body); err != nil {
			writeError(ctx, fasthttp.StatusBadRequest, "invalid JSON body")
			return
		}
		if path == "/echo" {
			writeJSON(ctx, fasthttp.StatusOK, &body)
			return
		}
		if errs := validate(body); len(errs) > 0 {
			writeJSON(ctx, fasthttp.StatusBadRequest, errs)
			return
		}
		post, err := store.create(body)
		if err != nil {
			log.Printf("create post: %v", err)
			writeError(ctx, fasthttp.StatusInternalServerError, "database error")
			return
		}
		writeJSON(ctx, fasthttp.StatusCreated, &post)
	}
}

func writeError(ctx *fasthttp.RequestCtx, status int, message string) {
	ctx.SetStatusCode(status)
	ctx.SetContentType("text/plain; charset=utf-8")
	ctx.SetBodyString(message)
}

func newHTTPServer(handler fasthttp.RequestHandler) *fasthttp.Server {
	return &fasthttp.Server{Handler: handler, ReadTimeout: 10 * time.Second, IdleTimeout: 10 * time.Second}
}

func serve(ctx context.Context, h *fasthttp.Server, listener net.Listener) error {
	defer listener.Close()
	if ctx.Err() != nil {
		return nil
	}
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	var group errgroup.Group
	group.Go(func() error { defer cancel(); return h.Serve(listener) })
	group.Go(func() error {
		<-ctx.Done()
		// Shutdown can run before Serve registers its listener. Closing the
		// owned listener first also stops that startup race, then drains handlers.
		_ = listener.Close()
		if err := h.Shutdown(); err != nil && !errors.Is(err, net.ErrClosed) {
			return err
		}
		return nil
	})
	return group.Wait()
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
	network, address := "unix", socketFile
	if port > 0 {
		network, address = "tcp", fmt.Sprintf(":%d", port)
	}
	listener, err := net.Listen(network, address)
	if err != nil {
		return err
	}
	log.Printf("Listening on %s (SQLite %s)", listener.Addr(), store.version)
	return serve(ctx, newHTTPServer(newHandler(store)), listener)
}

func main() {
	// Two processors maximize this workload's serialized SQLite writes.
	// An explicit GOMAXPROCS (four for echo) retains the runtime's usual control.
	if os.Getenv("GOMAXPROCS") == "" {
		runtime.GOMAXPROCS(2)
	}
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
