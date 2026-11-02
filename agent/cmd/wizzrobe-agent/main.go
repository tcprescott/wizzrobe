// Command wizzrobe-agent runs on a broadcaster's machine. It connects out to
// Wizzrobe and lets Wizzrobe drive the local OBS through obs-websocket,
// within the policy in package policy. Quit it (Ctrl+C) to stop Wizzrobe
// driving OBS at once.
package main

import (
	"context"
	"flag"
	"fmt"
	"log/slog"
	"os"
	"os/signal"
	"sort"
	"strings"
	"syscall"

	"github.com/tcprescott/wizzrobe/agent/policy"
	"github.com/tcprescott/wizzrobe/agent/relay"
)

var version = "0.0.0-spike"

func main() {
	server := flag.String("server", env("WIZZROBE_AGENT_SERVER", ""), "Wizzrobe agent URL (wss://…/agent)")
	token := flag.String("token", env("WIZZROBE_AGENT_TOKEN", ""), "pairing token")
	obsURL := flag.String("obs", env("OBS_WEBSOCKET_URL", "ws://127.0.0.1:4455"), "obs-websocket URL")
	showVersion := flag.Bool("version", false, "print the version and exit")
	flag.Parse()

	if *showVersion {
		fmt.Println(version)
		return
	}
	if *server == "" || *token == "" {
		fmt.Fprintln(os.Stderr, "wizzrobe-agent: -server and -token are required (or WIZZROBE_AGENT_SERVER / WIZZROBE_AGENT_TOKEN)")
		os.Exit(2)
	}

	log := slog.New(slog.NewTextHandler(os.Stderr, nil))
	allowed := policy.Allowed()
	sort.Strings(allowed)
	log.Info("starting", "version", version, "obs", *obsURL, "allowed", strings.Join(allowed, ","))

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	a := relay.New(relay.Config{
		ServerURL:    *server,
		Token:        *token,
		OBSURL:       *obsURL,
		OBSPassword:  os.Getenv("OBS_WEBSOCKET_PASSWORD"),
		AgentVersion: version,
		Logger:       log,
	})
	_ = a.Run(ctx)
	log.Info("stopped")
}

func env(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
