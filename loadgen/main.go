// A small Vegeta driver: randomized in-memory targets, bounded concurrency,
// independent attackers, strict statuses, and merged latency histograms.
package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"runtime"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/HdrHistogram/hdrhistogram-go"
	vegeta "github.com/tsenart/vegeta/v12/lib"
	"golang.org/x/sync/errgroup"
)

const version = "vegeta/v12.13.0 randomized-v1"

type sample struct {
	hist     *hdrhistogram.Histogram
	statuses map[uint16]uint64
	errors   map[string]uint64
	count    uint64
}

// SplitMix64 permutes a counter without a shared random-generator lock.
func mix(x uint64) uint64 {
	x += 0x9e3779b97f4a7c15
	x = (x ^ (x >> 30)) * 0xbf58476d1ce4e5b9
	x = (x ^ (x >> 27)) * 0x94d049bb133111eb
	return x ^ (x >> 31)
}

func cpuSeconds() float64 {
	var r syscall.Rusage
	if err := syscall.Getrusage(syscall.RUSAGE_SELF, &r); err != nil {
		panic(err)
	}
	return float64(r.Utime.Sec+r.Stime.Sec) + float64(r.Utime.Usec+r.Stime.Usec)/1e6
}

// Separate single-processor runtimes avoid cross-core Go scheduler/GC contention.
// Aggregate histogram counts, never averages of per-process percentiles or rates.
func multiprocess(processes, connections, cpus int, seed uint64) error {
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	group, ctx := errgroup.WithContext(ctx)
	reports := make([]map[string]json.RawMessage, processes)
	for i := range processes {
		group.Go(func() error {
			n, p := connections/processes, cpus/processes
			if i < connections%processes {
				n++
			}
			if i < cpus%processes {
				p++
			}
			args := append(append([]string{}, os.Args[1:]...), "-processes=1", "-shards=1",
				fmt.Sprintf("-connections=%d", n), fmt.Sprintf("-cpus=%d", p),
				fmt.Sprintf("-seed=%d", seed+uint64(i)*0x9e3779b97f4a7c15))
			command := exec.CommandContext(ctx, os.Args[0], args...)
			var stdout, stderr bytes.Buffer
			command.Stdout, command.Stderr = &stdout, &stderr
			if err := command.Run(); err != nil {
				return fmt.Errorf("Vegeta process %d: %w: %s", i, err, stderr.String())
			}
			return json.Unmarshal(stdout.Bytes(), &reports[i])
		})
	}
	if err := group.Wait(); err != nil {
		return err
	}
	hist := hdrhistogram.New(1, 60_000_000, 3)
	statuses := map[string]uint64{}
	var count uint64
	var cpu float64
	var started, ended int64
	for _, report := range reports {
		var row struct {
			Requests  uint64                `json:"requests"`
			CPU       float64               `json:"client_cpu_seconds"`
			Start     int64                 `json:"started_unix_ns"`
			End       int64                 `json:"ended_unix_ns"`
			Statuses  map[string]uint64     `json:"status_codes"`
			Histogram hdrhistogram.Snapshot `json:"histogram"`
		}
		data, err := json.Marshal(report)
		if err != nil {
			return err
		}
		if err := json.Unmarshal(data, &row); err != nil {
			return err
		}
		count += row.Requests
		cpu += row.CPU
		if started == 0 || row.Start < started {
			started = row.Start
		}
		ended = max(ended, row.End)
		for code, n := range row.Statuses {
			statuses[code] += n
		}
		if dropped := hist.Merge(hdrhistogram.Import(&row.Histogram)); dropped != 0 {
			return fmt.Errorf("merged histogram dropped %d values", dropped)
		}
	}
	wall := float64(ended-started) / 1e9
	merged := map[string]any{}
	for key, value := range reports[0] {
		merged[key] = value
	}
	merged["requests"], merged["requests_per_second"] = count, float64(count)/wall
	merged["p50_seconds"], merged["p95_seconds"], merged["p99_seconds"] = float64(hist.ValueAtQuantile(50))/1e6, float64(hist.ValueAtQuantile(95))/1e6, float64(hist.ValueAtQuantile(99))/1e6
	merged["status_codes"], merged["histogram"] = statuses, hist.Export()
	merged["wall_seconds"], merged["client_cpu_seconds"], merged["client_cpu_percent"] = wall, cpu, cpu/wall*100
	merged["started_unix_ns"], merged["ended_unix_ns"] = started, ended
	merged["processes"], merged["connections"], merged["cpus"], merged["shards"], merged["seed"] = processes, connections, cpus, processes, seed
	// Preserve per-process counts, timestamps, and CPU without redundant histograms.
	for _, report := range reports {
		delete(report, "histogram")
	}
	merged["children"] = reports
	return json.NewEncoder(os.Stdout).Encode(merged)
}

