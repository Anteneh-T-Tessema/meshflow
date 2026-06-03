package meshflow_test

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync"
	"testing"
	"time"

	meshflow "github.com/Anteneh-T-Tessema/meshflow/sdks/go"
)

// ── Test helpers ──────────────────────────────────────────────────────────────

func newTestClient(t *testing.T, handler http.Handler) (*meshflow.Client, *httptest.Server) {
	t.Helper()
	srv := httptest.NewServer(handler)
	t.Cleanup(srv.Close)
	c := meshflow.NewClient(srv.URL, "test-key")
	return c, srv
}

func jsonResp(w http.ResponseWriter, v interface{}) {
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(v)
}

// ── Client construction ───────────────────────────────────────────────────────

func TestNewClient(t *testing.T) {
	c := meshflow.NewClient("http://localhost:8000", "sk-test")
	if c == nil {
		t.Fatal("NewClient returned nil")
	}
	if c.BaseURL != "http://localhost:8000" {
		t.Errorf("BaseURL = %q, want %q", c.BaseURL, "http://localhost:8000")
	}
}

func TestNewClientStripsTrailingSlash(t *testing.T) {
	c := meshflow.NewClient("http://localhost:8000/", "key")
	if strings.HasSuffix(c.BaseURL, "/") {
		t.Errorf("BaseURL should not have trailing slash, got %q", c.BaseURL)
	}
}

// ── Health ────────────────────────────────────────────────────────────────────

func TestHealth(t *testing.T) {
	c, _ := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/health" {
			http.NotFound(w, r)
			return
		}
		jsonResp(w, map[string]interface{}{
			"ok": true, "version": "1.10.0", "uptime_s": 42.0, "db": "ok",
		})
	}))

	h, err := c.Health(context.Background())
	if err != nil {
		t.Fatalf("Health() error: %v", err)
	}
	if !h.OK {
		t.Error("Health.OK should be true")
	}
	if h.Version != "1.10.0" {
		t.Errorf("Health.Version = %q, want %q", h.Version, "1.10.0")
	}
}

// ── RunAgent ──────────────────────────────────────────────────────────────────

func TestRunAgent(t *testing.T) {
	c, _ := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/run" || r.Method != http.MethodPost {
			http.NotFound(w, r)
			return
		}
		jsonResp(w, map[string]interface{}{
			"run_id": "test-run-1", "status": "completed",
			"output": "MeshFlow is a governed multi-agent framework.",
			"total_cost_usd": 0.0, "total_tokens": 42,
			"agent_costs":  map[string]float64{"worker": 0.0},
			"cloud_agents": []string{},
		})
	}))

	result, err := c.RunAgent(context.Background(), "What is MeshFlow?")
	if err != nil {
		t.Fatalf("RunAgent() error: %v", err)
	}
	if result.RunID != "test-run-1" {
		t.Errorf("RunID = %q, want %q", result.RunID, "test-run-1")
	}
	if result.TotalTokens != 42 {
		t.Errorf("TotalTokens = %d, want 42", result.TotalTokens)
	}
}

func TestRunAgentWithOptions(t *testing.T) {
	var gotBody map[string]interface{}
	c, _ := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewDecoder(r.Body).Decode(&gotBody)
		jsonResp(w, map[string]interface{}{
			"run_id": "r2", "status": "completed", "output": "ok",
			"total_cost_usd": 0.0, "total_tokens": 10,
		})
	}))

	_, err := c.RunAgent(context.Background(), "task",
		meshflow.WithCostCap(0.50),
		meshflow.WithPolicyMode("hipaa"),
		meshflow.WithCascadeThreshold(0.65),
		meshflow.WithThinking(4000),
	)
	if err != nil {
		t.Fatalf("RunAgent with options: %v", err)
	}
	policy, _ := gotBody["policy"].(map[string]interface{})
	if policy == nil {
		t.Fatal("expected policy in request body")
	}
	if policy["budget_usd"] != 0.50 {
		t.Errorf("budget_usd = %v, want 0.50", policy["budget_usd"])
	}
	if policy["mode"] != "hipaa" {
		t.Errorf("mode = %v, want hipaa", policy["mode"])
	}
	if policy["cascade_threshold"] != 0.65 {
		t.Errorf("cascade_threshold = %v, want 0.65", policy["cascade_threshold"])
	}
	if policy["thinking_budget"] != float64(4000) {
		t.Errorf("thinking_budget = %v, want 4000", policy["thinking_budget"])
	}
}

