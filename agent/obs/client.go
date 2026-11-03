// Package obs is a minimal obs-websocket v5 client: identify (with the
// challenge/salt auth), send requests, receive events.
package obs

import (
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"sync"
	"sync/atomic"

	"github.com/coder/websocket"
	"github.com/coder/websocket/wsjson"
)

const (
	opHello           = 0
	opIdentify        = 1
	opIdentified      = 2
	opEvent           = 5
	opRequest         = 6
	opRequestResponse = 7

	// SubscribeAll is every low-volume event category (General through Ui).
	SubscribeAll = 2047

	subprotocol = "obswebsocket.json"
	readLimit   = 64 << 20 // a full-size PNG screenshot can run to tens of MB
)

var ErrClosed = errors.New("obs connection closed")

type frame struct {
	Op int             `json:"op"`
	D  json.RawMessage `json:"d"`
}

type RequestStatus struct {
	Result  bool   `json:"result"`
	Code    int    `json:"code"`
	Comment string `json:"comment,omitempty"`
}

type Response struct {
	RequestType string          `json:"requestType"`
	RequestID   string          `json:"requestId"`
	Status      RequestStatus   `json:"requestStatus"`
	Data        json.RawMessage `json:"responseData,omitempty"`
}

type Event struct {
	Type   string          `json:"eventType"`
	Intent int             `json:"eventIntent"`
	Data   json.RawMessage `json:"eventData,omitempty"`
}

type Client struct {
	conn       *websocket.Conn
	OBSVersion string
	nextID     atomic.Uint64

	mu      sync.Mutex
	pending map[string]chan Response
	err     error

	events chan Event
	done   chan struct{}
}

// Auth is the obs-websocket v5 authentication string for a password,
// salt and challenge.
func Auth(password, salt, challenge string) string {
	s := sha256.Sum256([]byte(password + salt))
	secret := base64.StdEncoding.EncodeToString(s[:])
	a := sha256.Sum256([]byte(secret + challenge))
	return base64.StdEncoding.EncodeToString(a[:])
}

// Dial connects and identifies. The returned client's read loop runs until
// ctx ends, Close is called, or OBS goes away.
func Dial(ctx context.Context, url, password string, subscriptions int) (*Client, error) {
	conn, _, err := websocket.Dial(ctx, url, &websocket.DialOptions{Subprotocols: []string{subprotocol}})
	if err != nil {
		return nil, err
	}
	conn.SetReadLimit(readLimit)

	var hello frame
	if err := wsjson.Read(ctx, conn, &hello); err != nil {
		conn.Close(websocket.StatusProtocolError, "no hello")
		return nil, fmt.Errorf("reading hello: %w", err)
	}
	if hello.Op != opHello {
		conn.Close(websocket.StatusProtocolError, "expected hello")
		return nil, fmt.Errorf("expected hello, got op %d", hello.Op)
	}
	var h struct {
		Version        string `json:"obsWebSocketVersion"`
		RPCVersion     int    `json:"rpcVersion"`
		Authentication *struct {
			Challenge string `json:"challenge"`
			Salt      string `json:"salt"`
		} `json:"authentication"`
	}
	if err := json.Unmarshal(hello.D, &h); err != nil {
		conn.Close(websocket.StatusProtocolError, "bad hello")
		return nil, err
	}

	identify := map[string]any{"rpcVersion": 1, "eventSubscriptions": subscriptions}
	if h.Authentication != nil {
		if password == "" {
			conn.Close(websocket.StatusNormalClosure, "")
			return nil, errors.New("OBS asks for a password and none is set")
		}
		identify["authentication"] = Auth(password, h.Authentication.Salt, h.Authentication.Challenge)
	}
	if err := writeFrame(ctx, conn, opIdentify, identify); err != nil {
		conn.Close(websocket.StatusInternalError, "")
		return nil, err
	}
	var ident frame
	if err := wsjson.Read(ctx, conn, &ident); err != nil {
		// OBS closes with 4009 on a wrong password.
		if websocket.CloseStatus(err) == 4009 {
			return nil, errors.New("OBS refused the password")
		}
		return nil, fmt.Errorf("waiting for identified: %w", err)
	}
	if ident.Op != opIdentified {
		conn.Close(websocket.StatusProtocolError, "expected identified")
		return nil, fmt.Errorf("expected identified, got op %d", ident.Op)
	}

	c := &Client{
		conn:       conn,
		OBSVersion: h.Version,
		pending:    map[string]chan Response{},
		events:     make(chan Event, 256),
		done:       make(chan struct{}),
	}
	go c.readLoop(context.WithoutCancel(ctx))
	return c, nil
}

func writeFrame(ctx context.Context, conn *websocket.Conn, op int, d any) error {
	raw, err := json.Marshal(d)
	if err != nil {
		return err
	}
	return wsjson.Write(ctx, conn, frame{Op: op, D: raw})
}

func (c *Client) readLoop(ctx context.Context) {
	defer close(c.done)
	defer close(c.events)
	for {
		var f frame
		if err := wsjson.Read(ctx, c.conn, &f); err != nil {
			c.fail(err)
			return
		}
		switch f.Op {
		case opRequestResponse:
			var r Response
			if json.Unmarshal(f.D, &r) != nil {
				continue
			}
			c.mu.Lock()
			ch := c.pending[r.RequestID]
			delete(c.pending, r.RequestID)
			c.mu.Unlock()
			if ch != nil {
				ch <- r
			}
		case opEvent:
			var e Event
			if json.Unmarshal(f.D, &e) != nil {
				continue
			}
			select {
			case c.events <- e:
			default: // a slow consumer loses events rather than stalling requests
			}
		}
	}
}

func (c *Client) fail(err error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.err == nil {
		c.err = err
	}
	for id, ch := range c.pending {
		close(ch)
		delete(c.pending, id)
	}
}

// Request sends one request and waits for its response.
func (c *Client) Request(ctx context.Context, requestType string, data json.RawMessage) (Response, error) {
	id := strconv.FormatUint(c.nextID.Add(1), 10)
	ch := make(chan Response, 1)
	c.mu.Lock()
	if c.err != nil {
		c.mu.Unlock()
		return Response{}, ErrClosed
	}
	c.pending[id] = ch
	c.mu.Unlock()

	body := map[string]any{"requestType": requestType, "requestId": id}
	if len(data) > 0 && string(data) != "null" {
		body["requestData"] = data
	}
	if err := writeFrame(ctx, c.conn, opRequest, body); err != nil {
		c.mu.Lock()
		delete(c.pending, id)
		c.mu.Unlock()
		return Response{}, err
	}
	select {
	case r, ok := <-ch:
		if !ok {
			return Response{}, ErrClosed
		}
		return r, nil
	case <-ctx.Done():
		c.mu.Lock()
		delete(c.pending, id)
		c.mu.Unlock()
		return Response{}, ctx.Err()
	}
}

func (c *Client) Events() <-chan Event  { return c.events }
func (c *Client) Done() <-chan struct{} { return c.done }
func (c *Client) Close() error          { return c.conn.Close(websocket.StatusNormalClosure, "") }
