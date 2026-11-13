# Third-party notices

## Optional AI runtime downloads (Windows x64)

The executable offers an optional per-user download; these components are not
embedded in the messenger. Upstream license and notice files remain with their
packages in the installed runtime directory:

- Node.js 22.23.5 — MIT and bundled dependency notices (`node/LICENSE`).
- Git for Windows 2.56.1 / Git Bash — GPLv2 and bundled component licenses
  (including Bash/coreutils and MSYS); see the installed Git distribution's notices
  and [Git for Windows source](https://github.com/git-for-windows/git).
- Pi coding agent 0.73.1 — MIT; installed through npm with its dependency licenses.
- ripgrep 15.2.0 — MIT/Unlicense; fd 10.5.2 — MIT/Apache-2.0. Their archive
  license files are retained alongside the executables in `tools`.

Official Node, Git, ripgrep and fd archives have pinned SHA256 values in
`app/core/ai_installer.py`. npm verifies package integrity from the registry.

## bb

The native AI chat adapts Pi event translation and timeline presentation from
[bb](https://github.com/get-bb/bb), revision
`7794941da1b6e2baf017e5f7ab65b5f223fcaf54`.

Copyright (c) 2026 Michael Yong. Licensed under the MIT License.
The complete notice and permission text are preserved in [licenses/bb-MIT.txt](licenses/bb-MIT.txt).

Adapted source references:

- `plugins/provider-pi/src/delta-translation.ts`
- `packages/thread-view/src/assistant-stream-projection.ts`
- `apps/app/src/components/thread/timeline/ExpandableTimelineRow.tsx`
- `apps/app/src/components/thread/timeline/TimelineReasoningDetail.tsx`

The Python/PyQt adaptation is in `app/core/ai_timeline.py` and
`app/ui/ai_transcript.py`. bb's React application and server are not bundled.