func TestRunAgentServerError(t *testing.T) {
	c, _ := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "internal server error", http.StatusInternalServerError)
	}))

	_, err := c.RunAgent(context.Background(), "task")
	if err == nil {
		t.Fatal("expected error for 500 response")
	}
}

// ── RunResult v1.10.0 fields ──────────────────────────────────────────────────

func TestRunResultV110Fields(t *testing.T) {
	c, _ := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		jsonResp(w, map[string]interface{}{
			"run_id": "r3", "status": "completed", "output": "ok",
			"total_cost_usd":          0.021,
			"total_tokens":            500,
			"agent_costs":             map[string]interface{}{"planner": 0.0, "writer": 0.021},
			"cloud_agents":            []string{"writer"},
			"cache_read_tokens":       200,
			"cache_creation_tokens":   50,
			"cascade_escalations":     1,
		})
	}))

	r, err := c.RunAgent(context.Background(), "task")
	if err != nil {
		t.Fatal(err)
	}
	if r.AgentCosts["writer"] != 0.021 {
		t.Errorf("AgentCosts[writer] = %v, want 0.021", r.AgentCosts["writer"])
	}
	if len(r.CloudAgents) != 1 || r.CloudAgents[0] != "writer" {
		t.Errorf("CloudAgents = %v, want [writer]", r.CloudAgents)
	}
	if r.CacheReadTokens != 200 {
		t.Errorf("CacheReadTokens = %d, want 200", r.CacheReadTokens)
	}
	if r.CascadeEscalations != 1 {
		t.Errorf("CascadeEscalations = %d, want 1", r.CascadeEscalations)
	}
}

// ── Streaming ─────────────────────────────────────────────────────────────────

func ndjsonStream(events []map[string]interface{}) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/x-ndjson")
		for _, ev := range events {
			line, _ := json.Marshal(ev)
			fmt.Fprintf(w, "%s\n", line)
		}
	}
}

func TestStream(t *testing.T) {
	events := []map[string]interface{}{
		{"kind": "node_start", "agent_id": "writer"},
		{"kind": "token", "text": "Hello"},
		{"kind": "token", "text": " world"},
		{"kind": "node_end", "output": "Hello world"},
		{"kind": "done"},
	}
	c, _ := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/stream" {
			ndjsonStream(events)(w, r)
		} else {
			http.NotFound(w, r)
		}
	}))

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	ch, err := c.Stream(ctx, "Write hello world")
	if err != nil {
		t.Fatalf("Stream() error: %v", err)
	}

	var got []meshflow.StreamEvent
	for ev := range ch {
		got = append(got, ev)
	}

	if len(got) != 5 {
		t.Errorf("got %d events, want 5", len(got))
	}
}

func TestStreamSSEFormat(t *testing.T) {
	c, _ := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		// SSE format
		fmt.Fprintf(w, "event: token\ndata: {\"kind\":\"token\",\"text\":\"Hi\"}\n\n")
		fmt.Fprintf(w, "event: done\ndata: {\"kind\":\"done\"}\n\n")
	}))

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	ch, err := c.Stream(ctx, "task")
	if err != nil {
		t.Fatalf("Stream() error: %v", err)
	}
	var got []meshflow.StreamEvent
	for ev := range ch {
		got = append(got, ev)
	}
	if len(got) < 1 {
		t.Error("expected at least one event from SSE stream")
	}
}

// ── StreamEvent helper methods ────────────────────────────────────────────────

func TestStreamEventIsToken(t *testing.T) {
	for _, kind := range []string{"token", "token_delta"} {
		ev := meshflow.StreamEvent{EventType: kind, Text: "hello"}
		if !ev.IsToken() {
			t.Errorf("IsToken() = false for kind %q", kind)
		}
	}
	ev := meshflow.StreamEvent{EventType: "node_end"}
	if ev.IsToken() {
		t.Error("IsToken() = true for node_end")
	}
}

