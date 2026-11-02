package relay

import (
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/coder/websocket"
	"github.com/coder/websocket/wsjson"

	"github.com/tcprescott/wizzrobe/agent/obstest"
)

// server is a stand-in for Wizzrobe's agent endpoint: each accepted agent
// connection arrives on conns.
type server struct {
	conns chan *websocket.Conn
	srv   *httptest.Server
}

func newServer(t *testing.T, token string) *server {
	s := &server{conns: make(chan *websocket.Conn, 4)}
	s.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer "+token {
			http.Error(w, "no", http.StatusUnauthorized)
			return
		}
		c, err := websocket.Accept(w, r, nil)
		if err != nil {
			return
		}
		c.SetReadLimit(1 << 20)
		s.conns <- c
		<-r.Context().Done()
	}))
	t.Cleanup(s.srv.Close)
	return s
}

func read(t *testing.T, c *websocket.Conn, wantType string) map[string]any {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	for {
		var m map[string]any
		if err := wsjson.Read(ctx, c, &m); err != nil {
			t.Fatalf("waiting for %s: %v", wantType, err)
		}
		if m["type"] == wantType {
			return m
		}
	}
}

func command(t *testing.T, c *websocket.Conn, id, req, data string) map[string]any {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	msg := map[string]any{"type": "command", "id": id, "request_type": req, "data": json.RawMessage(data)}
	if err := wsjson.Write(ctx, c, msg); err != nil {
		t.Fatal(err)
	}
	for {
		m := read(t, c, "result")
		if m["id"] == id {
			return m
		}
	}
}

func accept(t *testing.T, s *server) *websocket.Conn {
	t.Helper()
	select {
	case c := <-s.conns:
		return c
	case <-time.After(5 * time.Second):
		t.Fatal("agent never connected")
		return nil
	}
}

func startAgent(t *testing.T, s *server, obsURL string) {
	a := New(Config{
		ServerURL:   "ws" + strings.TrimPrefix(s.srv.URL, "http"),
		Token:       "tok",
		OBSURL:      obsURL,
		OBSPassword: "pw",
		MaxBackoff:  200 * time.Millisecond,
		Logger:      slog.New(slog.NewTextHandler(io.Discard, nil)),
	})
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	go a.Run(ctx)
}

func waitOBS(t *testing.T, c *websocket.Conn, connected bool) {
	t.Helper()
	for {
		m := read(t, c, "obs")
		if m["connected"] == connected {
			return
		}
	}
}

func TestCommandsRunAndPolicyRefuses(t *testing.T) {
	fake := obstest.New("pw")
	obsSrv := fake.Server()
	defer obsSrv.Close()
	s := newServer(t, "tok")
	startAgent(t, s, obstest.WSURL(obsSrv))

	c := accept(t, s)
	hello := read(t, c, "hello")
	if hello["obs_connected"] != true {
		waitOBS(t, c, true)
	}

	r := command(t, c, "1", "GetSceneList", `{}`)
	if r["ok"] != true {
		t.Fatalf("GetSceneList: %v", r)
	}
	r = command(t, c, "2", "SetCurrentProgramScene", `{"sceneName":"Match 2P"}`)
	if r["ok"] != true {
		t.Fatalf("switch: %v", r)
	}
	ev := read(t, c, "event")
	if ev["event_type"] != "CurrentProgramSceneChanged" {
		t.Fatalf("event: %v", ev)
	}

	r = command(t, c, "3", "SetStreamServiceSettings", `{"streamServiceSettings":{"key":"x"}}`)
	if r["ok"] != false || r["code"] != float64(CodeRefused) {
		t.Fatalf("expected refusal: %v", r)
	}
	for _, seen := range fake.Seen() {
		if seen == "SetStreamServiceSettings" {
			t.Fatal("a refused request reached OBS")
		}
	}
}

func TestReconnectsToServerAndOBS(t *testing.T) {
	fake := obstest.New("pw")
	obsSrv := fake.Server()
	defer obsSrv.Close()
	s := newServer(t, "tok")
	startAgent(t, s, obstest.WSURL(obsSrv))

	c := accept(t, s)
	read(t, c, "hello")
	c.Close(websocket.StatusGoingAway, "deploy")

	c2 := accept(t, s)
	read(t, c2, "hello")

	fake.DropAll()
	waitOBS(t, c2, false)
	waitOBS(t, c2, true)
	if r := command(t, c2, "1", "GetVersion", `{}`); r["ok"] != true {
		t.Fatalf("after OBS restart: %v", r)
	}
}

func TestOBSOfflineAnswersInsteadOfHanging(t *testing.T) {
	s := newServer(t, "tok")
	startAgent(t, s, "ws://127.0.0.1:1")
	c := accept(t, s)
	read(t, c, "hello")
	r := command(t, c, "1", "GetVersion", `{}`)
	if r["code"] != float64(CodeOBSOffline) {
		t.Fatalf("expected OBS offline: %v", r)
	}
}
