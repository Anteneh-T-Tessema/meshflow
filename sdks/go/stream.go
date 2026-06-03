// Package meshflow — streaming helpers (v1.10.0).
//
// Provides idiomatic Go utilities for working with the channel-based streaming
// API returned by [Client.Stream] and [Client.LiveEvents].
//
// Quick start:
//
//	ch, err := client.Stream(ctx, "Summarise the quarterly report")
//	if err != nil { ... }
//
//	// Print tokens in real-time
//	for ev := range ch {
//	    if ev.IsToken() {
//	        fmt.Print(ev.TokenText())
//	    }
//	}
//
//	// Or collect the full output
//	ch2, _ := client.Stream(ctx, "Write a haiku")
//	fmt.Println(CollectTokens(ch2))
//
//	// Filter to routing events only
//	ch3, _ := client.Stream(ctx, task, meshflow.WithModelTiers(...))
//	for ev := range RoutingEvents(ctx, ch3) {
//	    fmt.Printf("tier=%s model=%s\n", ev.TierUsed, ev.ModelUsed)
//	}
package meshflow

import (
	"context"
	"strings"
)

// ── StreamEvent helper methods ────────────────────────────────────────────────

// IsToken reports whether this event carries a generated text token.
// When true, call TokenText() to get the text.
func (e StreamEvent) IsToken() bool {
	return e.EventType == "token" || e.EventType == "token_delta"
}

// IsDone reports whether this event signals stream completion.
func (e StreamEvent) IsDone() bool {
	return e.EventType == "done" || e.EventType == "run_complete"
}

// IsRouting reports whether this event describes a model-tier routing
// decision (tier selection or cascade escalation).
// Inspect e.TierUsed and e.ModelUsed for details.
func (e StreamEvent) IsRouting() bool {
	return e.EventType == "routing"
}

// IsCascadeEscalation reports whether this routing event represents a
// cascade tier upgrade (i.e. the initial model's confidence was too low
// and the router retried with a higher tier).
func (e StreamEvent) IsCascadeEscalation() bool {
	if !e.IsRouting() {
		return false
	}
	v, ok := e.Metadata["cascade_escalation"]
	if !ok {
		return false
	}
	b, ok := v.(bool)
	return ok && b
}

// IsError reports whether this event carries an error message.
func (e StreamEvent) IsError() bool {
	return e.EventType == "error"
}

// IsNodeStart reports whether an agent / graph node has started processing.
func (e StreamEvent) IsNodeStart() bool {
	return e.EventType == "node_start"
}

// IsNodeEnd reports whether an agent / graph node has finished processing.
// The full output of that step is available in e.Data.
func (e StreamEvent) IsNodeEnd() bool {
	return e.EventType == "node_end"
}

// TokenText returns the generated text content of a token event.
// Returns an empty string for non-token events.
//
// MeshFlow servers may send text in the "text", "content", or "output"
// field depending on the endpoint version; this method checks all three.
func (e StreamEvent) TokenText() string {
	if e.Text != "" {
		return e.Text
	}
	if e.Data != "" {
		return e.Data
	}
	return ""
}

// RoutingReason returns the human-readable routing rationale from a routing
// event's metadata, or an empty string if not present.
func (e StreamEvent) RoutingReason() string {
	v, ok := e.Metadata["reason"]
	if !ok {
		return ""
	}
	s, _ := v.(string)
	return s
}

// ── StreamEvent — metadata convenience ───────────────────────────────────────

// Metadata is the decoded key-value metadata map from a StreamEvent.
// For routing events it carries: model, tier, is_local, cascade_escalation,
// reason.  For node_end events it carries: tokens, cost_usd.
//
// We reuse the existing ErrMsg / CostUSD / Tokens fields for the common
// cases and surface metadata as a map for extensibility.
type Metadata = map[string]interface{}

// ── Channel utilities ─────────────────────────────────────────────────────────

