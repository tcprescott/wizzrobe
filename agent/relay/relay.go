// Package relay keeps the agent's two connections up: out to Wizzrobe and to
// the local OBS. Wizzrobe sends commands; the agent checks each against the
// policy, runs it on OBS and sends the result back, and forwards OBS events.
//
// Wire format (JSON text frames, both directions):
//
//	agent → server  {"type":"hello","agent_version":..,"obs_connected":..,"obs_version":..}
//	agent → server  {"type":"obs","connected":bool,"obs_version":..,"error":..}
//	server → agent  {"type":"command","id":"..","request_type":"GetSceneList","data":{..}}
//	agent → server  {"type":"result","id":"..","ok":bool,"code":int,"comment":"..","data":{..}}
//	agent → server  {"type":"event","event_type":"..","data":{..}}
package relay

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"
	"sync"
	"time"

	"github.com/coder/websocket"
	"github.com/coder/websocket/wsjson"

	"github.com/tcprescott/wizzrobe/agent/obs"
	"github.com/tcprescott/wizzrobe/agent/policy"
)

// Result codes the agent itself produces; OBS's own codes (100+) pass through.
const (
	CodeRefused    = 1
	CodeOBSOffline = 2
	CodeTimeout    = 3
)

var forwardedEvents = map[string]bool{
	"CurrentProgramSceneChanged": true,
	"CurrentPreviewSceneChanged": true,
	"SceneListChanged":           true,
	"StreamStateChanged":         true,
	"RecordStateChanged":         true,
	"InputMuteStateChanged":      true,
	"InputVolumeChanged":         true,
	"InputCreated":               true,
	"InputRemoved":               true,
	"SceneItemTransformChanged":  false, // fires on every drag in OBS; too chatty to relay
	"ExitStarted":                true,
}

type Config struct {
	ServerURL    string
	Token        string
	OBSURL       string
	OBSPassword  string
	AgentVersion string
	// CommandTimeout bounds one OBS request. Zero means 15s.
	CommandTimeout time.Duration
	// MaxBackoff caps the reconnect wait. Zero means 30s.
	MaxBackoff time.Duration
	Logger     *slog.Logger
}

type Agent struct {
	cfg Config
	log *slog.Logger

	mu       sync.Mutex
	obs      *obs.Client
	obsErr   string
	sessions map[*session]struct{}
}

func New(cfg Config) *Agent {
	if cfg.CommandTimeout == 0 {
		cfg.CommandTimeout = 15 * time.Second
	}
	if cfg.MaxBackoff == 0 {
		cfg.MaxBackoff = 30 * time.Second
	}
	if cfg.Logger == nil {
		cfg.Logger = slog.Default()
	}
	return &Agent{cfg: cfg, log: cfg.Logger, sessions: map[*session]struct{}{}}
}

// Run keeps both connections up until ctx ends.
func (a *Agent) Run(ctx context.Context) error {
	go a.keepOBS(ctx)
	backoff := time.Second
	for ctx.Err() == nil {
		started := time.Now()
		err := a.serve(ctx)
		if ctx.Err() != nil {
			break
		}
		if time.Since(started) > time.Minute {
			backoff = time.Second
		}
		a.log.Warn("wizzrobe connection ended, reconnecting", "err", err, "in", backoff)
		if !sleep(ctx, backoff) {
			break
		}
		backoff = min(backoff*2, a.cfg.MaxBackoff)
	}
	return ctx.Err()
}

func sleep(ctx context.Context, d time.Duration) bool {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-t.C:
		return true
	}
}

func (a *Agent) keepOBS(ctx context.Context) {
	backoff := time.Second
	for ctx.Err() == nil {
		dctx, cancel := context.WithTimeout(ctx, 10*time.Second)
		c, err := obs.Dial(dctx, a.cfg.OBSURL, a.cfg.OBSPassword, obs.SubscribeAll)
		cancel()
		if err != nil {
			a.setOBS(nil, err.Error())
			a.log.Warn("OBS not reachable", "err", err, "retry_in", backoff)
			if !sleep(ctx, backoff) {
				return
			}
			backoff = min(backoff*2, a.cfg.MaxBackoff)
			continue
		}
		backoff = time.Second
		a.log.Info("connected to OBS", "obs_websocket", c.OBSVersion)
		a.setOBS(c, "")
		for e := range c.Events() {
			if forwardedEvents[e.Type] {
				a.broadcast(outEvent{Type: "event", EventType: e.Type, Data: e.Data})
			}
		}
		a.setOBS(nil, "OBS closed the connection")
		a.log.Warn("lost OBS")
		select {
		case <-ctx.Done():
			c.Close()
			return
		default:
		}
	}
}

type outOBS struct {
	Type       string `json:"type"`
	Connected  bool   `json:"connected"`
	OBSVersion string `json:"obs_version,omitempty"`
	Error      string `json:"error,omitempty"`
}

