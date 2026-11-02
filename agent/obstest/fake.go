// Package obstest is a fake obs-websocket v5 server for tests and for trying
// the spike console without OBS installed.
package obstest

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"

	"github.com/coder/websocket"
	"github.com/coder/websocket/wsjson"

	"github.com/tcprescott/wizzrobe/agent/obs"
)

// A 1x1 grey JPEG, enough for a preview tile to render.
const tinyJPEG = "data:image/jpg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q=="

type Fake struct {
	Password string

	mu       sync.Mutex
	Requests []string
	scenes   []string
	program  string
	conns    map[*websocket.Conn]struct{}
}

func New(password string) *Fake {
	return &Fake{
		Password: password,
		scenes:   []string{"Starting soon", "Match 2P", "Break"},
		program:  "Starting soon",
		conns:    map[*websocket.Conn]struct{}{},
	}
}

// Server starts the fake on a test server; its URL is ws://….
func (f *Fake) Server() *httptest.Server {
	return httptest.NewServer(http.HandlerFunc(f.ServeHTTP))
}

func WSURL(s *httptest.Server) string { return "ws" + strings.TrimPrefix(s.URL, "http") }

// DropAll closes every client connection, as if OBS quit.
func (f *Fake) DropAll() {
	f.mu.Lock()
	defer f.mu.Unlock()
	for c := range f.conns {
		c.Close(websocket.StatusGoingAway, "bye")
	}
}

func (f *Fake) Seen() []string {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]string(nil), f.Requests...)
}

type frame struct {
	Op int             `json:"op"`
	D  json.RawMessage `json:"d"`
}

func send(ctx context.Context, c *websocket.Conn, op int, d any) error {
	raw, _ := json.Marshal(d)
	return wsjson.Write(ctx, c, frame{Op: op, D: raw})
}

func (f *Fake) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	c, err := websocket.Accept(w, r, &websocket.AcceptOptions{Subprotocols: []string{"obswebsocket.json"}})
	if err != nil {
		return
	}
	ctx := r.Context()
	f.mu.Lock()
	f.conns[c] = struct{}{}
	f.mu.Unlock()
	defer func() {
		f.mu.Lock()
		delete(f.conns, c)
		f.mu.Unlock()
		c.CloseNow()
	}()

	const salt, challenge = "c2FsdA==", "Y2hhbGxlbmdl"
	hello := map[string]any{"obsWebSocketVersion": "5.5.0", "rpcVersion": 1}
	if f.Password != "" {
		hello["authentication"] = map[string]string{"salt": salt, "challenge": challenge}
	}
	if send(ctx, c, 0, hello) != nil {
		return
	}
	var id frame
	if wsjson.Read(ctx, c, &id) != nil || id.Op != 1 {
		return
	}
	if f.Password != "" {
		var d struct {
			Auth string `json:"authentication"`
		}
		_ = json.Unmarshal(id.D, &d)
		if d.Auth != obs.Auth(f.Password, salt, challenge) {
			c.Close(4009, "Authentication failed.")
			return
		}
	}
	if send(ctx, c, 2, map[string]any{"negotiatedRpcVersion": 1}) != nil {
		return
	}

	for {
		var fr frame
		if wsjson.Read(ctx, c, &fr) != nil {
			return
		}
		if fr.Op != 6 {
			continue
		}
		var req struct {
			Type string          `json:"requestType"`
			ID   string          `json:"requestId"`
			Data json.RawMessage `json:"requestData"`
		}
		_ = json.Unmarshal(fr.D, &req)
		f.mu.Lock()
		f.Requests = append(f.Requests, req.Type)
		f.mu.Unlock()

		status := map[string]any{"result": true, "code": 100}
		var data any
		var event map[string]any
		switch req.Type {
		case "GetVersion":
			data = map[string]any{"obsVersion": "31.0.0", "obsWebSocketVersion": "5.5.0"}
		case "GetSceneList":
			f.mu.Lock()
			list := []map[string]any{}
			for i, s := range f.scenes {
				list = append(list, map[string]any{"sceneName": s, "sceneIndex": i})
			}
			data = map[string]any{"currentProgramSceneName": f.program, "scenes": list}
			f.mu.Unlock()
		case "SetCurrentProgramScene":
			var d struct {
				Name string `json:"sceneName"`
			}
			_ = json.Unmarshal(req.Data, &d)
			f.mu.Lock()
			found := false
			for _, s := range f.scenes {
				found = found || s == d.Name
			}
			if found {
				f.program = d.Name
				event = map[string]any{"eventType": "CurrentProgramSceneChanged", "eventIntent": 4, "eventData": map[string]any{"sceneName": d.Name}}
			} else {
				status = map[string]any{"result": false, "code": 600, "comment": "No source was found by the name of `" + d.Name + "`."}
			}
			f.mu.Unlock()
		case "GetSourceScreenshot":
			data = map[string]any{"imageData": tinyJPEG}
		case "GetSceneItemList":
			data = map[string]any{"sceneItems": []map[string]any{
				{"sceneItemId": 1, "sourceName": "Runner 1 feed", "sceneItemTransform": map[string]any{"cropLeft": 0, "cropRight": 0, "cropTop": 0, "cropBottom": 0}},
				{"sceneItemId": 2, "sourceName": "Runner 2 feed", "sceneItemTransform": map[string]any{"cropLeft": 12, "cropRight": 12, "cropTop": 40, "cropBottom": 8}},
				{"sceneItemId": 3, "sourceName": "Overlay", "sceneItemTransform": map[string]any{}},
			}}
		case "GetInputList":
			data = map[string]any{"inputs": []map[string]any{
				{"inputName": "Runner 1 feed", "inputKind": "ffmpeg_source"},
				{"inputName": "Runner 2 feed", "inputKind": "ffmpeg_source"},
				{"inputName": "Commentary", "inputKind": "wasapi_input_capture"},
			}}
		case "GetInputVolume":
			data = map[string]any{"inputVolumeDb": -6.0, "inputVolumeMul": 0.5}
		case "GetInputMute":
			data = map[string]any{"inputMuted": false}
		case "GetStats":
			data = map[string]any{"cpuUsage": 12.5, "activeFps": 60, "outputSkippedFrames": 0, "outputTotalFrames": 1000}
		}
		resp := map[string]any{"requestType": req.Type, "requestId": req.ID, "requestStatus": status}
		if data != nil {
			resp["responseData"] = data
		}
		if send(ctx, c, 7, resp) != nil {
			return
		}
		if event != nil {
			_ = send(ctx, c, 5, event)
		}
	}
}
