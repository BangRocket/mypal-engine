# Clara App — Jan Fork Design

## Goal

Fork Jan into Clara's native desktop/web/mobile app. Thin client connecting to the existing Clara gateway. Replaces Discord as the primary interface while sharing conversation history across platforms.

## Architecture

Thin client. All intelligence (memory, LLM, tools, reflection) stays on the gateway. The app sends messages and renders responses.

- Frontend: React 19 + Vite + TailwindCSS + Vercel AI SDK + Zustand
- Desktop: Tauri 2 (Rust)
- Mobile: Tauri mobile (iOS/Android, Phase 4)
- Web: Same React app served standalone

## Phases

### Phase 1: Fork + Connect
- Fork Jan, strip local inference, rebrand to Clara
- Point ClaraTransport at gateway API
- Basic chat working through gateway

### Phase 2: Shared History + Adapter
- Gateway adapter for Clara app (WebSocket)
- Source tracking on messages (discord/app/web)
- Cross-platform thread continuity

### Phase 3: Memory + Blog UI
- Memory inspector panel
- Blog drafting/publishing
- Settings panel

### Phase 4: Mobile
- Tauri iOS/Android builds
- Push notifications