func run() error {
	path := flag.String("corpus", "", "JSONL request bodies")
	socket := flag.String("unix-socket", "/tmp/benchmark.sock", "Unix socket")
	url := flag.String("url", "http://localhost/posts", "HTTP target URL (also supports TCP without -unix-socket)")
	status := flag.Int("status", 201, "required response status")
	duration := flag.Duration("duration", 10*time.Second, "time issuing requests; outstanding requests drain")
	connections := flag.Int("connections", 50, "total concurrent requests")
	shards := flag.Int("shards", 5, "independent Vegeta attackers")
	cpus := flag.Int("cpus", 4, "load-generator GOMAXPROCS")
	processes := flag.Int("processes", 1, "independent load-generator processes sharing the total connections and CPUs")
	seed := flag.Uint64("seed", 20260918, "reproducible target-selection seed")
	showVersion := flag.Bool("version", false, "print version")
	flag.Parse()
	if *showVersion {
		fmt.Println(version)
		return nil
	}
	if *connections < 1 || *shards < 1 || *shards > *connections || *cpus < 1 || *duration <= 0 || *status < 100 || *status > 599 {
		return fmt.Errorf("invalid settings: connections=%d shards=%d cpus=%d duration=%s status=%d", *connections, *shards, *cpus, *duration, *status)
	}
	if *processes < 1 || *processes > min(*connections, *cpus) {
		return fmt.Errorf("processes=%d must be between 1 and min(connections=%d, cpus=%d)", *processes, *connections, *cpus)
	}
	if *processes > 1 {
		return multiprocess(*processes, *connections, *cpus, *seed)
	}
	runtime.GOMAXPROCS(*cpus)
	f, err := os.Open(*path)
	if err != nil {
		return fmt.Errorf("open corpus %q: %w", *path, err)
	}
	defer f.Close()
	var bodies [][]byte
	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		body := append([]byte(nil), scanner.Bytes()...)
		if !json.Valid(body) {
			return fmt.Errorf("corpus %q line %d is invalid JSON", *path, len(bodies)+1)
		}
		bodies = append(bodies, body)
	}
	if err := scanner.Err(); err != nil {
		return err
	}
	if len(bodies) == 0 {
		return fmt.Errorf("corpus %q is empty", *path)
	}
	results := make([]sample, *shards)
	attackers := make([]*vegeta.Attacker, *shards)
	for i := range attackers {
		n := *connections / *shards
		if i < *connections%*shards {
			n++
		}
		opts := []func(*vegeta.Attacker){vegeta.Workers(uint64(n)), vegeta.MaxWorkers(uint64(n)), vegeta.Connections(n), vegeta.MaxConnections(n), vegeta.HTTP2(false), vegeta.Proxy(nil), vegeta.Timeout(10 * time.Second), vegeta.MaxBody(0), vegeta.Redirects(vegeta.NoFollow)}
		if *socket != "" {
			opts = append(opts, vegeta.UnixSocket(*socket))
		}
		attackers[i] = vegeta.NewAttacker(opts...)
		results[i] = sample{hist: hdrhistogram.New(1, 60_000_000, 3), statuses: map[uint16]uint64{}, errors: map[string]uint64{}}
	}
	header := http.Header{"Content-Type": {"application/json"}}
	var group errgroup.Group
	startCPU := cpuSeconds()
	start := time.Now()
	for i, attacker := range attackers {
		group.Go(func() error {
			var sequence atomic.Uint64
			targeter := func(t *vegeta.Target) error {
				index := mix(*seed+uint64(i)*0x9e3779b97f4a7c15+sequence.Add(1)) % uint64(len(bodies))
				*t = vegeta.Target{Method: "POST", URL: *url, Header: header, Body: bodies[index]}
				return nil
			}
			s := &results[i]
			for r := range attacker.Attack(targeter, vegeta.Rate{Freq: 0}, *duration, "random-json") {
				s.count++
				s.statuses[r.Code]++
				if r.Error != "" {
					s.errors[r.Error]++
				}
				if err := s.hist.RecordValue(max(1, r.Latency.Microseconds())); err != nil {
					s.errors[err.Error()]++
				}
			}
			return nil
		})
	}
	if err := group.Wait(); err != nil {
		return err
	}
	end := time.Now()
	wall := end.Sub(start).Seconds()
	cpu := cpuSeconds() - startCPU
	hist := hdrhistogram.New(1, 60_000_000, 3)
	statuses := map[uint16]uint64{}
	errors := map[string]uint64{}
	var count uint64
	for _, s := range results {
		count += s.count
		if dropped := hist.Merge(s.hist); dropped != 0 {
			return fmt.Errorf("histogram dropped %d values", dropped)
		}
		for k, v := range s.statuses {
			statuses[k] += v
		}
		for k, v := range s.errors {
			errors[k] += v
		}
	}
	report := map[string]any{
		"load_generator": version, "requests": count, "requests_per_second": float64(count) / wall,
		"p50_seconds": float64(hist.ValueAtQuantile(50)) / 1e6, "p95_seconds": float64(hist.ValueAtQuantile(95)) / 1e6,
		"p99_seconds": float64(hist.ValueAtQuantile(99)) / 1e6, "status_codes": statuses, "errors": errors,
		"wall_seconds": wall, "client_cpu_seconds": cpu, "client_cpu_percent": cpu / wall * 100,
		"duration": duration.String(), "connections": *connections, "shards": *shards, "cpus": *cpus,
		"seed": *seed, "corpus_entries": len(bodies), "histogram": hist.Export(),
		"started_unix_ns": start.UnixNano(), "ended_unix_ns": end.UnixNano(), "processes": 1,
	}
	if err := json.NewEncoder(os.Stdout).Encode(report); err != nil {
		return err
	}
	if count == 0 || len(errors) > 0 || len(statuses) != 1 || statuses[uint16(*status)] != count {
		return fmt.Errorf("expected only HTTP %d: requests=%d statuses=%v errors=%v", *status, count, statuses, errors)
	}
	return nil
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