func TestStreamEventIsDone(t *testing.T) {
	for _, kind := range []string{"done", "run_complete"} {
		ev := meshflow.StreamEvent{EventType: kind}
		if !ev.IsDone() {
			t.Errorf("IsDone() = false for kind %q", kind)
		}
	}
	ev := meshflow.StreamEvent{EventType: "token", Text: "x"}
	if ev.IsDone() {
		t.Error("IsDone() = true for token event")
	}
}

func TestStreamEventIsRouting(t *testing.T) {
	ev := meshflow.StreamEvent{
		EventType: "routing",
		TierUsed:  "fast",
		ModelUsed: "llama3.2",
	}
	if !ev.IsRouting() {
		t.Error("IsRouting() = false")
	}
	if ev.IsToken() || ev.IsDone() {
		t.Error("routing event should not be token or done")
	}
}

func TestStreamEventIsCascadeEscalation(t *testing.T) {
	ev := meshflow.StreamEvent{
		EventType: "routing",
		Metadata:  map[string]interface{}{"cascade_escalation": true},
	}
	if !ev.IsCascadeEscalation() {
		t.Error("IsCascadeEscalation() = false for escalation event")
	}

	ev2 := meshflow.StreamEvent{
		EventType: "routing",
		Metadata:  map[string]interface{}{"cascade_escalation": false},
	}
	if ev2.IsCascadeEscalation() {
		t.Error("IsCascadeEscalation() = true for non-escalation routing event")
	}

	ev3 := meshflow.StreamEvent{EventType: "token"}
	if ev3.IsCascadeEscalation() {
		t.Error("IsCascadeEscalation() = true for token event")
	}
}

func TestStreamEventIsError(t *testing.T) {
	ev := meshflow.StreamEvent{EventType: "error", ErrMsg: "timeout"}
	if !ev.IsError() {
		t.Error("IsError() = false")
	}
}

func TestStreamEventTokenText(t *testing.T) {
	tests := []struct {
		ev   meshflow.StreamEvent
		want string
	}{
		{meshflow.StreamEvent{EventType: "token", Text: "hello"}, "hello"},
		{meshflow.StreamEvent{EventType: "token", Data: "world"}, "world"},
		{meshflow.StreamEvent{EventType: "token"}, ""},
		{meshflow.StreamEvent{EventType: "done"}, ""},
	}
	for _, tt := range tests {
		got := tt.ev.TokenText()
		if got != tt.want {
			t.Errorf("TokenText() = %q, want %q for event %+v", got, tt.want, tt.ev)
		}
	}
}

// ── CollectTokens ─────────────────────────────────────────────────────────────

func makeEventChan(events ...meshflow.StreamEvent) <-chan meshflow.StreamEvent {
	ch := make(chan meshflow.StreamEvent, len(events))
	for _, ev := range events {
		ch <- ev
	}
	close(ch)
	return ch
}

func TestCollectTokens(t *testing.T) {
	ch := makeEventChan(
		meshflow.StreamEvent{EventType: "node_start"},
		meshflow.StreamEvent{EventType: "token", Text: "Hello"},
		meshflow.StreamEvent{EventType: "token", Text: " world"},
		meshflow.StreamEvent{EventType: "node_end"},
		meshflow.StreamEvent{EventType: "done"},
	)
	got := meshflow.CollectTokens(ch)
	if got != "Hello world" {
		t.Errorf("CollectTokens() = %q, want %q", got, "Hello world")
	}
}

func TestCollectTokensEmpty(t *testing.T) {
	ch := makeEventChan(meshflow.StreamEvent{EventType: "done"})
	got := meshflow.CollectTokens(ch)
	if got != "" {
		t.Errorf("CollectTokens() = %q, want empty", got)
	}
}

func TestCollectTokensCtx(t *testing.T) {
	ch := makeEventChan(
		meshflow.StreamEvent{EventType: "token", Text: "abc"},
		meshflow.StreamEvent{EventType: "done"},
	)
	got, err := meshflow.CollectTokensCtx(context.Background(), ch)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got != "abc" {
		t.Errorf("CollectTokensCtx() = %q, want %q", got, "abc")
	}
}

func TestCollectTokensCtxCancelled(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel() // cancel immediately

	ch := make(chan meshflow.StreamEvent, 1)
	ch <- meshflow.StreamEvent{EventType: "token", Text: "x"}

	_, err := meshflow.CollectTokensCtx(ctx, ch)
	if err == nil {
		t.Error("expected context.Canceled error")
	}
}

