# Changelog

## 0.2.1 — 2026-10-12

A clean install previously resolved MCP SDK 2.3.0, then immediately crashed on the removed `mcp.server.fastmcp` import. This patch serves the same nine tools through SDK 2 and supported SDK 1.30, with typed annotations and useful error responses.

- Add a cross-platform Claude installer that builds an isolated `.venv`, validates imports outside the checkout, and writes an absolute-interpreter MCP entry. Existing servers are preserved; conflicting entries require an explicit replacement and get a backup.
- Add actual stdio tests for initialization, discovery, installed launcher, the nine-tool pipeline, error recovery, and H11 refusal. CI covers Linux/Windows, Python 3.10/3.12/3.13, SDK 1.30 and current SDK 2.
- Preserve and diagnose malformed input rather than emitting empty or silently changed programs. File reads require explicit `source_is_path`.
- Correct block/branch evaluation, edge-FB declarations, reset drafts, preset-register xref, SCL output names and XML attributes. Unsupported semantics remain explicit rather than guessed.
- Refuse emission on H11 detection, including preserved labels/comments and caller-selected output names. Original safety-labelled fixtures are kept; normal emission tests use a distinct synthetic process fixture.
- Include helpers, samples and review context in source distributions; verify installed-package behavior from a different working directory.

This release does not merge the pending library/AWL or project-bundle branches. Real GX-export coverage, vendor IDE import/compilation, and FAT remain unverified; generated artifacts are drafts.
