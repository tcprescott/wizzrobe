package policy

import (
	"encoding/json"
	"testing"
)

func TestCheck(t *testing.T) {
	cases := []struct {
		name, req, data string
		ok              bool
	}{
		{"scene switch", "SetCurrentProgramScene", `{"sceneName":"Break"}`, true},
		{"not listed", "SetStreamServiceSettings", `{}`, false},
		{"vendor request", "CallVendorRequest", `{}`, false},
		{"screenshot to disk", "SaveSourceScreenshot", `{}`, false},
		{"profile change", "SetProfileParameter", `{}`, false},
		{"browser https", "CreateInput", `{"inputKind":"browser_source","inputSettings":{"url":"https://example.org/overlay"}}`, true},
		{"browser http", "CreateInput", `{"inputKind":"browser_source","inputSettings":{"url":"http://example.org"}}`, false},
		{"browser file url", "SetInputSettings", `{"inputName":"x","inputSettings":{"url":"file:///etc/passwd"}}`, false},
		{"browser local file", "SetInputSettings", `{"inputName":"x","inputSettings":{"is_local_file":true,"local_file":"C:/x.html"}}`, false},
		{"image source kind", "CreateInput", `{"inputKind":"image_source","inputSettings":{}}`, false},
		{"window capture kind", "CreateInput", `{"inputKind":"window_capture","inputSettings":{}}`, false},
		{"media srt", "CreateInput", `{"inputKind":"ffmpeg_source","inputSettings":{"is_local_file":false,"input":"srt://ingest.example.org:8890?streamid=read:x"}}`, true},
		{"media local", "SetInputSettings", `{"inputName":"x","inputSettings":{"is_local_file":true}}`, false},
		{"media file input", "SetInputSettings", `{"inputName":"x","inputSettings":{"input":"file:///Users/x/movie.mp4"}}`, false},
		{"file key", "SetInputSettings", `{"inputName":"x","inputSettings":{"file":"/tmp/a.png"}}`, false},
		{"text from file", "SetInputSettings", `{"inputName":"x","inputSettings":{"read_from_file":true}}`, false},
		{"crop", "SetSceneItemTransform", `{"sceneName":"s","sceneItemId":3,"sceneItemTransform":{"cropLeft":10}}`, true},
		{"screenshot ok", "GetSourceScreenshot", `{"sourceName":"Program","imageFormat":"jpg","imageWidth":640}`, true},
		{"screenshot huge", "GetSourceScreenshot", `{"sourceName":"Program","imageFormat":"jpg","imageWidth":8000}`, false},
		{"screenshot bmp", "GetSourceScreenshot", `{"sourceName":"Program","imageFormat":"bmp","imageWidth":640}`, false},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			err := Check(c.req, json.RawMessage(c.data))
			if (err == nil) != c.ok {
				t.Fatalf("Check(%s) err=%v, want ok=%v", c.req, err, c.ok)
			}
		})
	}
}
