# Demonstration-Guided Agentic Automation

> What if a business user could show an AI how work gets done —
> and that demonstration became a reviewable, executable automation?

This project explores an agentic automation pattern where a business user
records and narrates an existing process, the system extracts business intent
and visual evidence, creates a reviewable execution plan, and an AI agent
executes the approved process through the application's user interface.

## Demo

🎥 [Watch the 60-second demo] - https://youtu.be/gEhxQjRQ6XI

## White Paper

📄 [Read the architecture paper](docs/Demonstration-Guided-Agentic-Automation.pdf)

## How It Works

Screen Recording
→ Audio Extraction
→ Frame Extraction
→ Scene-Change Detection
→ Frame Deduplication
→ Vision + Narration Alignment
→ Structured Execution Plan
→ Human Review
→ Agent Execution
→ Application-State Verification

## Key Experiment

For this POC, Salesforce business actions are executed directly through
the application UI without requiring an application-specific API or MCP
tool for those business operations.

The agent does not replay recorded cursor coordinates. It observes the
live application and works toward the intent of the approved plan.

## From Recording to Understanding

A screen recording contains significant visual redundancy.
Instead of treating every video frame equally, the pipeline:

1. Extracts the audio track.
2. Transcribes the user's narration.
3. Samples frames over time.
4. Detects meaningful scene changes.
5. Removes adjacent duplicate frames.
6. Uses a vision-capable model to describe the selected UI states.
7. Aligns those visual observations with narration around the same timestamps.
8. Synthesizes the combined timeline into a structured execution plan.

## Contributors

**Sumit Paliwal** — Problem framing, architecture, engineering guidance & overall direction.

**Shrey Sharma** — Engineering implementation & POC development.