// CollectTokens drains ch until it is closed and returns all token text
// concatenated into a single string.  ctx cancellation is not supported;
// use CollectTokensCtx for that.
//
// This is the simplest way to get the full text response from a stream:
//
//	ch, err := client.Stream(ctx, "Write a haiku")
//	if err != nil { ... }
//	fmt.Println(CollectTokens(ch))
func CollectTokens(ch <-chan StreamEvent) string {
	var sb strings.Builder
	for ev := range ch {
		if ev.IsToken() {
			sb.WriteString(ev.TokenText())
		}
	}
	return sb.String()
}

// CollectTokensCtx is like CollectTokens but returns early when ctx is
// cancelled, returning whatever text has accumulated so far alongside the
// context error.
func CollectTokensCtx(ctx context.Context, ch <-chan StreamEvent) (string, error) {
	var sb strings.Builder
	for {
		select {
		case ev, ok := <-ch:
			if !ok {
				return sb.String(), nil
			}
			if ev.IsToken() {
				sb.WriteString(ev.TokenText())
			}
		case <-ctx.Done():
			return sb.String(), ctx.Err()
		}
	}
}

// FilterStream returns a new channel that carries only events whose
// EventType matches one of kinds.  The goroutine drains the source channel
// completely (discarding non-matching events) so the caller does not need
// to consume events it does not care about.
//
// The returned channel is closed when ch is closed or ctx is cancelled.
//
// Example — only node_end events:
//
//	ends := FilterStream(ctx, ch, "node_end")
//	for ev := range ends {
//	    fmt.Printf("agent %s finished: %s\n", ev.AgentID, ev.Data[:80])
//	}
func FilterStream(ctx context.Context, ch <-chan StreamEvent, kinds ...string) <-chan StreamEvent {
	kindSet := make(map[string]struct{}, len(kinds))
	for _, k := range kinds {
		kindSet[k] = struct{}{}
	}
	out := make(chan StreamEvent, 16)
	go func() {
		defer close(out)
		for {
			select {
			case ev, ok := <-ch:
				if !ok {
					return
				}
				if _, match := kindSet[ev.EventType]; match {
					select {
					case out <- ev:
					case <-ctx.Done():
						// drain remaining events so the source goroutine can exit
						go func() { for range ch {} }() //nolint:revive
						return
					}
				}
			case <-ctx.Done():
				go func() { for range ch {} }() //nolint:revive
				return
			}
		}
	}()
	return out
}

// RoutingEvents returns a channel that carries only routing events (tier
// selections and cascade escalations).  Useful for cost-attribution
// dashboards and observability tooling.
//
//	for ev := range RoutingEvents(ctx, ch) {
//	    if ev.IsCascadeEscalation() {
//	        log.Printf("escalated to tier=%s model=%s", ev.TierUsed, ev.ModelUsed)
//	    }
//	}
func RoutingEvents(ctx context.Context, ch <-chan StreamEvent) <-chan StreamEvent {
	return FilterStream(ctx, ch, "routing")
}

// TokenStream transforms a StreamEvent channel into a plain string channel,
// emitting one string per token event.  Non-token events are silently
// discarded.  The output channel is closed when ch closes or ctx is done.
//
// Useful for direct wiring to a UI writer without routing-event noise:
//
//	for text := range TokenStream(ctx, ch) {
//	    fmt.Print(text)
//	}
func TokenStream(ctx context.Context, ch <-chan StreamEvent) <-chan string {
	out := make(chan string, 64)
	go func() {
		defer close(out)
		for {
			select {
			case ev, ok := <-ch:
				if !ok {
					return
				}
				if ev.IsToken() {
					t := ev.TokenText()
					if t == "" {
						continue
					}
					select {
					case out <- t:
					case <-ctx.Done():
						go func() { for range ch {} }() //nolint:revive
						return
					}
				}
			case <-ctx.Done():
				go func() { for range ch {} }() //nolint:revive
				return
			}
		}
	}()
	return out
}

// DrainStream reads and discards all events from ch until it is closed.
// Call this in a goroutine when you have obtained a stream but no longer
// need its events, to ensure the streaming goroutine can exit cleanly.
//
//	ch, err := client.Stream(ctx, task)
//	if err != nil { return err }
//	defer func() { go DrainStream(ch) }()
func DrainStream(ch <-chan StreamEvent) {
	for range ch {}
}