// ── FilterStream ──────────────────────────────────────────────────────────────

func TestFilterStream(t *testing.T) {
	ch := makeEventChan(
		meshflow.StreamEvent{EventType: "node_start"},
		meshflow.StreamEvent{EventType: "token", Text: "a"},
		meshflow.StreamEvent{EventType: "routing"},
		meshflow.StreamEvent{EventType: "token", Text: "b"},
		meshflow.StreamEvent{EventType: "done"},
	)

	ctx := context.Background()
	tokenCh := meshflow.FilterStream(ctx, ch, "token")

	var texts []string
	for ev := range tokenCh {
		texts = append(texts, ev.TokenText())
	}
	if len(texts) != 2 || texts[0] != "a" || texts[1] != "b" {
		t.Errorf("FilterStream tokens = %v, want [a b]", texts)
	}
}

// ── RoutingEvents ─────────────────────────────────────────────────────────────

func TestRoutingEvents(t *testing.T) {
	ch := makeEventChan(
		meshflow.StreamEvent{EventType: "token", Text: "x"},
		meshflow.StreamEvent{EventType: "routing", TierUsed: "fast", ModelUsed: "llama3.2"},
		meshflow.StreamEvent{EventType: "token", Text: "y"},
		meshflow.StreamEvent{EventType: "routing", TierUsed: "smart", ModelUsed: "mistral",
			Metadata: map[string]interface{}{"cascade_escalation": true}},
		meshflow.StreamEvent{EventType: "done"},
	)

	ctx := context.Background()
	rch := meshflow.RoutingEvents(ctx, ch)

	var got []meshflow.StreamEvent
	for ev := range rch {
		got = append(got, ev)
	}
	if len(got) != 2 {
		t.Fatalf("RoutingEvents count = %d, want 2", len(got))
	}
	if got[0].TierUsed != "fast" {
		t.Errorf("got[0].TierUsed = %q, want fast", got[0].TierUsed)
	}
	if !got[1].IsCascadeEscalation() {
		t.Error("got[1] should be a cascade escalation")
	}
}

// ── TokenStream ───────────────────────────────────────────────────────────────

func TestTokenStream(t *testing.T) {
	ch := makeEventChan(
		meshflow.StreamEvent{EventType: "routing"},
		meshflow.StreamEvent{EventType: "token", Text: "hello"},
		meshflow.StreamEvent{EventType: "token", Text: " go"},
		meshflow.StreamEvent{EventType: "done"},
	)
	ctx := context.Background()
	var sb strings.Builder
	for text := range meshflow.TokenStream(ctx, ch) {
		sb.WriteString(text)
	}
	if sb.String() != "hello go" {
		t.Errorf("TokenStream result = %q, want %q", sb.String(), "hello go")
	}
}

// ── RunOptions / functional options ──────────────────────────────────────────

func TestRunOptions(t *testing.T) {
	tests := []struct {
		name   string
		opts   []meshflow.RunOption
		checks func(t *testing.T, got map[string]interface{})
	}{
		{
			"WithCostCap",
			[]meshflow.RunOption{meshflow.WithCostCap(1.5)},
			func(t *testing.T, p map[string]interface{}) {
				if p["budget_usd"] != 1.5 {
					t.Errorf("budget_usd = %v, want 1.5", p["budget_usd"])
				}
			},
		},
		{
			"WithModelTiers",
			[]meshflow.RunOption{meshflow.WithModelTiers([]meshflow.ModelTierConfig{
				{Name: "fast", Model: "llama3.2", MaxTokens: 512},
				{Name: "large", Model: "gpt-4o", MaxTokens: 4096},
			})},
			func(t *testing.T, p map[string]interface{}) {
				tiers, ok := p["model_tiers"]
				if !ok {
					t.Error("model_tiers not in policy")
				}
				_ = tiers
			},
		},
		{
			"WithCascadeThreshold",
			[]meshflow.RunOption{meshflow.WithCascadeThreshold(0.65)},
			func(t *testing.T, p map[string]interface{}) {
				if p["cascade_threshold"] != 0.65 {
					t.Errorf("cascade_threshold = %v, want 0.65", p["cascade_threshold"])
				}
			},
		},
		{
			"WithThinking",
			[]meshflow.RunOption{meshflow.WithThinking(8000)},
			func(t *testing.T, p map[string]interface{}) {
				if p["thinking_budget"] != float64(8000) {
					t.Errorf("thinking_budget = %v, want 8000", p["thinking_budget"])
				}
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			var gotBody map[string]interface{}
			c, _ := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				_ = json.NewDecoder(r.Body).Decode(&gotBody)
				jsonResp(w, map[string]interface{}{
					"run_id": "r", "status": "completed", "output": "ok",
					"total_cost_usd": 0.0, "total_tokens": 0,
				})
			}))

			_, err := c.RunAgent(context.Background(), "task", tt.opts...)
			if err != nil {
				t.Fatalf("RunAgent: %v", err)
			}
			policy, _ := gotBody["policy"].(map[string]interface{})
			if policy == nil && len(tt.opts) > 0 {
				t.Fatal("policy missing from request")
			}
			if policy != nil {
				tt.checks(t, policy)
			}
		})
	}
}

