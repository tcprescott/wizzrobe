package obs_test

import (
	"context"
	"encoding/json"
	"testing"
	"time"

	"github.com/tcprescott/wizzrobe/agent/obs"
	"github.com/tcprescott/wizzrobe/agent/obstest"
)

func TestAuthMatchesProtocolExample(t *testing.T) {
	// Worked example from the obs-websocket v5 protocol docs.
	got := obs.Auth("supersecretpassword", "lM1GncleQOaCu9lT1yeUZhFYnqhsLLP1G5lAGo3ixaI=", "+IxH4CnCiqpX1rM9scsNynZzbOe4KhDeYcTNS3PDaeY=")
	if got != "1Ct943GAT+6YQUUX47Ia/ncufilbe6+oD6lY+5kaCu4=" {
		t.Fatalf("auth = %s", got)
	}
}

func TestDialRequestAndEvent(t *testing.T) {
	fake := obstest.New("pw")
	srv := fake.Server()
	defer srv.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	c, err := obs.Dial(ctx, obstest.WSURL(srv), "pw", obs.SubscribeAll)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	r, err := c.Request(ctx, "SetCurrentProgramScene", json.RawMessage(`{"sceneName":"Break"}`))
	if err != nil || !r.Status.Result {
		t.Fatalf("set scene: %v %+v", err, r)
	}
	select {
	case e := <-c.Events():
		if e.Type != "CurrentProgramSceneChanged" {
			t.Fatalf("event %s", e.Type)
		}
	case <-ctx.Done():
		t.Fatal("no event")
	}

	r, err = c.Request(ctx, "SetCurrentProgramScene", json.RawMessage(`{"sceneName":"Nope"}`))
	if err != nil || r.Status.Result || r.Status.Code != 600 {
		t.Fatalf("missing scene should fail with 600: %v %+v", err, r)
	}
}

func TestWrongPassword(t *testing.T) {
	srv := obstest.New("pw").Server()
	defer srv.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if _, err := obs.Dial(ctx, obstest.WSURL(srv), "wrong", obs.SubscribeAll); err == nil {
		t.Fatal("expected refusal")
	}
	if _, err := obs.Dial(ctx, obstest.WSURL(srv), "", obs.SubscribeAll); err == nil {
		t.Fatal("expected an error with no password")
	}
}

func TestRequestAfterOBSQuits(t *testing.T) {
	fake := obstest.New("")
	srv := fake.Server()
	defer srv.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	c, err := obs.Dial(ctx, obstest.WSURL(srv), "", obs.SubscribeAll)
	if err != nil {
		t.Fatal(err)
	}
	fake.DropAll()
	<-c.Done()
	if _, err := c.Request(ctx, "GetVersion", nil); err == nil {
		t.Fatal("expected an error once OBS is gone")
	}
}
