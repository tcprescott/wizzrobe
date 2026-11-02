// Package policy decides which obs-websocket requests Wizzrobe may send to a
// broadcaster's OBS. Anything not listed is refused, so a compromised
// Wizzrobe account can drive OBS but can't read the broadcaster's disk,
// change their stream key, or run vendor/plugin requests.
package policy

import (
	"encoding/json"
	"fmt"
	"net/url"
	"strings"
)

var allowedRequests = map[string]bool{
	"GetVersion":             true,
	"GetStats":               true,
	"GetStreamStatus":        true,
	"GetRecordStatus":        true,
	"GetSceneList":           true,
	"GetCurrentProgramScene": true,
	"SetCurrentProgramScene": true,
	"GetCurrentPreviewScene": true,
	"SetCurrentPreviewScene": true,
	"CreateScene":            true,
	"GetInputList":           true,
	"GetInputKindList":       true,
	"GetInputSettings":       true,
	"CreateInput":            true,
	"SetInputSettings":       true,
	"GetSceneItemList":       true,
	"GetSceneItemId":         true,
	"CreateSceneItem":        true,
	"GetSceneItemTransform":  true,
	"SetSceneItemTransform":  true,
	"SetSceneItemEnabled":    true,
	"SetSceneItemIndex":      true,
	"GetSourceScreenshot":    true,
	"GetInputVolume":         true,
	"SetInputVolume":         true,
	"GetInputMute":           true,
	"SetInputMute":           true,
	"StartRecord":            true,
	"StopRecord":             true,
	"StartStream":            true,
	"StopStream":             true,
}

// Input kinds Wizzrobe may create. Text sources differ per OS.
var allowedKinds = map[string]bool{
	"browser_source":     true,
	"color_source_v3":    true,
	"ffmpeg_source":      true,
	"text_gdiplus_v3":    true,
	"text_gdiplus_v2":    true,
	"text_ft2_source_v2": true,
}

// Settings keys that point OBS at the local disk.
var bannedKeys = map[string]bool{
	"local_file":     true,
	"file":           true,
	"files":          true,
	"read_from_file": true,
	"from_file":      true,
}

var allowedMediaSchemes = map[string]bool{"https": true, "srt": true, "rtmp": true, "rtmps": true, "rist": true}

const maxScreenshotWidth = 1920

// Allowed lists the request types, for the agent's startup log and tests.
func Allowed() []string {
	out := make([]string, 0, len(allowedRequests))
	for k := range allowedRequests {
		out = append(out, k)
	}
	return out
}

// Check returns an error when Wizzrobe must not send this request.
func Check(requestType string, data json.RawMessage) error {
	if !allowedRequests[requestType] {
		return fmt.Errorf("%s is not on the agent's allowlist", requestType)
	}
	switch requestType {
	case "CreateInput":
		var d struct {
			Kind     string         `json:"inputKind"`
			Settings map[string]any `json:"inputSettings"`
		}
		if err := json.Unmarshal(data, &d); err != nil {
			return fmt.Errorf("bad CreateInput data: %w", err)
		}
		if !allowedKinds[d.Kind] {
			return fmt.Errorf("input kind %q is not allowed", d.Kind)
		}
		return checkSettings(d.Settings)
	case "SetInputSettings":
		var d struct {
			Settings map[string]any `json:"inputSettings"`
		}
		if err := json.Unmarshal(data, &d); err != nil {
			return fmt.Errorf("bad SetInputSettings data: %w", err)
		}
		return checkSettings(d.Settings)
	case "GetSourceScreenshot":
		var d struct {
			Format string `json:"imageFormat"`
			Width  int    `json:"imageWidth"`
		}
		if err := json.Unmarshal(data, &d); err != nil {
			return fmt.Errorf("bad GetSourceScreenshot data: %w", err)
		}
		if d.Format != "jpg" && d.Format != "jpeg" && d.Format != "png" {
			return fmt.Errorf("screenshot format %q is not allowed", d.Format)
		}
		if d.Width <= 0 || d.Width > maxScreenshotWidth {
			return fmt.Errorf("screenshot width must be 1 to %d", maxScreenshotWidth)
		}
	}
	return nil
}

func checkSettings(settings map[string]any) error {
	for k, v := range settings {
		key := strings.ToLower(k)
		if bannedKeys[key] {
			return fmt.Errorf("setting %q points at a local file", k)
		}
		if key == "is_local_file" {
			if b, ok := v.(bool); ok && b {
				return fmt.Errorf("local media files are not allowed")
			}
		}
		s, isString := v.(string)
		if !isString {
			continue
		}
		switch key {
		case "url":
			if err := requireScheme(s, map[string]bool{"https": true}); err != nil {
				return fmt.Errorf("browser source url: %w", err)
			}
		case "input":
			if err := requireScheme(s, allowedMediaSchemes); err != nil {
				return fmt.Errorf("media source input: %w", err)
			}
		}
	}
	return nil
}

func requireScheme(raw string, schemes map[string]bool) error {
	u, err := url.Parse(raw)
	if err != nil || u.Host == "" {
		return fmt.Errorf("%q is not a full URL", raw)
	}
	if !schemes[strings.ToLower(u.Scheme)] {
		return fmt.Errorf("scheme %q is not allowed", u.Scheme)
	}
	return nil
}