// ── ImageInput ────────────────────────────────────────────────────────────────

var _pngBytes = []byte("\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01" +
	"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00" +
	"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")

func TestNewImageFromBytes(t *testing.T) {
	img := meshflow.NewImageFromBytes(_pngBytes, "image/png")
	block := img.ToContentBlock()
	if block["type"] != "image" {
		t.Errorf("type = %v, want image", block["type"])
	}
	src, _ := block["source"].(map[string]interface{})
	if src["type"] != "base64" {
		t.Errorf("source.type = %v, want base64", src["type"])
	}
	if src["media_type"] != "image/png" {
		t.Errorf("media_type = %v, want image/png", src["media_type"])
	}
}

func TestNewImageFromURL(t *testing.T) {
	img := meshflow.NewImageFromURL("https://example.com/chart.png")
	block := img.ToContentBlock()
	src, _ := block["source"].(map[string]interface{})
	if src["type"] != "url" {
		t.Errorf("source.type = %v, want url", src["type"])
	}
	if src["url"] != "https://example.com/chart.png" {
		t.Errorf("url = %v", src["url"])
	}
}

func TestNewImageFromURLOpenAI(t *testing.T) {
	img := meshflow.NewImageFromURL("https://example.com/chart.png")
	block := img.ToOpenAIContentBlock()
	if block["type"] != "image_url" {
		t.Errorf("type = %v, want image_url", block["type"])
	}
	iu, _ := block["image_url"].(map[string]interface{})
	if iu["url"] != "https://example.com/chart.png" {
		t.Errorf("image_url.url = %v", iu["url"])
	}
}

func TestNewImageFromBytesOpenAI(t *testing.T) {
	img := meshflow.NewImageFromBytes(_pngBytes, "image/png")
	block := img.ToOpenAIContentBlock()
	if block["type"] != "image_url" {
		t.Errorf("type = %v, want image_url", block["type"])
	}
	iu, _ := block["image_url"].(map[string]interface{})
	url, _ := iu["url"].(string)
	if !strings.HasPrefix(url, "data:image/png;base64,") {
		t.Errorf("url should be a data URI")
	}
}

func TestNewImageFromFile(t *testing.T) {
	f, err := os.CreateTemp("", "testimg*.png")
	if err != nil {
		t.Fatal(err)
	}
	defer os.Remove(f.Name())
	_, _ = f.Write(_pngBytes)
	f.Close()

	img, err := meshflow.NewImageFromFile(f.Name())
	if err != nil {
		t.Fatalf("NewImageFromFile: %v", err)
	}
	block := img.ToContentBlock()
	src, _ := block["source"].(map[string]interface{})
	if src["type"] != "base64" {
		t.Errorf("source.type = %v, want base64", src["type"])
	}
}

func TestNewImageFromFileMissing(t *testing.T) {
	_, err := meshflow.NewImageFromFile("/nonexistent/path/image.png")
	if err == nil {
		t.Error("expected error for missing file")
	}
}

// ── DocumentInput ─────────────────────────────────────────────────────────────

func TestNewDocumentFromString(t *testing.T) {
	doc := meshflow.NewDocumentFromString("Revenue: $1.2M", "report.txt")
	block := doc.ToContentBlock()
	if block["type"] != "document" {
		t.Errorf("type = %v, want document", block["type"])
	}
	src, _ := block["source"].(map[string]interface{})
	if src["text"] != "Revenue: $1.2M" {
		t.Errorf("text = %v", src["text"])
	}
}

