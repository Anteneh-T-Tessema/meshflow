package meshflow_test

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
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
