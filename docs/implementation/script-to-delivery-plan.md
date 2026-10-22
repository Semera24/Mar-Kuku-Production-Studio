# Saved script to delivery implementation

Authorized 2026-10-22. Work inline on `feat/script-to-video-delivery`; preserve production media and existing browser sessions. The user approved proceeding after backup, with joint hands-on testing afterward.

## Recovery points

- Baseline commit: d95a4d3.
- Local app/evidence archive: `/Users/yonassayfu/Projects/mar-kuku-production-studio-backups/studio-before-next-milestone-20261022.tar.gz` (110 MB; archive listing verified).
- Separate Git bundle at the same location, `.bundle` suffix; `git bundle verify` passed.
- GitHub origin is configured, main already exists. Both existing credentials and supplied token received HTTP 403 on push. Await corrected repository permissions; do not store the token in Git or source.

## Design and execution

1. Repair consultation acceptance, immutable revisions, durable submission checkpoints, stop semantics and per-episode identity. Empty or blocked provider replies never count as success. Ambiguous external submissions require reconciliation, never blind retry. Test these failure modes before adding production behavior.
2. Add an episode-neutral production service and strict structured chapter brief. Use explicit half-open frame ranges, approved script hashes, file provenance, real media probing and missing-asset errors. Reject dialogue inside a silent hold. Never compile prose with unresolved timing as if it were an approved specification.
3. Connect actual asset intake and voice generation. Preserve approved artwork. Accept explicitly registered image/audio sources, copy into versioned chapter folders, validate media and pin hashes. Offer local macOS speech as a labelled working voice option; browser/cloud provider capabilities must report unavailable rather than invent an audio/image result. Browser generation and download must produce actual files before marking ready.
4. Compile registered chapter assets through the existing scene compiler and Palmier bridge, save/reopen and semantically reconcile native projects, export MP4, fully decode and probe it. Reuse the existing writer lock; no mutation of unrelated active projects. Expose preparation/render/status through CLI and Studio chat/UI.
5. Add YouTube Studio delivery with metadata and explicit visibility. Persist upload intent and observed URL; do not repeat an uncertain upload. A private test upload is the first joint live acceptance, not public release.
6. Run regression and integration checks; save exact results, known external blockers and joint testing steps. Commit changes locally. Retry GitHub push once corrected access is supplied.

## Scope decisions

- Full episode assembly and unrestricted arbitrary-app GUI operations are later extensions of this chapter delivery path, not prerequisites for the first chapter.
- Use current approved local media for automated checks; do not spend generation credits or upload owner media merely to make tests green.
- A provider's text promising a file is not a generated asset; an upload button click is not verified publication.
- UI and CLI share the same services. Test fixtures remain labelled fixtures.

## Progress

- Backup complete; remote push blocked by repository authorization.
- Implementation in progress.
