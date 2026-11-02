// Command relay is the phase 0 spike's stand-in for Wizzrobe: it accepts one
// agent connection and serves a bare console page that sends it commands.
// It is a test tool, not product code; the real endpoint lands in phase 1.
package main

import (
	"context"
	"crypto/subtle"
	_ "embed"
	"encoding/json"
	"flag"
	"io"
	"log/slog"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/coder/websocket"
	"github.com/coder/websocket/wsjson"

	"github.com/tcprescott/wizzrobe/agent/obstest"
	"github.com/tcprescott/wizzrobe/agent/relay"
)

//go:embed console.html
var consoleHTML []byte

type hub struct {
	token string
	log   *slog.Logger

	mu       sync.Mutex
	agent    *websocket.Conn
	consoles map[*websocket.Conn]struct{}
	sent     map[string]time.Time
	obsState map[string]any // latest OBS status, replayed to a newly opened console
}

func (h *hub) authorized(r *http.Request) bool {
	got := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
	if got == "" {
		got = r.URL.Query().Get("key")
	}
	return subtle.ConstantTimeCompare([]byte(got), []byte(h.token)) == 1
}

func (h *hub) toConsoles(v any) {
	h.mu.Lock()
	list := make([]*websocket.Conn, 0, len(h.consoles))
	for c := range h.consoles {
		list = append(list, c)
	}
	h.mu.Unlock()
	for _, c := range list {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		_ = wsjson.Write(ctx, c, v)
		cancel()
	}
}

func (h *hub) serveAgent(w http.ResponseWriter, r *http.Request) {
	if !h.authorized(r) {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}
	c, err := websocket.Accept(w, r, nil)
	if err != nil {
		return
	}
	c.SetReadLimit(64 << 20)
	h.mu.Lock()
	if h.agent != nil {
		h.agent.Close(websocket.StatusPolicyViolation, "replaced by a newer agent connection")
	}
	h.agent = c
	h.mu.Unlock()
	h.log.Info("agent connected", "remote", r.RemoteAddr)
	connected := time.Now()

	defer func() {
		h.mu.Lock()
		if h.agent == c {
			h.agent = nil
		}
		h.mu.Unlock()
		h.log.Info("agent gone", "after", time.Since(connected).Round(time.Second))
		h.toConsoles(map[string]any{"type": "agent", "connected": false})
	}()
	h.toConsoles(map[string]any{"type": "agent", "connected": true})

	for {
		var m map[string]any
		if err := wsjson.Read(r.Context(), c, &m); err != nil {
			return
		}
		switch m["type"] {
		case "hello":
			h.mu.Lock()
			h.obsState = map[string]any{"type": "obs", "connected": m["obs_connected"], "obs_version": m["obs_version"]}
			h.mu.Unlock()
		case "obs":
			h.mu.Lock()
			h.obsState = m
			h.mu.Unlock()
		case "result":
			id, _ := m["id"].(string)
			h.mu.Lock()
			if t, ok := h.sent[id]; ok {
				m["rtt_ms"] = time.Since(t).Milliseconds()
				delete(h.sent, id)
			}
			h.mu.Unlock()
		}
		h.toConsoles(m)
	}
}

func (h *hub) serveConsole(w http.ResponseWriter, r *http.Request) {
	if !h.authorized(r) {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}
	c, err := websocket.Accept(w, r, nil)
	if err != nil {
		return
	}
	c.SetReadLimit(1 << 20)
	h.mu.Lock()
	h.consoles[c] = struct{}{}
	connected := h.agent != nil
	obsState := h.obsState
	h.mu.Unlock()
	defer func() {
		h.mu.Lock()
		delete(h.consoles, c)
		h.mu.Unlock()
		c.CloseNow()
	}()
	_ = wsjson.Write(r.Context(), c, map[string]any{"type": "agent", "connected": connected})
	if obsState != nil {
		_ = wsjson.Write(r.Context(), c, obsState)
	}

	for {
		var m struct {
			ID          string          `json:"id"`
			RequestType string          `json:"request_type"`
			Data        json.RawMessage `json:"data"`
		}
		if err := wsjson.Read(r.Context(), c, &m); err != nil {
			return
		}
		h.mu.Lock()
		agent := h.agent
		h.sent[m.ID] = time.Now()
		h.mu.Unlock()
		if agent == nil {
			_ = wsjson.Write(r.Context(), c, map[string]any{"type": "result", "id": m.ID, "ok": false, "comment": "no agent connected"})
			continue
		}
		ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
		_ = wsjson.Write(ctx, agent, map[string]any{"type": "command", "id": m.ID, "request_type": m.RequestType, "data": m.Data})
		cancel()
	}
}

func main() {
	listen := flag.String("listen", "127.0.0.1:8787", "address to listen on")
	demo := flag.Bool("demo", false, "also run a fake OBS and an in-process agent, to try the console without OBS")
	flag.Parse()
	token := os.Getenv("SPIKE_TOKEN")
	if token == "" {
		token = "spike"
	}
	log := slog.New(slog.NewTextHandler(os.Stderr, nil))
	h := &hub{token: token, log: log, consoles: map[*websocket.Conn]struct{}{}, sent: map[string]time.Time{}}

	mux := http.NewServeMux()
	mux.HandleFunc("/agent", h.serveAgent)
	mux.HandleFunc("/console/ws", h.serveConsole)
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/" {
			http.NotFound(w, r)
			return
		}
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.Header().Set("Cache-Control", "no-store")
		_, _ = w.Write(consoleHTML)
	})

	if *demo {
		fake := obstest.New("demo")
		obsSrv := fake.Server()
		defer obsSrv.Close()
		go func() {
			time.Sleep(300 * time.Millisecond)
			a := relay.New(relay.Config{
				ServerURL: "ws://" + *listen + "/agent", Token: token,
				OBSURL: obstest.WSURL(obsSrv), OBSPassword: "demo", AgentVersion: "demo",
				Logger: slog.New(slog.NewTextHandler(io.Discard, nil)),
			})
			_ = a.Run(context.Background())
		}()
	}

	log.Info("spike relay", "console", "http://"+*listen+"/?key="+token, "agent_url", "ws://"+*listen+"/agent")
	srv := &http.Server{Addr: *listen, Handler: mux, ReadHeaderTimeout: 10 * time.Second}
	if err := srv.ListenAndServe(); err != nil {
		log.Error("stopped", "err", err)
		os.Exit(1)
	}
}