type outHello struct {
	Type         string `json:"type"`
	AgentVersion string `json:"agent_version"`
	OBSConnected bool   `json:"obs_connected"`
	OBSVersion   string `json:"obs_version,omitempty"`
}

type outEvent struct {
	Type      string          `json:"type"`
	EventType string          `json:"event_type"`
	Data      json.RawMessage `json:"data,omitempty"`
}

type outResult struct {
	Type    string          `json:"type"`
	ID      string          `json:"id"`
	OK      bool            `json:"ok"`
	Code    int             `json:"code"`
	Comment string          `json:"comment,omitempty"`
	Data    json.RawMessage `json:"data,omitempty"`
}

type inMessage struct {
	Type        string          `json:"type"`
	ID          string          `json:"id"`
	RequestType string          `json:"request_type"`
	Data        json.RawMessage `json:"data"`
}

func (a *Agent) setOBS(c *obs.Client, errText string) {
	a.mu.Lock()
	a.obs, a.obsErr = c, errText
	a.mu.Unlock()
	msg := outOBS{Type: "obs", Connected: c != nil, Error: errText}
	if c != nil {
		msg.OBSVersion = c.OBSVersion
	}
	a.broadcast(msg)
}

func (a *Agent) currentOBS() *obs.Client {
	a.mu.Lock()
	defer a.mu.Unlock()
	return a.obs
}

type session struct {
	conn *websocket.Conn
	ctx  context.Context
}

func (s *session) send(v any) {
	ctx, cancel := context.WithTimeout(s.ctx, 10*time.Second)
	defer cancel()
	_ = wsjson.Write(ctx, s.conn, v)
}

func (a *Agent) broadcast(v any) {
	a.mu.Lock()
	list := make([]*session, 0, len(a.sessions))
	for s := range a.sessions {
		list = append(list, s)
	}
	a.mu.Unlock()
	for _, s := range list {
		s.send(v)
	}
}

func (a *Agent) serve(ctx context.Context) error {
	dctx, cancel := context.WithTimeout(ctx, 15*time.Second)
	conn, _, err := websocket.Dial(dctx, a.cfg.ServerURL, &websocket.DialOptions{
		HTTPHeader: http.Header{"Authorization": {"Bearer " + a.cfg.Token}},
	})
	cancel()
	if err != nil {
		return err
	}
	defer conn.CloseNow()
	conn.SetReadLimit(1 << 20)

	sctx, stop := context.WithCancel(ctx)
	defer stop()
	s := &session{conn: conn, ctx: sctx}

	hello := outHello{Type: "hello", AgentVersion: a.cfg.AgentVersion}
	if c := a.currentOBS(); c != nil {
		hello.OBSConnected, hello.OBSVersion = true, c.OBSVersion
	}
	s.send(hello)
	a.log.Info("connected to Wizzrobe", "url", a.cfg.ServerURL)

	a.mu.Lock()
	a.sessions[s] = struct{}{}
	a.mu.Unlock()
	defer func() {
		a.mu.Lock()
		delete(a.sessions, s)
		a.mu.Unlock()
	}()

	go keepAlive(sctx, conn)

	for {
		var m inMessage
		if err := wsjson.Read(sctx, conn, &m); err != nil {
			return err
		}
		if m.Type != "command" || m.ID == "" {
			continue
		}
		go a.handle(s, m)
	}
}

// keepAlive pings so idle proxies (and Cloud Run) see traffic and a dead
// link is noticed within a minute.
func keepAlive(ctx context.Context, conn *websocket.Conn) {
	t := time.NewTicker(20 * time.Second)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			pctx, cancel := context.WithTimeout(ctx, 20*time.Second)
			err := conn.Ping(pctx)
			cancel()
			if err != nil {
				conn.CloseNow()
				return
			}
		}
	}
}

func (a *Agent) handle(s *session, m inMessage) {
	res := outResult{Type: "result", ID: m.ID}
	if err := policy.Check(m.RequestType, m.Data); err != nil {
		res.Code, res.Comment = CodeRefused, err.Error()
		a.log.Warn("refused command", "request_type", m.RequestType, "reason", err)
		s.send(res)
		return
	}
	c := a.currentOBS()
	if c == nil {
		res.Code, res.Comment = CodeOBSOffline, "OBS is not connected"
		s.send(res)
		return
	}
	ctx, cancel := context.WithTimeout(s.ctx, a.cfg.CommandTimeout)
	defer cancel()
	r, err := c.Request(ctx, m.RequestType, m.Data)
	switch {
	case errors.Is(err, context.DeadlineExceeded):
		res.Code, res.Comment = CodeTimeout, "OBS didn't answer in time"
	case err != nil:
		res.Code, res.Comment = CodeOBSOffline, err.Error()
	default:
		res.OK, res.Code, res.Comment, res.Data = r.Status.Result, r.Status.Code, r.Status.Comment, r.Data
	}
	a.log.Info("ran command", "request_type", m.RequestType, "ok", res.OK, "code", res.Code)
	s.send(res)
}