func TestNewDocumentFromStringOpenAI(t *testing.T) {
	doc := meshflow.NewDocumentFromString("hello", "note.txt")
	block := doc.ToOpenAIContentBlock()
	if block["type"] != "text" {
		t.Errorf("type = %v, want text", block["type"])
	}
	text, _ := block["text"].(string)
	if !strings.Contains(text, "hello") {
		t.Errorf("text should contain document content")
	}
}

func TestNewDocumentFromFile(t *testing.T) {
	f, err := os.CreateTemp("", "testdoc*.txt")
	if err != nil {
		t.Fatal(err)
	}
	defer os.Remove(f.Name())
	_, _ = f.WriteString("quarterly results: up 12%")
	f.Close()

	doc, err := meshflow.NewDocumentFromFile(f.Name())
	if err != nil {
		t.Fatalf("NewDocumentFromFile: %v", err)
	}
	block := doc.ToContentBlock()
	src, _ := block["source"].(map[string]interface{})
	if src["text"] != "quarterly results: up 12%" {
		t.Errorf("text = %v", src["text"])
	}
}

func TestNewDocumentFromBytes(t *testing.T) {
	doc := meshflow.NewDocumentFromBytes([]byte("%PDF-1.4"), "application/pdf", "invoice.pdf")
	block := doc.ToContentBlock()
	src, _ := block["source"].(map[string]interface{})
	if src["type"] != "base64" {
		t.Errorf("source.type = %v, want base64", src["type"])
	}
}

// ── AudioInput ────────────────────────────────────────────────────────────────

func TestNewAudioFromBytes(t *testing.T) {
	audio := meshflow.NewAudioFromBytes([]byte("\xff\xfb\x90\x04"), "audio/mpeg")
	block := audio.ToContentBlock()
	if block["type"] != "audio" {
		t.Errorf("type = %v, want audio", block["type"])
	}
}

func TestNewAudioFromBytesOpenAI(t *testing.T) {
	audio := meshflow.NewAudioFromBytes([]byte("\xff\xfb"), "audio/mpeg")
	block := audio.ToOpenAIContentBlock()
	if block["type"] != "input_audio" {
		t.Errorf("type = %v, want input_audio", block["type"])
	}
}

// ── RunAgentStructured ────────────────────────────────────────────────────────

func TestRunAgentStructured(t *testing.T) {
	var gotBody map[string]interface{}
	c, _ := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewDecoder(r.Body).Decode(&gotBody)
		jsonResp(w, map[string]interface{}{
			"run_id":  "struct-1",
			"status":  "completed",
			"output":  `{"title":"Q3 Report","summary":"Revenue up 12%"}`,
			"parsed_output": map[string]interface{}{
				"title":   "Q3 Report",
				"summary": "Revenue up 12%",
			},
			"total_cost_usd": 0.0,
			"total_tokens":   60,
		})
	}))

	schema := `{"type":"object","properties":{"title":{"type":"string"},"summary":{"type":"string"}}}`
	result, err := c.RunAgentStructured(context.Background(),
		"Write a Q3 market report.",
		schema,
	)
	if err != nil {
		t.Fatalf("RunAgentStructured: %v", err)
	}
	if result.RunID != "struct-1" {
		t.Errorf("RunID = %q, want struct-1", result.RunID)
	}
	if result.ParsedOutput == nil {
		t.Fatal("ParsedOutput should not be nil")
	}
	if result.ParsedOutput["title"] != "Q3 Report" {
		t.Errorf("title = %v, want Q3 Report", result.ParsedOutput["title"])
	}
	// Verify request body sent output_mode=json and schema
	if gotBody["output_mode"] != "json" {
		t.Errorf("output_mode = %v, want json", gotBody["output_mode"])
	}
	if gotBody["schema"] != schema {
		t.Errorf("schema not forwarded correctly")
	}
}

func TestRunAgentStructuredEmptySchema(t *testing.T) {
	c, _ := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		jsonResp(w, map[string]interface{}{
			"run_id": "s2", "status": "completed", "output": "{}",
			"total_cost_usd": 0.0, "total_tokens": 10,
		})
	}))
	result, err := c.RunAgentStructured(context.Background(), "task", "")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result.RunID != "s2" {
		t.Errorf("RunID = %q", result.RunID)
	}
}

