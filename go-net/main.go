package main

import (
	"flag"
	"fmt"
	"net"
	"net/http"
	"os"
	"os/signal"
	"syscall"
)

func handler(w http.ResponseWriter, r *http.Request) {
	fmt.Fprintf(w, "Hi there, I love %s!", r.URL.Path[1:])
}

func main() {

	var socketFile string
	flag.StringVar(&socketFile, "socket", "/tmp/benchmark.sock", "Unix domain socket path. Default: /tmp/benchmark.sock")
	flag.Parse()

	srv := &http.Server{}
	unixListener, err := net.Listen("unix", socketFile)
	if err != nil {
		panic(err)
	}

	sigs := make(chan os.Signal, 1)
	signal.Notify(sigs, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		<-sigs
		srv.Shutdown(nil)
	}()

	srv.Serve(unixListener)
}
