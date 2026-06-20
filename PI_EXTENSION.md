# Phase 3 — Pi Coding Agent Extension

Pi integration with the hpm shared memory store. Deferred until Pi is set up locally.

## Overview

Pi uses a TypeScript extension API (`@earendil-works/pi-coding-agent`). The goal is to give Pi the same memory capabilities Hermes has — auto-capture, semantic recall, and cited answers — by building a Pi extension that communicates with the shared `hpm` CLI.

## Extension Surface

| Hook / API | What It Would Do |
|---|---|
| `Dynamic Context` extension | Inject relevant memory summaries at the start of each Pi session |
| Tool registration | Add `/memory-find` and `/memory-save` as first-class Pi commands |
| Keyboard shortcuts | `Ctrl+M` to quick-save a fact |
| Event hooks (if available) | Auto-capture hook after each tool call |

## Prerequisites (to investigate when implementing)

Pi's extension API docs need review at https://github.com/earendil-works/pi and in the `docs/` directory within the Pi package. Determine whether Pi provides:

- A per-turn / post-tool-call hook (for auto-capture)
- A compaction callback (for memory-aware summarization)
- Direct access to the session tree (for tagging memories to tree nodes)

If Pi lacks a post-turn hook, fall back to polling its session file for new branches, the same pattern the Hermes sidecar uses with `state.db`.

## Extension Sketch

```typescript
// pi-memory-extension.ts — rough design
import { Extension, Tool } from '@earendil-works/pi-coding-agent';

export class MemoryExtension implements Extension {
  name = 'pi-memory';
  version = '0.1.0';

  tools: Tool[] = [
    {
      name: 'memory-find',
      description: 'Search past memories with semantic understanding',
      handler: async (query: string) => {
        // Call shared Python CLI: `hpm answer "<query>" --limit 5`
        // Parse results, return with citations
      }
    },
    {
      name: 'memory-save',
      description: 'Explicitly save a fact to memory',
      handler: async (content: string, tags?: string[]) => {
        // Call shared Python CLI: `hpm save "<content>" --tags ...`
      }
    }
  ];

  // If Pi exposes a post-turn hook:
  onAfterTurn?: async (turn) => {
    const summary = await cheapLLM.summarize(turn.messages);
    const result = await exec(`hpm capture "${summary}" --source pi`);
  };

  // Inject relevant context at session start:
  augmentContext?: async () => {
    const result = await exec(`hpm answer "" --limit 5`);
    // Format as markdown block for Pi's system prompt
  };
}
```

## Shared CLI Bridge

Both agents use the same `hpm` CLI, avoiding duplicated memory logic:

| Command | Description |
|---|---|
| `hpm capture <text>` | Summarize and store a conversation turn |
| `hpm answer "<query>"` | Cited answer via hybrid search + reranker + LLM |
| `hpm save "<fact>" [--tags ...]` | Direct fact save |
| `hpm status` | Show store statistics |

## Dependencies

- Pi Coding Agent installed and configured locally
- `hpm` CLI installed and in PATH
- `OPENCODE_GO_API_KEY` set for summarization and answer synthesis

## Build Notes

Place the extension at `~/code/hpm/src/pi-extension/pi-memory-extension.ts` when ready. The extension directory structure already exists in the project's AGENTS.md ownership table as `src/pi-extension/` (not yet created).