func TestRunAgentStructuredServerError(t *testing.T) {
	c, _ := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "internal error", http.StatusInternalServerError)
	}))
	_, err := c.RunAgentStructured(context.Background(), "task", "")
	if err == nil {
		t.Error("expected error for 500 response")
	}
}

func TestRunAgentStructuredWithOptions(t *testing.T) {
	var gotBody map[string]interface{}
	c, _ := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewDecoder(r.Body).Decode(&gotBody)
		jsonResp(w, map[string]interface{}{
			"run_id": "s3", "status": "completed",
			"total_cost_usd": 0.0, "total_tokens": 0,
		})
	}))
	_, err := c.RunAgentStructured(context.Background(), "task", "",
		meshflow.WithCostCap(0.10),
	)
	if err != nil {
		t.Fatal(err)
	}
	policy, _ := gotBody["policy"].(map[string]interface{})
	if policy == nil || policy["budget_usd"] != 0.10 {
		t.Errorf("budget_usd not forwarded, policy=%v", policy)
	}
}

func TestStructuredRunResultEmbedRunResult(t *testing.T) {
	c, _ := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		jsonResp(w, map[string]interface{}{
			"run_id": "r", "status": "completed",
			"total_cost_usd": 0.021, "total_tokens": 400,
			"cloud_agents": []string{"writer"},
			"parsed_output": map[string]interface{}{"x": 1},
		})
	}))
	result, err := c.RunAgentStructured(context.Background(), "task", "")
	if err != nil {
		t.Fatal(err)
	}
	// Embedded RunResult fields accessible
	if result.TotalCostUSD != 0.021 {
		t.Errorf("TotalCostUSD = %v", result.TotalCostUSD)
	}
	if len(result.CloudAgents) != 1 || result.CloudAgents[0] != "writer" {
		t.Errorf("CloudAgents = %v", result.CloudAgents)
	}
	// ParsedOutput accessible
	if result.ParsedOutput["x"] != float64(1) {
		t.Errorf("ParsedOutput[x] = %v", result.ParsedOutput["x"])
	}
}

// ── BuildContentBlocks ────────────────────────────────────────────────────────

func TestBuildContentBlocksAnthropic(t *testing.T) {
	img := meshflow.NewImageFromBytes(_pngBytes, "image/png")
	doc := meshflow.NewDocumentFromString("text", "doc.txt")
	blocks := meshflow.BuildContentBlocks([]meshflow.MultimodalInput{img, doc}, "anthropic")
	if len(blocks) != 2 {
		t.Errorf("len = %d, want 2", len(blocks))
	}
	if blocks[0]["type"] != "image" {
		t.Errorf("blocks[0].type = %v, want image", blocks[0]["type"])
	}
	if blocks[1]["type"] != "document" {
		t.Errorf("blocks[1].type = %v, want document", blocks[1]["type"])
	}
}

func TestBuildContentBlocksOpenAI(t *testing.T) {
	img := meshflow.NewImageFromBytes(_pngBytes, "image/png")
	blocks := meshflow.BuildContentBlocks([]meshflow.MultimodalInput{img}, "openai")
	if blocks[0]["type"] != "image_url" {
		t.Errorf("blocks[0].type = %v, want image_url", blocks[0]["type"])
	}
}

// ── RunAgentMultimodal ────────────────────────────────────────────────────────

func TestRunAgentMultimodal(t *testing.T) {
	var gotBody map[string]interface{}
	c, _ := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewDecoder(r.Body).Decode(&gotBody)
		jsonResp(w, map[string]interface{}{
			"run_id": "mm-1", "status": "completed",
			"output": "Invoice total: $1,200", "total_cost_usd": 0.0, "total_tokens": 80,
		})
	}))

	img := meshflow.NewImageFromBytes(_pngBytes, "image/png")
	result, err := c.RunAgentMultimodal(context.Background(),
		"Extract the invoice total.",
		[]meshflow.MultimodalInput{img},
	)
	if err != nil {
		t.Fatalf("RunAgentMultimodal: %v", err)
	}
	if result.RunID != "mm-1" {
		t.Errorf("RunID = %q, want mm-1", result.RunID)
	}
	inputs, _ := gotBody["multimodal_inputs"].([]interface{})
	if len(inputs) != 1 {
		t.Errorf("multimodal_inputs len = %d, want 1", len(inputs))
	}
}

