# Multi-Modal Inputs

MeshFlow agents accept images, documents, and audio alongside text tasks.

## Image inputs

```python
from meshflow import Agent, ImageInput, build_multimodal_message

agent = Agent(name="vision", role="researcher")

# From URL
result = await agent.run_multimodal(
    "Describe what you see in this image",
    inputs=[ImageInput(url="https://example.com/chart.png")],
)

# From base64
import base64
with open("screenshot.png", "rb") as f:
    b64 = base64.b64encode(f.read()).decode()

result = await agent.run_multimodal(
    "What errors appear in this screenshot?",
    inputs=[ImageInput(base64=b64, media_type="image/png")],
)
```

## Document inputs

```python
from meshflow import DocumentInput

result = await agent.run_multimodal(
    "Summarize the key findings",
    inputs=[DocumentInput(path="report.pdf")],  # txt, md, json, csv, pdf
)

# From text content directly
result = await agent.run_multimodal(
    "Extract all dates mentioned",
    inputs=[DocumentInput(content="Meeting on 2026-01-15. Follow-up on 2026-02-01.")],
)
```

## Audio inputs

```python
from meshflow import AudioInput

result = await agent.run_multimodal(
    "Transcribe and summarize this audio",
    inputs=[AudioInput(path="meeting.mp3")],
)
```

## Multiple inputs

```python
from meshflow import MultiModalInput

result = await agent.run_multimodal(
    "Compare the image and document — are they consistent?",
    inputs=[
        ImageInput(url="https://example.com/diagram.png"),
        DocumentInput(path="specification.md"),
    ],
)
```

## build_multimodal_message()

Low-level helper to construct the provider message format directly:

```python
from meshflow import build_multimodal_message

message = build_multimodal_message(
    text="What do you see?",
    inputs=[ImageInput(url="https://example.com/photo.jpg")],
)
# → {"role": "user", "content": [<image_block>, <text_block>]}
```
