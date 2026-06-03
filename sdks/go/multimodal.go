// Package meshflow — multi-modal input types (v1.10.0).
//
// Mirrors the Python SDK's meshflow.multimodal.inputs module for use with
// [Client.RunAgentMultimodal] and [Client.StreamMultimodal].
//
// Quick start:
//
//	// From raw bytes (HTTP download, in-memory data)
//	img := meshflow.NewImageFromBytes(pngBytes, "image/png")
//
//	// From a remote URL
//	img := meshflow.NewImageFromURL("https://example.com/chart.png")
//
//	// From a local file
//	img, err := meshflow.NewImageFromFile("screenshot.png")
//
//	// From a text string
//	doc := meshflow.NewDocumentFromString(jsonText, "response.json")
//
//	// Run with multimodal inputs
//	result, err := client.RunAgentMultimodal(ctx,
//	    "Extract all line items from this invoice.",
//	    []meshflow.MultimodalInput{img, doc},
//	)
package meshflow

import (
	"encoding/base64"
	"fmt"
	"mime"
	"os"
	"path/filepath"
	"strings"
)

// MultimodalInput is the interface implemented by all multi-modal input types.
// Pass a slice of MultimodalInput to RunAgentMultimodal or StreamMultimodal.
type MultimodalInput interface {
	// ToContentBlock returns an Anthropic-compatible content block.
	ToContentBlock() map[string]interface{}
	// ToOpenAIContentBlock returns an OpenAI-compatible content block.
	ToOpenAIContentBlock() map[string]interface{}
}

// ── ImageInput ────────────────────────────────────────────────────────────────

// ImageInput represents an image for multi-modal LLM calls.
//
// Create with one of the constructor functions:
//   - [NewImageFromBytes] — raw bytes (HTTP downloads, in-memory data)
//   - [NewImageFromURL] — remote URL (no download required)
//   - [NewImageFromFile] — local file path
type ImageInput struct {
	source   string // URL or file path; empty when data is set
	mimeType string
	data     []byte
}

// NewImageFromBytes creates an ImageInput from raw image bytes.
// mimeType is the MIME type of the image, e.g. "image/png" or "image/jpeg".
//
//	resp, _ := http.Get("https://example.com/chart.png")
//	body, _ := io.ReadAll(resp.Body)
//	img := meshflow.NewImageFromBytes(body, "image/png")
func NewImageFromBytes(data []byte, mimeType string) ImageInput {
	return ImageInput{data: data, mimeType: mimeType}
}

// NewImageFromURL creates an ImageInput from a public remote URL.
// The URL is forwarded to the provider without downloading.
//
// Note: not all providers support URL image sources; use NewImageFromBytes
// for maximum compatibility.
func NewImageFromURL(url string) ImageInput {
	return ImageInput{source: url, mimeType: "image/jpeg"}
}

// NewImageFromFile reads an image file from disk and returns an ImageInput.
// The MIME type is detected from the file extension.
//
//	img, err := meshflow.NewImageFromFile("invoice_scan.png")
func NewImageFromFile(path string) (ImageInput, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return ImageInput{}, fmt.Errorf("meshflow: read image file %q: %w", path, err)
	}
	ext := strings.ToLower(filepath.Ext(path))
	mimeType := mime.TypeByExtension(ext)
	if mimeType == "" {
		mimeType = "image/jpeg"
	}
	return ImageInput{data: data, mimeType: mimeType}, nil
}

// ToContentBlock returns an Anthropic-compatible image content block.
func (img ImageInput) ToContentBlock() map[string]interface{} {
	if img.source != "" && (strings.HasPrefix(img.source, "http://") ||
		strings.HasPrefix(img.source, "https://")) {
		return map[string]interface{}{
			"type":   "image",
			"source": map[string]interface{}{"type": "url", "url": img.source},
		}
	}
	b64 := base64.StdEncoding.EncodeToString(img.data)
	return map[string]interface{}{
		"type": "image",
		"source": map[string]interface{}{
			"type":       "base64",
			"media_type": img.mimeType,
			"data":       b64,
		},
	}
}

// ToOpenAIContentBlock returns an OpenAI-compatible image_url content block
// for use with GPT-4o, GPT-4-turbo, and compatible models.
func (img ImageInput) ToOpenAIContentBlock() map[string]interface{} {
	if img.source != "" {
		return map[string]interface{}{
			"type":      "image_url",
			"image_url": map[string]interface{}{"url": img.source},
		}
	}
	b64 := base64.StdEncoding.EncodeToString(img.data)
	dataURI := fmt.Sprintf("data:%s;base64,%s", img.mimeType, b64)
	return map[string]interface{}{
		"type":      "image_url",
		"image_url": map[string]interface{}{"url": dataURI},
	}
}

// ── DocumentInput ─────────────────────────────────────────────────────────────

// DocumentInput represents a text or PDF document for multi-modal LLM calls.
//
// Create with one of the constructor functions:
//   - [NewDocumentFromString] — plain text, JSON, Markdown, CSV
//   - [NewDocumentFromFile] — local file path
//   - [NewDocumentFromBytes] — raw PDF bytes
type DocumentInput struct {
	title    string
	text     string // plain text content (non-PDF)
	data     []byte // raw bytes (for PDF blobs)
	mimeType string
}