func TestRunAgentMultimodalMultipleInputs(t *testing.T) {
	var gotBody map[string]interface{}
	c, _ := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewDecoder(r.Body).Decode(&gotBody)
		jsonResp(w, map[string]interface{}{
			"run_id": "r", "status": "completed", "output": "ok",
			"total_cost_usd": 0.0, "total_tokens": 0,
		})
	}))

	img := meshflow.NewImageFromBytes(_pngBytes, "image/png")
	doc := meshflow.NewDocumentFromString("context text", "ctx.txt")
	_, err := c.RunAgentMultimodal(context.Background(), "task",
		[]meshflow.MultimodalInput{img, doc},
	)
	if err != nil {
		t.Fatal(err)
	}
	inputs, _ := gotBody["multimodal_inputs"].([]interface{})
	if len(inputs) != 2 {
		t.Errorf("multimodal_inputs len = %d, want 2", len(inputs))
	}
}

// ── BatchRun ──────────────────────────────────────────────────────────────────

func TestBatchRun(t *testing.T) {
	var mu sync.Mutex
	calls := 0
	c, _ := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		calls++
		mu.Unlock()
		var body map[string]interface{}
		_ = json.NewDecoder(r.Body).Decode(&body)
		jsonResp(w, map[string]interface{}{
			"run_id": "batch-r", "status": "completed",
			"output":         fmt.Sprintf("result for: %v", body["task"]),
			"total_cost_usd": 0.0, "total_tokens": 10,
		})
	}))

	tasks := []string{"task A", "task B", "task C", "task D"}
	results := c.BatchRun(context.Background(), tasks, 2)
	if len(results) != 4 {
		t.Fatalf("len(results) = %d, want 4", len(results))
	}
	if calls != 4 {
		t.Errorf("server called %d times, want 4", calls)
	}
	for i, r := range results {
		if r == nil {
			t.Errorf("results[%d] is nil", i)
			continue
		}
		if r.Status != "completed" {
			t.Errorf("results[%d].Status = %q, want completed", i, r.Status)
		}
	}
}

func TestBatchRunOrder(t *testing.T) {
	c, _ := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body map[string]interface{}
		_ = json.NewDecoder(r.Body).Decode(&body)
		task, _ := body["task"].(string)
		jsonResp(w, map[string]interface{}{
			"run_id": "r", "status": "completed",
			"output": "echo:" + task, "total_cost_usd": 0.0, "total_tokens": 5,
		})
	}))

	tasks := []string{"alpha", "beta", "gamma"}
	results := c.BatchRun(context.Background(), tasks, 3)
	for i, task := range tasks {
		if results[i] == nil {
			t.Errorf("results[%d] nil", i)
			continue
		}
		if results[i].Output != "echo:"+task {
			t.Errorf("results[%d].Output = %q, want %q", i, results[i].Output, "echo:"+task)
		}
	}
}

func TestBatchRunEmpty(t *testing.T) {
	c := meshflow.NewClient("http://localhost:9999", "")
	results := c.BatchRun(context.Background(), []string{}, 4)
	if len(results) != 0 {
		t.Errorf("len(results) = %d, want 0", len(results))
	}
}

func TestBatchRunServerError(t *testing.T) {
	c, _ := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "unavailable", http.StatusServiceUnavailable)
	}))

	results := c.BatchRun(context.Background(), []string{"t1", "t2"}, 2)
	for i, r := range results {
		if r == nil {
			t.Errorf("results[%d] nil; expected failed RunResult", i)
			continue
		}
		if r.Status != "failed" {
			t.Errorf("results[%d].Status = %q, want failed", i, r.Status)
		}
		if r.Error == "" {
			t.Errorf("results[%d].Error should be set", i)
		}
	}
}

func TestBatchRunDefaultConcurrency(t *testing.T) {
	c, _ := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		jsonResp(w, map[string]interface{}{
			"run_id": "r", "status": "completed", "output": "ok",
			"total_cost_usd": 0.0, "total_tokens": 0,
		})
	}))
	results := c.BatchRun(context.Background(), []string{"a", "b"}, 0)
	if len(results) != 2 {
		t.Errorf("len = %d, want 2", len(results))
	}
}