// NewDocumentFromString creates a DocumentInput from a plain-text string.
// title is used as the document title in the context (e.g. "report.json").
//
//	doc := meshflow.NewDocumentFromString(apiResponseBody, "api_response.json")
func NewDocumentFromString(text, title string) DocumentInput {
	return DocumentInput{text: text, title: title}
}

// NewDocumentFromFile reads a text file from disk.
// Supported types: .txt, .md, .json, .csv, .yaml, .xml, and most plain-text formats.
// For PDFs, use NewDocumentFromBytes.
//
//	doc, err := meshflow.NewDocumentFromFile("contract.md")
func NewDocumentFromFile(path string) (DocumentInput, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return DocumentInput{}, fmt.Errorf("meshflow: read document file %q: %w", path, err)
	}
	return DocumentInput{text: string(data), title: filepath.Base(path)}, nil
}

// NewDocumentFromBytes creates a DocumentInput from raw bytes (e.g. a PDF download).
// mimeType should be "application/pdf" for PDFs.
//
//	body, _ := io.ReadAll(pdfResp.Body)
//	doc := meshflow.NewDocumentFromBytes(body, "application/pdf", "invoice.pdf")
func NewDocumentFromBytes(data []byte, mimeType, title string) DocumentInput {
	return DocumentInput{data: data, mimeType: mimeType, title: title}
}

// ToContentBlock returns an Anthropic-compatible document content block.
func (d DocumentInput) ToContentBlock() map[string]interface{} {
	if d.text != "" {
		return map[string]interface{}{
			"type":   "document",
			"title":  d.title,
			"source": map[string]interface{}{"type": "text", "text": d.text},
		}
	}
	b64 := base64.StdEncoding.EncodeToString(d.data)
	mt := d.mimeType
	if mt == "" {
		mt = "application/pdf"
	}
	return map[string]interface{}{
		"type":  "document",
		"title": d.title,
		"source": map[string]interface{}{
			"type":       "base64",
			"media_type": mt,
			"data":       b64,
		},
	}
}

// ToOpenAIContentBlock returns an OpenAI-compatible text content block.
// OpenAI does not have a native document type; documents are wrapped as text.
func (d DocumentInput) ToOpenAIContentBlock() map[string]interface{} {
	text := d.text
	if text == "" && len(d.data) > 0 {
		// PDF bytes — provide a note (full text extraction requires pypdf on server)
		text = fmt.Sprintf("[%s — binary document, %d bytes]", d.title, len(d.data))
	}
	return map[string]interface{}{
		"type": "text",
		"text": fmt.Sprintf("[%s]\n%s", d.title, text),
	}
}

// ── AudioInput ────────────────────────────────────────────────────────────────

// AudioInput represents an audio file for multi-modal LLM calls.
//
// Create with [NewAudioFromBytes] or [NewAudioFromFile].
type AudioInput struct {
	mimeType string
	data     []byte
}

// NewAudioFromBytes creates an AudioInput from raw audio bytes.
//
//	audio := meshflow.NewAudioFromBytes(mp3Data, "audio/mpeg")
func NewAudioFromBytes(data []byte, mimeType string) AudioInput {
	return AudioInput{data: data, mimeType: mimeType}
}

// NewAudioFromFile reads an audio file from disk.
//
//	audio, err := meshflow.NewAudioFromFile("recording.mp3")
func NewAudioFromFile(path string) (AudioInput, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return AudioInput{}, fmt.Errorf("meshflow: read audio file %q: %w", path, err)
	}
	ext := strings.ToLower(filepath.Ext(path))
	mimeType := mime.TypeByExtension(ext)
	if mimeType == "" {
		mimeType = "audio/mpeg"
	}
	return AudioInput{data: data, mimeType: mimeType}, nil
}

// ToContentBlock returns an Anthropic-compatible audio content block.
func (a AudioInput) ToContentBlock() map[string]interface{} {
	b64 := base64.StdEncoding.EncodeToString(a.data)
	return map[string]interface{}{
		"type": "audio",
		"source": map[string]interface{}{
			"type":       "base64",
			"media_type": a.mimeType,
			"data":       b64,
		},
	}
}

// ToOpenAIContentBlock returns an OpenAI-compatible input_audio content block.
func (a AudioInput) ToOpenAIContentBlock() map[string]interface{} {
	b64 := base64.StdEncoding.EncodeToString(a.data)
	ext := strings.TrimPrefix(strings.Split(a.mimeType, "/")[len(strings.Split(a.mimeType, "/"))-1], "x-")
	if ext == "mpeg" {
		ext = "mp3"
	}
	return map[string]interface{}{
		"type": "input_audio",
		"input_audio": map[string]interface{}{
			"data":   b64,
			"format": ext,
		},
	}
}

// ── Helper: build content blocks ──────────────────────────────────────────────

// BuildContentBlocks converts a slice of MultimodalInputs into provider-formatted
// content blocks.  provider should be "anthropic" (default) or "openai".
func BuildContentBlocks(inputs []MultimodalInput, provider string) []map[string]interface{} {
	isOpenAI := strings.EqualFold(provider, "openai") ||
		strings.EqualFold(provider, "gpt") ||
		strings.EqualFold(provider, "azure")
	blocks := make([]map[string]interface{}, len(inputs))
	for i, inp := range inputs {
		if isOpenAI {
			blocks[i] = inp.ToOpenAIContentBlock()
		} else {
			blocks[i] = inp.ToContentBlock()
		}
	}
	return blocks
}
